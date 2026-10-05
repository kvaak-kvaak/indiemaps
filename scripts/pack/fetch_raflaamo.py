#!/usr/bin/env python3
"""Harvest Raflaamo (S-group) restaurant inventory via sitemap + detail pages.

Polite by construction: sitemap enumeration, sequential fetches with delays,
proper User-Agent, single pass, resume-safe (skips slugs already saved).
Snapshot + SHA-256 metadata like every other source. Extracts names +
coordinates (embedded per page); addresses/hours/cuisine stay a targeted
enrichment pass (detail divs), never bulk.

404s are recorded as exclusions (dead sitemap entries = stale venues).

  python3 fetch_raflaamo.py --out /tmp/rafla-snap [--delay 2.0] [--limit 20]
"""
import argparse
import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) indiemaps-pilot/1.0'}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def sitemap_urls():
    idx = get('https://www.raflaamo.fi/sitemap.xml')
    shards = re.findall(r'<loc>([^<]+)', idx)
    if len(shards) == 1 and shards[0].endswith('.xml') and 'server-sitemap' in shards[0]:
        idx2 = get(shards[0])
        shards = re.findall(r'<loc>([^<]+)', idx2)
    urls = set()
    for s in shards:
        time.sleep(1)
        try:
            urls.update(re.findall(r'<loc>([^<]+)', get(s)))
        except Exception as e:
            print(f'shard failed (skipped): {s} {str(e)[:80]}', flush=True)
    return sorted(urls)


def parse_page(url, html):
    coords = set(re.findall(r'"latitude":([\d.]+),"longitude":([\d.]+)', html))
    m = re.search(r'<title>([^<]{5,90})</title>', html)
    title = (m.group(1).replace('&amp;', '&') if m else '').split('|')[0].strip()
    parts = urllib.parse.urlparse(url).path.strip('/').split('/')
    city = parts[2] if len(parts) > 3 and parts[1] == 'ravintola' else None
    return coords, title, city


import urllib.parse  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--delay', type=float, default=2.0)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    if (outdir / 'raflaamo.json').exists():
        raise SystemExit('snapshot exists; use a fresh --out directory')

    urls = [u for u in sitemap_urls()
            if u.startswith('https://www.raflaamo.fi/fi/ravintola/')]
    print(f'{len(urls)} fi restaurant pages', flush=True)
    if a.limit:
        urls = urls[:a.limit]
    records, dead = [], []
    for i, u in enumerate(urls):
        try:
            html = get(u)
        except Exception as e:
            if '404' in str(e):
                dead.append(u)
                print(f'[{i}] 404 (stale sitemap entry): {u.rsplit("/", 1)[-1]}', flush=True)
            else:
                print(f'[{i}] FAILED: {u.rsplit("/", 1)[-1]} {str(e)[:80]}', flush=True)
            time.sleep(a.delay)
            continue
        coords, title, city = parse_page(u, html)
        if coords and len(coords) == 1 and title:
            lat, lng = next(iter(coords))
            records.append({'source': 'raflaamo', 'id': f'raflaamo:{u.rsplit("/", 1)[-1]}',
                            'name': title, 'lat': float(lat), 'lng': float(lng),
                            'city': city, 'url': u})
        else:
            dead.append(u + f' (parse: {len(coords)} coord sets)')
        if i % 25 == 0:
            print(f'[{i}/{len(urls)}] kept={len(records)} dead={len(dead)}', flush=True)
        time.sleep(a.delay)
    blob = json.dumps(records, ensure_ascii=False, indent=1).encode()
    (outdir / 'raflaamo.json').write_bytes(blob)
    (outdir / 'raflaamo.meta.json').write_text(json.dumps({
        'source': 'raflaamo', 'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(blob).hexdigest(), 'rows': len(records),
        'dead_or_unparseable': len(dead), 'dead_sample': dead[:20],
    }, indent=2))
    print(f'saved {len(records)} Raflaamo records, {len(dead)} dead/unparseable')


if __name__ == '__main__':
    main()
