#!/usr/bin/env python3
"""Hazard-road detector (coastal instance): interpolation geocoding fails
on long, sparse, linearly-addressed roads (seafronts, esplanades), so pins
with no surveyed position on such roads get the bottom tier instead of a
confident render.

Method (no new data source beyond one cached coastline fetch):
- coastline geometry via Overpass (natural=coastline, cached per area —
  coastlines barely move),
- density proxy from pack addresses: distinct housenumbers per street key;
  <5 distinct = sparse (normal high streets score far higher),
- a food POI with no surveyed position (no 'osm' in sources) within
  COAST_M of the coastline on a sparse street -> position_hazard + the
  triggering road class recorded. Middle path: excluded from default
  map/list payloads (server filter), retained in search + by-id with
  distinct marking. Pins never moved, records never deleted.

Regression group (my measured seafront specimens; the planning thread's
~20-case Hundreds cluster to be confirmed as the same gate): kiosk rows
Kiosk 6/7/9, Olivers, Beach Hut FSA pin, Adventure Island units must flag;
town-centre controls (High Street, Rayleigh Road shops) must not.

  python3 hazard_roads.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, re, time, urllib.request
from pathlib import Path

UA = {'User-Agent': 'indiemaps-pack-builder/0.1 (+https://github.com/kvaak-kvaak/indiemaps)'}
COAST_M = 100
SPARSE_N = 5


def coast_geometry(bbox):
    w, s, e, n = bbox
    q = (f'[out:json][timeout:60];way["natural"="coastline"]'
         f'({s},{w},{n},{e});out geom;')
    last = None
    for ep in ('https://overpass-api.de/api/interpreter',
               'https://overpass.kumi.systems/api/interpreter'):
        try:
            req = urllib.request.Request(
                ep + '?data=' + urllib.parse.quote(q), headers=UA)
            d = json.load(urllib.request.urlopen(req, timeout=120))
            segs = []
            for el in d.get('elements', []):
                g = el.get('geometry') or []
                pts = [(p['lon'], p['lat']) for p in g
                       if p.get('lat') is not None]
                for a, b in zip(pts, pts[1:]):
                    segs.append((a, b))
            if segs:
                return segs
            last = 'no coastline in bbox'
        except Exception as ex:
            last = str(ex)[:100]
            time.sleep(10)
    print(f'coastline fetch: {last} — hazard detection skipped green')
    return []


import urllib.parse  # noqa: E402


def dist_pt_seg_m(lon, lat, a, b):
    import math
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = (bx - ax) * 62500, (by - ay) * 111320
    L2 = dx * dx + dy * dy
    if not L2:
        return math.hypot((lon - ax) * 62500, (lat - ay) * 111320)
    t = max(0.0, min(1.0, ((lon - ax) * 62500 * dx + (lat - ay) * 111320 * dy) / L2))
    return math.hypot((lon - (ax + t * dx / 62500)) * 62500,
                      (lat - (ay + t * dy / 111320)) * 111320)


def street_key(address):
    a = (address or '').strip()
    a = re.sub(r'^\s*\d+[a-z]?\s*[-–]?\s*', '', a, flags=re.I)
    first = a.split(',')[0].strip().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-zåäö ]', ' ', first)).strip() or None


def house_no(address):
    m = re.match(r'\s*(\d+[a-z]?)', address or '', re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r',\s*(\d+[a-z]?)\b', address or '')
    return m.group(1).lower() if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    bbox = list(map(float, a.bbox.split(',')))
    pois = json.load(open(a.pois))
    for p in pois:
        p.pop('position_hazard', None)
        p.pop('hazard_road', None)

    segs = coast_geometry(bbox)
    meta = json.load(open(a.meta))
    if not segs:
        meta['hazard_roads'] = {'skipped': 'no coastline geometry'}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        json.dump(pois, open(a.pois, 'w'), indent=1)
        return

    numbers = {}
    for p in pois:
        sk = street_key(p.get('address'))
        hn = house_no(p.get('address'))
        if sk and hn:
            numbers.setdefault(sk, set()).add(hn)

    flagged = []
    for p in pois:
        if p.get('category') not in ('restaurant', 'cafe', 'pub'):
            continue
        if 'osm' in (p.get('sources', []) or []):
            continue  # surveyed position: hierarchy already protected
        if p.get('lat') is None:
            continue
        d = min(dist_pt_seg_m(p['lng'], p['lat'], x, y) for x, y in segs)
        if d > COAST_M:
            continue
        sk = street_key(p.get('address'))
        density = len(numbers.get(sk, set())) if sk else 99
        if density >= SPARSE_N:
            continue
        p['position_hazard'] = True
        p['hazard_road'] = {'class': 'coastal', 'street': sk,
                            'coast_m': round(d), 'street_numbers': density}
        flagged.append((p['id'], p.get('name'), sk, round(d), density))
        print(f"hazard: {p.get('name')} [{p['id']}] street={sk!r} coast={round(d)}m numbers={density}")

    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta['hazard_roads'] = {'coast_m': COAST_M, 'sparse_n': SPARSE_N,
                            'segments': len(segs), 'flagged': len(flagged),
                            'members': [{'id': i, 'name': n} for i, n, _, _, _ in flagged]}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'hazard_roads: {len(flagged)} flagged ({len(segs)} coast segments)')


if __name__ == '__main__':
    main()
