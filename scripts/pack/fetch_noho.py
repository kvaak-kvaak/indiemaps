#!/usr/bin/env python3
"""Harvest NoHo Partners venue inventory via city location pages + details.

STATUS 2026-10-13: PARKED — city pages and /liiketoiminta/ravintolat/
carry corporate nav only (no venue inventory); venues live on brand
domains. Restel (NoHo-owned, 142 records) is already wired via
chains_fi. Revisit only if NoHo publishes a machine-readable list.
Kept as the documented pattern for that day; produces nothing now.


  python3 fetch_noho.py --out /tmp/noho-snap [--delay 2.0]
"""
import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) indiemaps-pilot/1.0'}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-zåäö ]', ' ', (s or '').lower())).strip()


def cities():
    idx = get('https://www.noho.fi/no_location-sitemap.xml')
    return sorted({u.rsplit('/', 2)[-2] for u in re.findall(r'<loc>([^<]+)', idx)
                   if '/blog/no_location/' in u and '/en/' not in u})


def parse_detail(url, html):
    coords = set(re.findall(r'"latitude":([\d.]+),"longitude":([\d.]+)', html))
    coords |= set(re.findall(r'data-lat="([\d.]+)"[^>]+data-lng="([\d.]+)"', html))
    m = re.search(r'<title>([^<]{3,90})</title>', html)
    title = (m.group(1).replace('&amp;', '&') if m else '').split('|')[0].strip()
    addr = None
    m2 = re.search(r'(?:address|osoite|katu)[^<>]{0,40}?([\wåäöÅÄÖ .\-]{4,60} \d{1,5}[a-z]?(?:,|\s)\s*\d{5}\s+[\wåäöÅÄÖ .\-]+)', html)
    if m2:
        addr = m2.group(1).strip()
    return coords, title, addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--delay', type=float, default=2.0)
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    if (outdir / 'noho.json').exists():
        raise SystemExit('snapshot exists; use a fresh --out directory')

    venues = {}
    for c in cities():
        time.sleep(a.delay)
        try:
            h = get(f'https://www.noho.fi/blog/no_location/{c}/')
        except Exception as e:
            print(f'{c}: city page failed ({str(e)[:60]})', flush=True)
            continue
        links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{3,80})</a>', h)
        for u, t in links:
            if '/blog/no_location/' in u or not norm(t):
                continue
            full = urllib.parse.urljoin(f'https://www.noho.fi/blog/no_location/{c}/', u)
            venues.setdefault(full, {'name': t.strip(), 'city': c})
        print(f'{c}: {len(links)} links', flush=True)
    print(f'{len(venues)} venue pages', flush=True)

    records, dead = [], []
    for i, (u, v) in enumerate(sorted(venues.items())):
        time.sleep(a.delay)
        try:
            html = get(u)
        except Exception as e:
            if '404' in str(e):
                dead.append(u)
            else:
                print(f'{u} failed ({str(e)[:60]})', flush=True)
            continue
        coords, title, addr = parse_detail(u, html)
        lat = lng = None
        if coords:
            la, ln = sorted(coords)[0]
            lat, lng = float(la), float(ln)
        records.append({'source': 'noho', 'id': f"noho:{urllib.parse.quote(u.rsplit('/', 2)[-2] or u[-40:], safe='')}",
                        'name': v['name'], 'page_title': title, 'city': v['city'],
                        'lat': lat, 'lng': lng, 'address': addr, 'url': u})
        if (i + 1) % 20 == 0:
            print(f'{i + 1}/{len(venues)}', flush=True)
    data = json.dumps(records, ensure_ascii=False, indent=1).encode()
    (outdir / 'noho.json').write_bytes(data)
    (outdir / 'noho.meta.json').write_text(json.dumps({
        'source': 'noho', 'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(data).hexdigest(), 'rows': len(records),
        'dead_or_unparseable': len(dead), 'dead_sample': dead[:20],
        'origin': 'https://www.noho.fi/no_location-sitemap.xml'}, indent=2))
    print(f'saved {len(records)} NoHo records ({len(dead)} dead)', flush=True)


if __name__ == '__main__':
    main()
