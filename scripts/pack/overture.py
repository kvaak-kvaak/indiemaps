#!/usr/bin/env python3
"""Overture contact backfill: websites + phones for listings that lack them.

FSA carries no websites and unmapped OSM nodes carry no contact — but the same
business usually exists in Overture (FSQ/Meta-sourced, Microsoft excluded).
This stage fills ONLY empty website/phone fields, records overture_id, and
appends 'overture' to sources. Never overwrites surveyed data, never invents.

  python3 overture.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json

Requires: pip install duckdb. Keyless (anonymous S3).
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overture-join'))
from join import norm, nscore, dist_m, grid_index, nearby  # noqa

RELEASE = '2026-07-22.0'
S3BASE = f's3://overturemaps-us-west-2/release/{RELEASE}/theme=places/type=place/*.parquet'


def pull(w, s, e, n):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2'; SET http_timeout=30000;")
    rows = con.execute(f"""SELECT id, names.primary AS name,
      (bbox.xmin+bbox.xmax)/2 AS lon, (bbox.ymin+bbox.ymax)/2 AS lat,
      addresses[1].postcode AS postcode, websites[1] AS website, phones[1] AS phone
    FROM read_parquet('{S3BASE}')
    WHERE bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s}
      AND NOT list_contains(list_distinct([s2.dataset FOR s2 IN sources]), 'Microsoft')
      AND (len(websites) > 0 OR len(phones) > 0)
    """).fetchall()
    cols = ['id', 'name', 'lon', 'lat', 'postcode', 'website', 'phone']
    return [dict(zip(cols, r)) for r in rows]


def norm_pc(p):
    return (p or '').replace(' ', '').lower() or None


STOP = {'and', 'of', 'de', 'la', 's'}
GENERIC = {'southend', 'Leigh', 'westcliff', 'chalkwell', 'shoebury', 'shoeburyness',
           'thorpe', 'essex', 'london', 'high', 'street', 'road', 'avenue',
           'broadway', 'parade', 'town', 'centre', 'center', 'branch', 'store',
           'station', 'sea', 'old', 'new', 'north', 'south', 'east', 'west', 'on'}


def toks(s):
    return set(w for w in norm(s).split() if len(w) >= 3 and w not in STOP)


def accept(pname, oname, ns, d, pc):
    shared = (toks(pname) & toks(oname)) - GENERIC
    if norm(pname) == norm(oname) and len(norm(pname)) >= 6:
        return True  # exact same name
    ca, cb = norm(pname).replace(' ', ''), norm(oname).replace(' ', '')
    if min(len(ca), len(cb)) >= 8 and (ca in cb or cb in ca):
        return True  # spaceless variant ('RedChilliezs' vs 'Red Chilliezs')
    if not shared:
        return False  # only generic town words in common (e.g. 'Southend')
    return ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    t0 = time.time()
    ov = pull(w, s, e, n)
    print(f'overture with contact: {len(ov)} ({time.time()-t0:.0f}s)')
    grid, cell = grid_index(ov, 'lat', 'lon')

    pois = json.load(open(a.pois))
    filled_web, filled_phone = 0, 0
    for p in pois:
        # clear previous backfill (re-run safe); manual/ surveyed values stay
        if p.get('website_source') == 'overture':
            del p['website']; del p['website_source']
        if p.get('phone_source') == 'overture':
            del p['phone']; del p['phone_source']
        p.pop('overture_id', None)
        p['sources'] = [s for s in p.get('sources', []) if s != 'overture']
        if p.get('website') and p.get('phone'):
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs = None, 0
        for j in nearby(grid, cell, p['lat'], p['lng']):
            o = ov[j]
            d = dist_m(p['lat'], p['lng'], o['lat'], o['lon'])
            opc = norm_pc(o.get('postcode'))
            pc = bool(opc and ppc and opc == ppc)
            if d > (250 if pc else 120):
                continue
            ns = nscore(p['name'], o['name'] or '')
            if accept(p['name'], o['name'] or '', ns, d, pc):
                sc = ns + (0.3 if pc else 0)
                if sc > bs:
                    bs, best = sc, o
        if best is None:
            continue
        contributed = False
        if not p.get('website') and best.get('website'):
            p['website'] = best['website']
            p['website_source'] = 'overture'
            filled_web += 1
            contributed = True
        if not p.get('phone') and best.get('phone'):
            p['phone'] = best['phone']
            p['phone_source'] = 'overture'
            filled_phone += 1
            contributed = True
        if contributed:
            p['overture_id'] = best['id']
            if 'overture' not in p.get('sources', []):
                p['sources'].append('overture')
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['overture'] = {'release': RELEASE, 'contact_rows_in_bbox': len(ov),
                        'websites_filled': filled_web, 'phones_filled': filled_phone}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'backfilled websites={filled_web} phones={filled_phone}')


if __name__ == '__main__':
    main()
