#!/usr/bin/env python3
"""Finnish chain enrichment (Restel + Raflaamo snapshots, first-party data).

data/chains_fi/{restel,raflaamo}.json are one-off harvests (Oct 2026, metas
beside them) of chain-published restaurant records: surveyed positions,
phones, and (Restel, thin) hours. MATCH ONLY: rows that match nothing are
discarded, no POI is ever created — snapshots age, and creation from aging
data is exactly what this stage must not do.

Enrichment per match: chain_brand + chain_source, chain phone (fills
empties only, same rule as Overture backfill), chain hours converted to
OSM syntax (Restel day-dict; applied gap-only like ta_hours — current
sources always win by absence of competition). Matched records join
sources 'chain', which corroborates like ATP (chain-published position
agreement) for the orphan rule and keep-evidence.

Re-run safe (clears chain_* first). FI areas only (wired in build.py).

  python3 chains_fi.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, re
from pathlib import Path

from overture import toks, accept, nscore, dist_m, grid_index, nearby, norm_pc  # noqa

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {'restel': ROOT / 'data' / 'chains_fi' / 'restel.json',
           'raflaamo': ROOT / 'data' / 'chains_fi' / 'raflaamo.json'}
DAYMAP = {'monday': 'Mo', 'tuesday': 'Tu', 'wednesday': 'We', 'thursday': 'Th',
          'friday': 'Fr', 'saturday': 'Sa', 'sunday': 'Su'}


def restel_hours_to_osm(h):
    """Restel per-day strings ({"monday": "11-23", ...}) to OSM syntax.
    Empty/missing days are closed. Unparseable yields '' — never invented."""
    if not isinstance(h, dict):
        return ''
    parts = []
    for day, code in DAYMAP.items():
        v = (h.get(day) or '').strip()
        if not v:
            continue
        m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?$', v)
        if not m:
            continue
        o = f'{int(m.group(1)):02d}:{m.group(2) or "00"}'
        c = f'{int(m.group(3)):02d}:{m.group(4) or "00"}'
        parts.append(f'{code} {o}-{c}')
    return '; '.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    rows = []
    for src, path in SOURCES.items():
        try:
            data = json.load(open(path))
        except Exception as ex:
            print(f'chains_fi: {src} unreadable ({ex}), skipped')
            continue
        for r in data:
            lat, lng = r.get('lat'), r.get('lng')
            if lat is None or lng is None:
                continue
            if not (s <= lat <= n and w <= lng <= e):
                continue
            if not r.get('name'):
                continue
            rows.append({'src': src, 'name': r['name'], 'lat': lat, 'lng': lng,
                         'phone': r.get('phone') or '',
                         'postcode': (r.get('postcode') or '').replace(' ', '').lower() or None,
                         'hours': r.get('hours') if src == 'restel' else None})
    print(f'chains_fi rows in bbox: {len(rows)}')

    pois = json.load(open(a.pois))
    for p in pois:
        for k in ('chain_brand', 'chain_source', 'chain_hours'):
            p.pop(k, None)
        p['sources'] = [x for x in p.get('sources', []) if x != 'chain']

    grid, cell = grid_index(rows, 'lat', 'lng') if rows else ({}, None)
    matched = hours_added = phones_added = 0
    for p in pois:
        if p.get('category') not in ('restaurant', 'cafe', 'pub'):
            continue
        if not p.get('name'):
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs, bsrc = None, 0, ''
        if rows:
            for j in nearby(grid, cell, p['lat'], p['lng']):
                o = rows[j]
                d = dist_m(p['lat'], p['lng'], o['lat'], o['lng'])
                if d > 150:
                    continue
                rpc = o.get('postcode')
                pc = bool(rpc and ppc and rpc == ppc)
                ns = nscore(p['name'], o['name'])
                if accept(p['name'], o['name'], ns, d, pc):
                    sc = ns + (0.3 if pc else 0)
                    if sc > bs:
                        bs, best, bsrc = sc, o, o['src']
        if best is None:
            continue
        matched += 1
        p['chain_brand'] = best['name']
        p['chain_source'] = bsrc
        if best.get('phone') and not p.get('phone'):
            p['phone'] = best['phone']
            p['phone_source'] = 'chain'
            phones_added += 1
        if best.get('hours') and not (p.get('opening_hours_osm') or p.get('atp_hours')
                                      or p.get('site_hours') or p.get('nhs_hours')
                                      or p.get('sm_hours')):
            h = restel_hours_to_osm(best['hours'])
            if h:
                p['chain_hours'] = h
                hours_added += 1
        if 'chain' not in p.get('sources', []):
            p['sources'].append('chain')
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['chains_fi'] = {'rows_in_bbox': len(rows), 'matched': matched,
                         'hours_added': hours_added, 'phones_added': phones_added}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'chains_fi: {len(rows)} rows -> {matched} matched, +{hours_added} hours, +{phones_added} phones')


if __name__ == '__main__':
    main()
