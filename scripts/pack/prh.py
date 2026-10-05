#!/usr/bin/env python3
"""PRH/YTJ adapter: Finnish Trade Register open data (free, keyless, CC BY 4.0,
updated daily). Finland's CH-equivalent with documented gaps: no sole traders
(`toiminimi` excluded upstream), no phones/emails, street addresses on only
~28% of records.

Two access modes (same pattern as the CH bulk workflow):
- targeted search (corroboration queries): `search_companies(name, ...)`
- bulk sweep (discovery): `fetch_bulk(outdir)` downloads /all_companies.

Normalized company dict schema (what rules consume):
  {company_number, company_name, names_all, status, registered_address,
   postcode, municipality, business_lines, registration_date}
status is canonical: 'registered' | 'ceased' | 'unknown'.
"""
import argparse
import hashlib
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

UA = {'User-Agent': 'indiemaps-pilot/1.0 (+https://github.com/kvaak-kvaak/indiemaps)'}
BASE = 'https://avoindata.prh.fi/opendata-ytj-api/v3'
# NACE section I (accommodation + food service), TOIMI2 coding.
FOOD_PREFIXES = ('55', '56')


def get(url, timeout=90):
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _descriptions(entry):
    out = []
    for d in entry.get('descriptions', []) or []:
        if isinstance(d, dict):
            out.append((d.get('description') or '').lower())
        else:
            out.append(str(d).lower())
    return out


def canonical_status(company):
    """Latest registered entry wins; keyword match across all three
    languages, type codes as fallback (1=registered, 4=ceased per docs)."""
    entries = sorted(company.get('registeredEntries', []) or [],
                     key=lambda e: e.get('registrationDate') or '', reverse=True)
    texts = []
    for e in entries:
        texts.extend(_descriptions(e))
    blob = ' '.join(texts)
    if any(k in blob for k in ('lakannut', 'upphört', 'ceased', 'lopettanut')):
        return 'ceased'
    if any(k in blob for k in ('rekisterissä', 'registrerad', 'registered')):
        return 'registered'
    types = [str(e.get('type')) for e in entries]
    if '4' in types:
        return 'ceased'
    if '1' in types:
        return 'registered'
    return 'unknown'


def current_name(company):
    names = company.get('names', []) or []
    live = [n for n in names if not n.get('endDate')]
    pick = live[-1] if live else (names[-1] if names else {})
    return pick.get('name', ''), [n.get('name', '') for n in names]


def normalize_company(company):
    name, names_all = current_name(company)
    addrs = company.get('addresses', []) or []
    a = addrs[0] if addrs else {}
    street = (a.get('street') or '').strip()
    if a.get('buildingNumber'):
        street = f"{street} {a.get('buildingNumber')}".strip()
    post = a.get('postCode') or a.get('postcode') or ''
    cities = [p.get('city', '') for p in (a.get('postOffices') or []) if p.get('city')]
    lines = [(b.get('type') or '') for b in (company.get('mainBusinessLine') and [company['mainBusinessLine']] or [])]
    return {
        'company_number': ((company.get('businessId') or {}).get('value')) or '',
        'company_name': name,
        'names_all': names_all,
        'status': canonical_status(company),
        'registered_address': f"{street}, {post} {cities[0] if cities else ''}".strip(', '),
        'postcode': str(post).replace(' ', ''),
        'municipality': cities[0] if cities else '',
        'business_lines': lines,
        'registration_date': ((company.get('businessId') or {}).get('registrationDate')) or '',
    }


def is_food(company):
    lines = [(b.get('type') or '') for b in (company.get('mainBusinessLine') and [company['mainBusinessLine']] or [])]
    return any(str(t).startswith(FOOD_PREFIXES) for t in lines)


def search_companies(name, municipality=None, limit=20):
    """Targeted corroboration query. Returns normalized dicts."""
    q = {'name': name, 'limit': str(limit)}
    url = BASE + '/companies?' + urllib.parse.urlencode(q)
    d = get(url)
    out = []
    for c in d.get('companies', [])[:limit]:
        n = normalize_company(c)
        if municipality and municipality.lower() not in (n['municipality'] or '').lower():
            continue
        out.append(n)
        time.sleep(0.3)
    return out


def fetch_bulk(outdir):
    """Full Trade Register dump (large). Snapshot + hash, never overwrite."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / 'prh_all_companies.zip'
    if dest.exists():
        raise SystemExit('snapshot exists; use a fresh directory')
    url = BASE + '/all_companies'
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=600).read()
    dest.write_bytes(data)
    (outdir / 'prh.meta.json').write_text(json.dumps({
        'source': 'prh-ytj', 'fetched_at': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
        'url': url,
    }, indent=2))
    print(f'saved {len(data)} bytes')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--search', default=None)
    ap.add_argument('--municipality', default=None)
    ap.add_argument('--bulk-out', default=None)
    a = ap.parse_args()
    if a.bulk_out:
        fetch_bulk(a.bulk_out)
    elif a.search:
        print(json.dumps(search_companies(a.search, a.municipality),
                         ensure_ascii=False, indent=1)[:2000])
    else:
        ap.error('give --search NAME or --bulk-out DIR')


if __name__ == '__main__':
    main()
