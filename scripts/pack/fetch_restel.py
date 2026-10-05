#!/usr/bin/env python3
"""Fetch Restel operated restaurants via their public WordPress REST API.

Polite by construction: one page at a time, short sleeps, single pass,
proper User-Agent. Snapshot + SHA-256 metadata like every other source.
Output: list of {source, id, name, lat, lng, address, postcode, city,
phone, hours, store_id, url, fetched fields raw}.

  python3 fetch_restel.py --out /tmp/restel-snapshot
"""
import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {'User-Agent': 'indiemaps-pilot/1.0 (+https://github.com/kvaak-kvaak/indiemaps)'}
BASE = 'https://www.restel.fi/wp-json/wp/v2/restaurants'


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.headers, json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True,
                    help='output directory (snapshot + meta written here)')
    ap.add_argument('--per-page', type=int, default=100)
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    if (outdir / 'restel.json').exists():
        raise SystemExit('snapshot exists; use a fresh --out directory')

    page, records, total_pages = 1, [], None
    while True:
        url = f'{BASE}?per_page={a.per_page}&page={page}&_fields=id,slug,link,modified,title,acf'
        try:
            headers, batch = get(url)
        except Exception as e:
            raise SystemExit(f'page {page} failed: {str(e)[:120]}')
        if total_pages is None:
            try:
                total_pages = int(headers.get('X-WP-TotalPages', 1))
            except (TypeError, ValueError):
                total_pages = 1
            print(f'{total_pages} pages expected', flush=True)
        if not batch:
            break
        for r in batch:
            t, acf = r.get('title', {}), r.get('acf', {})
            records.append({
                'source': 'restel',
                'id': f"restel:{r.get('id')}",
                'name': (t.get('rendered') or '').strip(),
                'lat': acf.get('location_latitude'),
                'lng': acf.get('location_longitude'),
                'address': (acf.get('address') or '').strip() or None,
                'postcode': (acf.get('zip_code') or '').strip() or None,
                'city': (acf.get('city') or '').strip() or None,
                'phone': (acf.get('phone') or '').strip() or None,
                'hours': acf.get('visiting_hours') or None,
                'store_id': acf.get('store_id'),
                'url': r.get('link'),
                'modified': r.get('modified'),
            })
        print(f'page {page}/{total_pages}: {len(records)} total', flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(3)
    blob = json.dumps(records, ensure_ascii=False, indent=1).encode()
    (outdir / 'restel.json').write_bytes(blob)
    (outdir / 'restel.meta.json').write_text(json.dumps({
        'source': 'restel', 'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(blob).hexdigest(), 'rows': len(records),
        'pages': total_pages, 'origin': 'https://www.restel.fi/wp-json/wp/v2/restaurants',
    }, indent=2))
    print(f'saved {len(records)} Restel records')


if __name__ == '__main__':
    main()
