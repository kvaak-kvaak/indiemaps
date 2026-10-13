#!/usr/bin/env python3
"""Harvest ABC station estate via embedded Next.js data (single polite fetch).

https://www.abcasemat.fi/asemat ships its full station list (names, brand
codes, addresses, postcodes, municipalities, coordinates, capabilities +
opening times) in __NEXT_DATA__. One GET, snapshot + SHA-256 metadata like
every other source. Whole estate: staffed stations, unmanned automats,
market/convenience units ride on the same records (brand codes).

  python3 fetch_abc.py --out /tmp/abc-snap
"""
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) indiemaps-pilot/1.0'}
URL = 'https://www.abcasemat.fi/asemat'
DAYS = {'MON': 'Mo', 'TUE': 'Tu', 'WED': 'We', 'THU': 'Th', 'FRI': 'Fr', 'SAT': 'Sa', 'SUN': 'Su'}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def opening_to_osm(caps):
    """Capability openingTimes -> OSM-syntax hours. 24H days and timed
    ranges only; anything else yields '' (never invented)."""
    parts = []
    for cap in caps or []:
        ot = cap.get('openingTimes') or {}
        for blk in ot.get('defaults', []):
            for d in blk.get('days', []):
                code = DAYS.get(d.get('day', ''))
                if not code:
                    continue
                if d.get('mode') == '24H':
                    parts.append(f'{code} 00:00-24:00')
                elif re.match(r'^\d{1,2}:\d{2}-\d{1,2}:\d{2}$', str(d.get('mode') or '')):
                    parts.append(f"{code} {d['mode']}")
    return '; '.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    if (outdir / 'abc.json').exists():
        raise SystemExit('snapshot exists; use a fresh --out directory')

    html = get(URL)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit('no NEXT_DATA on asemat page (layout changed?)')
    stations = json.loads(m.group(1))['props']['pageProps']['stations']
    records = []
    for s in stations:
        loc = ((s.get('locationData') or {}).get('location')) or {}
        addr = loc.get('address') or {}
        coords = loc.get('coordinates') or {}
        muni = (addr.get('municipality') or {}).get('name', {}) or {}
        try:
            lat = float(coords.get('lat')) if coords.get('lat') else None
            lng = float(coords.get('lon')) if coords.get('lon') else None
        except (TypeError, ValueError):
            lat = lng = None
        nm = ((s.get('locationData') or {}).get('name') or {}).get('default', '')
        records.append({
            'source': 'abc', 'id': f"abc:{s.get('clientId')}",
            'name': nm, 'brand': ((s.get('locationData') or {}).get('brand') or {}).get('code', ''),
            'lat': lat, 'lng': lng,
            'address': ((addr.get('street') or {}).get('default', '') or ''),
            'postcode': addr.get('postcode', '') or '',
            'city': muni.get('default', '') or '',
            'capabilities': [c.get('capability') for c in (s.get('mainCapabilities') or []) if c.get('capability')],
            'hours': opening_to_osm(s.get('mainCapabilities')),
        })
    data = json.dumps(records, ensure_ascii=False, indent=1).encode()
    (outdir / 'abc.json').write_bytes(data)
    (outdir / 'abc.meta.json').write_text(json.dumps({
        'source': 'abc', 'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(data).hexdigest(), 'rows': len(records),
        'origin': URL}, indent=2))
    with_coords = sum(1 for r in records if r['lat'] and r['lng'])
    print(f'saved {len(records)} ABC records ({with_coords} with coords)', flush=True)


if __name__ == '__main__':
    main()
