#!/usr/bin/env python3
"""NHS pharmacy layer (England only): authoritative list + per-day hours.

  python3 nhs.py --update-cache          # quarterly: download CSV, geocode postcodes
  python3 nhs.py --bbox=W,S,E,N --pois <pois.json> --meta <meta.json>

Source: NHSBSA Consolidated Pharmaceutical List (quarterly CSV, OGL 3.0) —
ODS code, trading name, address, postcode, opening hours Mon..Sun, contract
type. No coordinates in file: postcode centroids via postcodes.io (bulk).
Distance sellers (no visitable premises) are excluded like FSA mobile caterers.

Merge: match existing POIs (name + postcode, else proximity) -> attach
nhs_hours + nhs_ods; unmatched in-bbox rows are ADDED as source:nhs POIs.
"""
import argparse, csv, json, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'data' / 'nhs' / 'cache.json'
DAYS = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY']
OSM_DAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
SKIP_NAME = re.compile(r'digital|online|internet|mail.?order|distance.?sell', re.I)
UA = {'User-Agent': 'yellowpages-pack-builder/0.1'}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout)


def latest_resource():
    d = json.load(get('https://opendata.nhsbsa.net/api/3/action/package_show?id=consolidated-pharmaceutical-list'))
    csvs = [r for r in d['result']['resources'] if r['format'].upper() == 'CSV']
    return sorted(csvs, key=lambda r: r.get('created', ''))[-1]['url']


def norm(s):
    return re.sub(r'[^a-z0-9 ]', ' ', (s or '').lower())


def toks(s):
    return set(w for w in norm(s).split() if len(w) > 2)


def dist_m(a, b, c, d):
    return (((a - c) * 111320) ** 2 + ((b - d) * 62500) ** 2) ** 0.5


def clean_hours(raw):
    """'09:00-19:00' kept; 'CLOSED'/empty dropped; multi-ranges preserved."""
    if not raw:
        return ''
    parts = [p.strip() for p in raw.split(',')]
    keep = [p for p in parts if re.match(r'^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$', p)]
    return ','.join(keep)


def update_cache():
    import urllib.request as u
    url = latest_resource()
    print('downloading', url[:90], flush=True)
    data = get(url, timeout=300).read().decode('utf-8-sig')
    rows = list(csv.DictReader(data.splitlines()))
    print(f'{len(rows)} national rows', flush=True)
    recs = []
    for r in rows:
        if SKIP_NAME.search(r.get('PHARMACY_TRADING_NAME') or ''):
            continue
        addr = ' '.join([(r.get(f'ADDRESS_FIELD_{i}') or '').strip() for i in (1, 2, 3, 4)])
        addr = re.sub(r'\s+', ' ', addr).strip(' ,')
        hours = {}
        for od, nd in zip(DAYS, OSM_DAYS):
            h = clean_hours(r.get(f'PHARMACY_OPENING_HOURS_{od}') or '')
            if h:
                hours[nd] = h
        recs.append({'ods': r.get('PHARMACY_ODS_CODE_F_CODE'),
                     'name': (r.get('PHARMACY_TRADING_NAME') or '').strip(),
                     'org': (r.get('ORGANISATION_NAME') or '').strip(),
                     'address': addr, 'postcode': (r.get('POST_CODE') or '').strip() or None,
                     'hours': hours, 'contract': r.get('CONTRACT_TYPE')})
    # bulk geocode postcodes (100/request)
    pcs = sorted({r['postcode'] for r in recs if r['postcode']})
    geo = {}
    for i in range(0, len(pcs), 100):
        batch = pcs[i:i + 100]
        req = u.Request('https://api.postcodes.io/postcodes',
                        data=json.dumps({'postcodes': batch}).encode(),
                        headers={'Content-Type': 'application/json', **UA})
        try:
            j = json.load(u.urlopen(req, timeout=60))
            for q, res in zip(batch, j['result']):
                if res and res.get('result'):
                    geo[q] = (res['result']['latitude'], res['result']['longitude'])
        except Exception as e:
            print('geocode batch failed:', str(e)[:80])
        time.sleep(0.3)
    kept = 0
    for r in recs:
        g = geo.get(r['postcode'] or '')
        if g:
            r['lat'], r['lng'] = g
            kept += 1
    print(f'geocoded {kept}/{len(recs)}', flush=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    json.dump({'updated': time.strftime('%Y-%m-%d'), 'count': len(recs),
               'rows': [r for r in recs if 'lat' in r]}, open(CACHE, 'w'))
    print('cache:', CACHE)


def merge(bbox, pois_path, meta_path):
    if not CACHE.exists():
        sys.exit('NHS cache missing — run: python3 scripts/pack/nhs.py --update-cache')
    w, s, e, n = bbox
    cache = json.load(open(CACHE))
    rows = [r for r in cache['rows']
            if r.get('lat') is not None and w <= r['lng'] <= e and s <= r['lat'] <= n]
    print(f'NHS rows in bbox: {len(rows)} (cache {cache["updated"]})', flush=True)
    pois = json.load(open(pois_path))
    matched = added = 0
    used = set()
    # Match against a snapshot: appended NHS records must never become match
    # targets mid-loop (same-name branches would collapse onto each other).
    base_pois = list(pois)
    for r in rows:
        hours = '; '.join(f'{d} {r["hours"][d]}' for d in OSM_DAYS if d in r['hours'])
        best, bs = None, 0
        rpc = (r['postcode'] or '').replace(' ', '').lower()
        for p in base_pois:
            if id(p) in used:
                continue
            if p.get('category') not in ('health', 'shopping', 'services'):
                continue
            d = dist_m(p['lat'], p['lng'], r['lat'], r['lng'])
            if d > 150:
                continue
            ta, tb = toks(r['name']), toks(p['name'])
            if not ta or not tb:
                continue
            ns = len(ta & tb) / max(len(ta), len(tb))
            ppc = (p.get('postcode') or '').replace(' ', '').lower()
            pc = bool(ppc and rpc and ppc == rpc)
            if ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc)):
                if ns + (0.3 if pc else 0) > bs:
                    bs, best = ns + (0.3 if pc else 0), p
        if best is not None:
            matched += 1
            used.add(id(best))
            best['nhs_ods'] = r['ods']
            # Match diagnostics for the per-source debug UI (set-once,
            # mirroring the non-clearing pattern of the keys below).
            if 'nhs_match' not in best:
                best['nhs_match'] = {'dist_m': round(dist_m(best['lat'], best['lng'], r['lat'], r['lng']))}
            if hours and not best.get('nhs_hours'):
                best['nhs_hours'] = hours
            if 'nhs' not in best.get('sources', []):
                best['sources'].append('nhs')
        else:
            added += 1
            addr = r['address'] + (f", {r['postcode']}" if r['postcode'] else '')
            pois.append({
                'id': f"nhs-{r['ods']}", 'nhs_ods': r['ods'],
                'fsa_id': None, 'osm_type': None, 'osm_id': None,
                'name': r['name'], 'category': 'health', 'category_label': 'Pharmacy',
                'lat': r['lat'], 'lng': r['lng'], 'geo_precision': 'postcode',
                'address': addr, 'postcode': r['postcode'],
                'phone': '', 'email': '', 'website': '',
                'facebook': '', 'instagram': '', 'twitter': '',
                'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
                'nhs_hours': hours, 'amenities': [], 'photos': [],
                'description': '', 'sources': ['nhs'],
            })
    json.dump(pois, open(pois_path, 'w'), indent=1)
    meta = json.load(open(meta_path))
    meta['nhs'] = {'cache_updated': cache['updated'], 'rows_in_bbox': len(rows),
                   'matched': matched, 'added': added}
    json.dump(meta, open(meta_path, 'w'), indent=1)
    print(f'nhs: matched={matched} added={added}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--update-cache', action='store_true')
    ap.add_argument('--bbox')
    ap.add_argument('--pois')
    ap.add_argument('--meta')
    a = ap.parse_args()
    if a.update_cache:
        return update_cache()
    if not (a.bbox and a.pois and a.meta):
        sys.exit('need --bbox W,S,E,N --pois PATH --meta PATH (or --update-cache)')
    merge(list(map(float, a.bbox.split(','))), a.pois, a.meta)


if __name__ == '__main__':
    main()
