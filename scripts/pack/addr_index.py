#!/usr/bin/env python3
"""OSM address index (lightweight pull, no amenity filter).

FSA/CH rows carry structured addresses (housenumber + street) but batch
coordinates. OSM carries ~3k surveyed address points per town bbox
(addr:housenumber / addr:housename on nodes and ways). This stage pulls
them into a pack-local index so resolve_positions can place pins by
string equality — lookup, never estimation.

  python3 addr_index.py --bbox=W,S,E,N --out packs/<id>/addr_index.json
"""
import argparse, json, time, urllib.request, urllib.parse


UA = {'User-Agent': 'indiemaps-pack-builder/0.1 (+https://github.com/kvaak-kvaak/indiemaps)'}


def fetch(q):
    last = None
    for ep in ('https://overpass-api.de/api/interpreter',
               'https://overpass.kumi.systems/api/interpreter'):
        for _ in range(2):
            try:
                url = ep + '?data=' + urllib.parse.quote(q)
                req = urllib.request.Request(url, headers=UA)
                return json.load(urllib.request.urlopen(req, timeout=150))
            except Exception as e:
                last = str(e)[:100]
                time.sleep(10)
    raise RuntimeError(f'address index pull failed: {last}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    w, s, e, n = a.bbox.split(',')
    q = (f'[out:json][timeout:120];(node["addr:housenumber"]({s},{w},{n},{e});'
         f'node["addr:housename"]({s},{w},{n},{e});'
         f'way["addr:housenumber"]({s},{w},{n},{e});'
         f'way["addr:housename"]({s},{w},{n},{e}););out tags center;')
    d = fetch(q)
    out = []
    for el in d.get('elements', []):
        t = el.get('tags', {})
        c = el.get('center', {})
        lat = el.get('lat', c.get('lat'))
        lon = el.get('lon', c.get('lon'))
        if lat is None or lon is None:
            continue
        out.append({'type': el.get('type'), 'id': el.get('id'),
                    'lat': lat, 'lng': lon,
                    'housenumber': (t.get('addr:housenumber') or '').strip() or None,
                    'housename': (t.get('addr:housename') or '').strip() or None,
                    'street': (t.get('addr:street') or '').strip() or None,
                    'postcode': (t.get('addr:postcode') or '').strip() or None})
    named = sum(1 for o in out if o['housenumber'] or o['housename'])
    if not out:
        raise SystemExit('address index empty — aborting (never silently degrade)')
    json.dump(out, open(a.out, 'w'))
    print(f'addr_index: {len(out)} objects ({named} with number/name)')


if __name__ == '__main__':
    main()
