#!/usr/bin/env python3
"""Helsinki Servicemap existence layer (municipal, keyless, CC BY 4.0).

The City of Helsinki Service Map (api.hel.fi/servicemap/v2) lists service
points — including ~1,700 private restaurants/cafés/pubs — with names,
addresses, coordinates, websites and, for some units, opening-hours
connections. For Helsinki this stage plays the FSA role: an independent
existence check with authoritative names/addresses. Attribution (CC BY 4.0)
is rendered in the app; the release manifest records the pull.

Matching is brand-anchored like every other stage (shared specific
vocabulary + distance + postcode corroboration). Servicemap hours are
Finnish free text normalised conservatively to OSM-ish strings; date-bound
exceptions (Aikajakso) and reservation-only notes are skipped, never
invented. Unparseable hours = existence-only record, counted in meta.

  python3 servicemap.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json [--municipality helsinki]
"""
import argparse, json, re, time, urllib.parse, urllib.request
from pathlib import Path

UA = {'User-Agent': 'indiemaps-pack-builder/0.1 (+https://github.com/kvaak-kvaak/indiemaps)'}
BASE = 'https://api.hel.fi/servicemap/v2'
# Food services in the Servicemap ontology -> our categories. Institutional
# catering (schools, daycare, care homes, staff canteens) is excluded, same
# spirit as FSA_EXCLUDE: not public customer destinations.
SERVICES = {577: 'restaurant', 239: 'cafe', 869: 'pub', 919: 'pub'}
FI_DAYS = {'ma': 'Mo', 'ti': 'Tu', 'ke': 'We', 'to': 'Th', 'pe': 'Fr', 'la': 'Sa', 'su': 'Su'}
ORDER = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
STOP = {'and', 'of', 'de', 'la', 'oy', 'ab', 'the'}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def pick(d, default=''):
    """Multilingual dict -> fi, en, sv fallback."""
    if isinstance(d, dict):
        return (d.get('fi') or d.get('en') or d.get('sv') or default).strip()
    return (d or default).strip() if isinstance(d, str) else default


def norm(s):
    s = re.sub(r'\bltd\b|\blimited\b|\bplc\b|\bthe\b|\boy\b|\bab\b', '', (s or '').lower())
    return re.sub(r'[^a-z0-9åäö ]', ' ', s.replace('&', 'and'))


def toks(s):
    return set(w for w in norm(s).split() if len(w) >= 3 and w not in STOP)


def dist_m(a, b, c, d):
    return (((a - c) * 111320) ** 2 + ((b - d) * 62500) ** 2) ** 0.5


def norm_pc(p):
    return (p or '').replace(' ', '').lower() or None


def parse_fi_hours(text):
    """Finnish free-text hours -> OSM-ish string, or '' if unparseable.

    Handles: 'ma-pe 11.30-2.00', 'su suljettu' (closed), split-shift
    continuation lines ('ti-to\\n  10.30-14.30\\n  16.30-21.30'), overnight
    closes ('2.00'), midnight '0.00' -> 24:00. Skips date-range headers,
    Aikajakso exception dates and reservation-only notes — those need
    calendar logic, not string parsing.
    """
    text = (text or '').replace('\xa0', ' ')
    table = {}
    cur_days = []
    for line in text.splitlines():
        line = line.strip('–-• \t')
        if not line:
            continue
        if re.search(r'\d{1,2}\.\d{1,2}\.\d{4}|aikajakso', line, re.I):
            cur_days = []
            continue  # date-bound validity / exceptions: out of scope
        if re.search(r'varauksella|tilauksesta|sopimuksen mukaan', line, re.I) and \
                not re.search(r'\d{1,2}[.:]\d{2}', line):
            cur_days = []
            continue  # reservation-only, no times stated
        dm = re.match(r'(ma|ti|ke|to|pe|la|su)(?:\s*[-–]\s*(ma|ti|ke|to|pe|la|su))?\b\s*(.*)$', line, re.I)
        if dm:
            d1, d2 = FI_DAYS[dm.group(1).lower()], FI_DAYS[(dm.group(2) or dm.group(1)).lower()]
            i, j = ORDER.index(d1), ORDER.index(d2)
            cur_days = []
            k = i
            while True:
                cur_days.append(ORDER[k])
                if k == j:
                    break
                k = (k + 1) % 7
            rest = dm.group(3)
        else:
            rest = line  # continuation line: times attach to current days
        if re.search(r'suljettu|kiinni', rest, re.I):
            cur_days = []
            continue
        m = re.search(r'(\d{1,2})[.:](\d{2})\s*[-–]\s*(\d{1,2})[.:](\d{2})', rest)
        if m and cur_days:
            o, c = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
            if c == 0:
                c = 24 * 60
            if 0 <= o <= 24 * 60 and 0 <= c <= 24 * 60 and o != c:
                for d in cur_days:
                    table.setdefault(d, []).append((o, c))
    if not table:
        return ''
    groups, cur = [], None
    for d in ORDER:
        if d not in table:
            if cur:
                groups.append(cur)
                cur = None
            continue
        h = table[d]
        if cur and cur[1] == h and ORDER.index(d) == ORDER.index(cur[2]) + 1:
            cur = (cur[0], cur[1], d)
        else:
            if cur:
                groups.append(cur)
            cur = (d, h, d)
    if cur:
        groups.append(cur)
    parts = []
    for a, h, b in groups:
        span = a if a == b else f'{a}-{b}'
        for o, c in h:
            parts.append(f'{span} {o // 60:02d}:{o % 60:02d}-{c // 60:02d}:{c % 60:02d}')
    return '; '.join(parts)


def pull_units(municipality):
    units = []
    for sid, cat in SERVICES.items():
        url = (f'{BASE}/unit/?service={sid}&municipality={municipality}'
               f'&page_size=1000&language=fi')
        while url:
            d = get(url)
            for u in d.get('results', []):
                u['_cat'] = cat
                units.append(u)
            url = d.get('next')
            time.sleep(0.3)
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    ap.add_argument('--municipality', default='helsinki')
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    t0 = time.time()
    units = pull_units(a.municipality)
    rows = []
    for u in units:
        loc = (u.get('location') or {}).get('coordinates') or [None, None]
        lon, lat = loc[0], loc[1]
        if lat is None or not (s <= lat <= n and w <= lon <= e):
            continue
        name = pick(u.get('name'))
        if not name:
            continue
        zipc = (u.get('address_zip') or '').strip() or None
        addr = pick(u.get('street_address'))
        hours, unparsed = '', False
        for c in (u.get('connections') or []):
            if c.get('section_type') == 'OPENING_HOURS':
                h = parse_fi_hours(pick((c.get('name') or {})))
                if h and not hours:
                    hours = h
                elif not h:
                    unparsed = True
        rows.append({'sm_id': u['id'], 'name': name, 'lat': lat, 'lng': lon,
                     'address': (addr + (f', {zipc}' if zipc else '')).strip(', '),
                     'postcode': zipc, 'website': pick(u.get('www')),
                     'phone': u.get('phone') if isinstance(u.get('phone'), str) else '',
                     'hours': hours, 'unparsed_hours': unparsed, 'cat': u['_cat']})
    print(f'servicemap units in bbox: {len(rows)} ({time.time()-t0:.0f}s)')

    pois = json.load(open(a.pois))
    matched = added = hours_kept = hours_unparsed = 0
    used = set()
    # Match against a snapshot: appended sm records must never become match
    # targets mid-loop (chain branches would collapse onto each other).
    base_pois = list(pois)
    for r in rows:
        if r['hours']:
            hours_kept += 1
        elif r['unparsed_hours']:
            hours_unparsed += 1
        best, bs = None, 0
        rpc = norm_pc(r['postcode'])
        for p in base_pois:
            if id(p) in used:
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
            best['sm_id'] = r['sm_id']
            if 'sm_match' not in best:
                best['sm_match'] = {'dist_m': round(dist_m(best['lat'], best['lng'], r['lat'], r['lng']))}
            if not best.get('website') and r['website']:
                best['website'] = r['website']
                best['website_source'] = 'servicemap'
            if not best.get('phone') and r['phone']:
                best['phone'] = r['phone']
                best['phone_source'] = 'servicemap'
            if r['hours'] and not best.get('sm_hours'):
                best['sm_hours'] = r['hours']
            if 'servicemap' not in best.get('sources', []):
                best['sources'].append('servicemap')
        else:
            added += 1
            rec = {
                'id': f"sm-{r['sm_id']}", 'sm_id': r['sm_id'],
                'fsa_id': None, 'osm_type': None, 'osm_id': None,
                'name': r['name'], 'category': r['cat'],
                'category_label': {'restaurant': 'Restaurant', 'cafe': 'Café', 'pub': 'Pub / Bar'}[r['cat']],
                'lat': r['lat'], 'lng': r['lng'], 'geo_precision': 'servicemap',
                'address': r['address'], 'postcode': r['postcode'],
                'phone': r['phone'],
                'email': '', 'website': r['website'],
                'facebook': '', 'instagram': '', 'twitter': '',
                'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
                'brand_wikidata': None, 'wikipedia': None, 'wikidata': None,
                'sm_hours': r['hours'],
                'amenities': [], 'photos': [], 'description': '',
                'sources': ['servicemap'],
            }
            # Sources follow pack convention: key present only when set.
            if r['phone']:
                rec['phone_source'] = 'servicemap'
            if r['website']:
                rec['website_source'] = 'servicemap'
            pois.append(rec)
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['servicemap'] = {'units_in_bbox': len(rows), 'matched': matched,
                          'added': added, 'hours_kept': hours_kept,
                          'hours_unparsed': hours_unparsed}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'servicemap: matched={matched} added={added} hours_kept={hours_kept} unparsed={hours_unparsed}')


if __name__ == '__main__':
    main()
