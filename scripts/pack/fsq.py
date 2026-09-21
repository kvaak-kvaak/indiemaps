#!/usr/bin/env python3
"""Foursquare OS Places layer (direct dataset, not Overture's FSQ slice).

Overture's FSQ-derived rows proved to be a thin shadow (~34 rows where the
direct dataset holds ~600 in the same box), so FSQ-OS is pulled directly
from HuggingFace (hf://, bbox pushdown — laptop-safe, ~1 min for a town
box). Apache-2.0 licensed.

Two jobs, both conservative:
1. Match-only corroboration: attach fsq_id + diagnostics, fill empty phone
   fields (phone_source 'fsq'), append 'fsq' to sources. Never overwrites.
2. Phase-2 gated creation: unmatched food rows may seed a POI ONLY via a
   fresh-FSA record with no pack POI (match_unlocatable), AND only when
   date_closed is unset (a closed venue corroborates nothing). Provisional
   'fsq_new' tag for audit.

Token: HF_TOKEN env (repo secret in CI; user-supplied, never committed).
Without it the stage skips green with a meta note.

  python3 fsq.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overture import (toks, accept, nscore, dist_m, grid_index, nearby,  # noqa
                      norm_pc, match_unlocatable, FOOD_CATS as _OV_FOOD)

ROOT = Path(__file__).resolve().parents[2]
FSQ_RELEASE = 'dt=2026-08-11'
FSQ_LEVEL2_RESTAURANTS = '4d4b7105d754a06374d81259'
BASE = f'hf://datasets/foursquare/fsq-os-places/release/{FSQ_RELEASE}/places/parquet/*.parquet'
CAT = f'hf://datasets/foursquare/fsq-os-places/release/{FSQ_RELEASE}/categories/parquet/*.parquet'


def match_names(poi):
    names = [poi.get('name'), poi.get('fsa_name'),
             (poi.get('alias') or {}).get('registered')]
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    token = os.environ.get('HF_TOKEN', '')
    if not token:
        print('fsq: no HF_TOKEN, skipping stage green')
        meta = json.load(open(a.meta))
        meta['fsq'] = {'skipped': 'HF_TOKEN absent (repo secret, user-supplied)'}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        return
    import duckdb
    con = duckdb.connect()
    con.execute('INSTALL httpfs; LOAD httpfs;')
    con.execute(f"CREATE SECRET hf_token (TYPE huggingface, TOKEN '{token}')")
    con.execute('PRAGMA threads=2')
    w, s, e, n = map(float, a.bbox.split(','))
    food_ids = [r[0] for r in con.execute(
        f"SELECT category_id FROM '{CAT}' "
        f"WHERE level2_category_id = '{FSQ_LEVEL2_RESTAURANTS}'").fetchall()]
    lit = '[' + ','.join(f"'{i}'" for i in food_ids) + ']'
    rows = con.execute(f"""SELECT fsq_place_id, name, latitude, longitude,
        address, postcode, tel, date_created, date_refreshed, date_closed
        FROM '{BASE}'
        WHERE longitude BETWEEN {w} AND {e} AND latitude BETWEEN {s} AND {n}
          AND len(list_intersect(fsq_category_ids, {lit})) > 0""").fetchall()
    cols = ['id', 'name', 'lat', 'lng', 'addr', 'pc', 'phone',
            'created', 'refreshed', 'closed']
    rows = [dict(zip(cols, r)) for r in rows if r[1] and r[2] is not None and r[3] is not None]
    print(f'fsq-os food rows in bbox: {len(rows)} (release {FSQ_RELEASE})')

    pois = json.load(open(a.pois))
    for p in pois:
        p.pop('fsq_id', None)
        p.pop('fsq_match', None)
        p['sources'] = [x for x in p.get('sources', []) if x != 'fsq']
    grid, cell = grid_index(rows, 'lat', 'lng') if rows else ({}, None)
    matched = phones = created = 0
    used = set()
    for p in pois:
        if p.get('category') not in ('restaurant', 'cafe', 'pub'):
            continue
        names = match_names(p)
        if not names:
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs, best_j = None, 0, None
        if rows:
            for j in nearby(grid, cell, p['lat'], p['lng']):
                o = rows[j]
                d = dist_m(p['lat'], p['lng'], o['lat'], o['lng'])
                if d > 150:
                    continue
                rpc = norm_pc(o.get('pc'))
                pc = bool(rpc and ppc and rpc == ppc)
                for nm in names:
                    ns = nscore(nm, o['name'] or '')
                    if accept(nm, o['name'] or '', ns, d, pc):
                        sc = ns + (0.3 if pc else 0)
                        if sc > bs:
                            bs, best, best_j = sc, o, j
        if best is None:
            continue
        used.add(best_j)
        matched += 1
        p['fsq_id'] = best['id']
        p['fsq_match'] = {'name': best.get('name'),
                         'dist_m': round(dist_m(p['lat'], p['lng'], best['lat'], best['lng']))}
        if not p.get('phone') and best.get('phone'):
            p['phone'] = best['phone']
            p['phone_source'] = 'fsq'
            phones += 1
        if 'fsq' not in p.get('sources', []):
            p['sources'].append('fsq')
    if rows:
        meta_pre = json.load(open(a.meta))
        unloc = (meta_pre.get('fsa_resweep') or {}).get('unlocatable', [])
        have_fsa = {p.get('fsa_id') for p in pois if p.get('fsa_id')}
        for j, o in enumerate(rows):
            if j in used or o.get('closed'):
                continue  # closed venues corroborate nothing
            f, sc = match_unlocatable(o.get('name'), norm_pc(o.get('pc')), unloc)
            if f is None or f.get('fhrs_id') in have_fsa:
                continue
            created += 1
            have_fsa.add(f['fhrs_id'])
            pois.append({
                'id': f"fsa-{f['fhrs_id']}", 'fsa_id': f['fhrs_id'],
                'osm_type': None, 'osm_id': None, 'name': f['name'],
                'category': 'restaurant', 'category_label': 'Restaurant / Café',
                'lat': o['lat'], 'lng': o['lng'], 'geo_precision': 'fsq',
                'address': (o.get('addr') or '') +
                           (f", {o.get('pc')}" if o.get('pc') else ''),
                'postcode': o.get('pc'),
                'phone': o.get('phone') or '', 'email': '', 'website': '',
                'facebook': '', 'instagram': '', 'twitter': '',
                'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
                'brand_wikidata': None, 'wikipedia': None, 'wikidata': None,
                'amenities': [], 'photos': [], 'description': '',
                'sources': ['fsq'], 'provisional_creation': 'fsq_new',
                'fsq_id': o['id'],
                **({'fsa_rating_date': f['ratingDate']} if f.get('ratingDate') else {}),
            })
            print(f"fsq-created: {f['name']} [{f['fhrs_id']}] via {o.get('name')} (sc={sc})")
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['fsq'] = {'release': FSQ_RELEASE, 'rows_in_bbox': len(rows),
                   'matched': matched, 'phones_filled': phones,
                   'created': created}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'fsq-os: {len(rows)} rows -> {matched} matched, +{phones} phones, {created} created')


if __name__ == '__main__':
    main()
