#!/usr/bin/env python3
"""NoHo verification helper (targeted, never bulk).

Given a venue name (+ optional city), checks NoHo's city location pages
for a matching venue and returns evidence (matched name, city page URL,
detail URL when linked). Feeds the corroboration queue; never imports.

  python3 verify_noho.py --name "Nokka" [--city helsinki]
"""
import argparse
import json
import re
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) indiemaps-pilot/1.0'}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-zåäö ]', ' ', (s or '').lower())).strip()


def verify(name, city=None):
    cities = [city] if city else None
    if cities is None:
        idx = get('https://www.noho.fi/no_location-sitemap.xml')
        cities = sorted({u.rsplit('/', 2)[-2] for u in re.findall(r'<loc>([^<]+)', idx)
                         if '/blog/no_location/' in u and '/en/' not in u})
    target = norm(name)
    for c in cities:
        try:
            h = get(f'https://www.noho.fi/blog/no_location/{c}/')
        except Exception as e:
            print(f'{c}: fetch failed ({str(e)[:60]})')
            continue
        heads = re.findall(r'<h[23][^>]*>([^<]{3,80})</h[23]>', h)
        links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{3,80})</a>', h)
        for hh in heads + [t for _, t in links]:
            if norm(hh) and (norm(hh) == target or target in norm(hh) or norm(hh) in target):
                detail = next((u for u, t in links if norm(t) == norm(hh)), None)
                return {'verified': True, 'matched_name': hh.strip(),
                        'city': c, 'city_url': f'https://www.noho.fi/blog/no_location/{c}/',
                        'detail_url': detail}
    return {'verified': False, 'name': name, 'city': city}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--city', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    r = verify(a.name, a.city)
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else r)


if __name__ == '__main__':
    main()
