#!/usr/bin/env python3
"""Finnish chain enrichment (Restel + Raflaamo + ABC + S-etukortti +
restaurant-official + chain-expansion + official-locator snapshots).

data/chains_fi/*.json are one-off first-party harvests (Oct 2026, metas
beside them): surveyed positions, phones, and hours. MATCH ONLY: rows
that match nothing are discarded, no POI is ever created — snapshots age,
and creation from aging data is exactly what this stage must not do.

Per-row doctrine comes from the harvests themselves where present
(s-etukortti map_policy): existing_identity_match_required matches
normally; search_only rows (EV chargers etc.) attach but force
search_only; host_link_review rows attach as search_only pending a host
link. Address-only rows (no coords) never reach the matcher at all.
Unclassified rows match any food/shop/service/hotel POI but require a
postcode hit within 100 m. Helsinki-file rows win over finland-file rows
on same-name double hits (their hours/URLs are richer).

Enrichment per match: chain_brand + chain_source, chain phone (fills
empties only, same rule as Overture backfill), chain hours (gap-only like
ta_hours — current sources always win by absence of competition). Matched
records join sources 'chain', which corroborates like ATP (chain-published
position agreement) for the orphan rule and keep-evidence.

Re-run safe (clears chain_* first). FI areas only (wired in build.py).

  python3 chains_fi.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, re
from pathlib import Path

from overture import toks, accept, nscore, dist_m, grid_index, nearby, norm_pc  # noqa

ROOT = Path(__file__).resolve().parents[2]
CFI = ROOT / 'data' / 'chains_fi'
SOURCES = {'restel': CFI / 'restel.json',
           'raflaamo': CFI / 'raflaamo.json',
           'abc': CFI / 'abc.json',
           'set_hki': CFI / 's-etukortti-helsinki' / 'normalized.json',
           'set_fi': CFI / 's-etukortti-finland' / 'normalized.json',
           'rest_off': CFI / 'restaurant-official' / 'normalized.json',
           'chain_exp': CFI / 'chain-expansion' / 'normalized.json',
           'biltema': CFI / 'official-locator-samples' / 'biltema.json',
           'budgetsport': CFI / 'official-locator-samples' / 'budgetsport.json',
           'loc_norm': CFI / 'official-locator-samples' / 'normalized.json'}
ABC_KEEP_CAPS = {'refuel', 'grocer', 'menu', 'food', 'restaurant', 'cafe', 'market', 'carwash'}
DAYMAP = {'monday': 'Mo', 'tuesday': 'Tu', 'wednesday': 'We', 'thursday': 'Th',
          'friday': 'Fr', 'saturday': 'Sa', 'sunday': 'Su'}
DAYMAP_FI = {'maanantai': 'Mo', 'tiistai': 'Tu', 'keskiviikko': 'We', 'torstai': 'Th',
             'perjantai': 'Fr', 'lauantai': 'Sa', 'sunnuntai': 'Su'}
SETDAY = {'MON': 'Mo', 'TUE': 'Tu', 'WED': 'We', 'THU': 'Th', 'FRI': 'Fr', 'SAT': 'Sa', 'SUN': 'Su'}
ESPDAY = {'maanantai': 'Mo', 'tiistai': 'Tu', 'keskiviikko': 'We', 'torstai': 'Th',
          'perjantai': 'Fr', 'lauantai': 'Sa', 'sunnuntai': 'Su'}
# s-etukortti category -> POI categories it may match. Unlisted category =
# generic (any food/shop/service/hotel, postcode-required). Keep tight:
# a wrong-category match is worse than a miss.
SET_CATS = {
    'restaurant': {'restaurant', 'cafe', 'pub'},
    'fast_food': {'restaurant', 'cafe', 'pub'},
    'cafe': {'restaurant', 'cafe', 'pub'},
    'bar': {'restaurant', 'cafe', 'pub'},
    'pub': {'restaurant', 'cafe', 'pub'},
    'food_outlet': {'restaurant', 'cafe', 'pub'},
    'food_drink_unspecified': {'restaurant', 'cafe', 'pub'},
    'terrace': {'restaurant', 'cafe', 'pub'},
    'hotel_breakfast': {'restaurant', 'cafe', 'pub'},
    'hotel': {'hotel'},
    'supermarket': {'shopping'},
    'convenience_store': {'shopping'},
    'department_store': {'shopping'},
    'cosmetics': {'shopping'},
    'florist': {'shopping'},
    'pharmacy': {'health'},
    'dentist': {'health'},
    'optician': {'health'},
    'eye_clinic': {'health'},
    'bank_service': {'services'},
    'banking_financial_services': {'services'},
    'insurance': {'services'},
    'legal_services': {'services'},
    'funeral_director': {'services'},
    'ferry_services': {'services'},
    'cinema_event_venue': {'services'},
    'meeting_event_space': {'services'},
    'car_wash': {'services'},
    'service_station': {'shopping', 'transport'},
    'ev_charging': {'parking', 'transport', 'services'},
}


def restel_hours_to_osm(h, daymap=DAYMAP):
    """Per-day strings ({"monday": "11-23", ...}) to OSM syntax.
    Empty/missing days are closed. Unparseable yields '' — never invented."""
    if not isinstance(h, dict):
        return ''
    parts = []
    for day, code in daymap.items():
        v = (h.get(day) or '').strip()
        if not v:
            continue
        m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?$', v)
        if not m:
            continue
        o = f'{int(m.group(1)):02d}:{m.group(2) or "00"}'
        c = f'{int(m.group(3)):02d}:{m.group(4) or "00"}'
        parts.append(f'{code} {o}-{c}')
    return '; '.join(parts)


def setweek_hours_to_osm(wt):
    """s-etukortti weekly_opening_times ([{day, mode, ranges, exceptional,
    message}]) to OSM syntax. Exceptional/annotated days are skipped —
    never interpreted. Empty yields ''."""
    if not isinstance(wt, list):
        return ''
    parts = []
    for d in wt:
        if not isinstance(d, dict):
            continue
        if d.get('exceptional') or d.get('message'):
            continue
        code = SETDAY.get((d.get('day') or '').upper())
        if not code:
            continue
        mode = (d.get('mode') or '').upper()
        if mode == 'ALL_DAY':
            parts.append(f'{code} 00:00-24:00')
        elif mode == 'RANGE':
            for r in (d.get('ranges') or []):
                o, c = r.get('open'), r.get('close')
                if o and c and re.match(r'^\d{2}:\d{2}$', o) and re.match(r'^\d{2}:\d{2}$', c):
                    parts.append(f'{code} {o}-{c}')
    return '; '.join(parts)


def espresso_hours_to_osm(h):
    """Espresso House [{weekDay (Finnish), openFrom/openTo HH:MM:SS}]."""
    if not isinstance(h, list):
        return ''
    parts = []
    for d in h:
        code = ESPDAY.get((d.get('weekDay') or '').lower())
        o, c = (d.get('openFrom') or '')[:5], (d.get('openTo') or '')[:5]
        if code and re.match(r'^\d{2}:\d{2}$', o) and re.match(r'^\d{2}:\d{2}$', c):
            parts.append(f'{code} {o}-{c}')
    return '; '.join(parts)


def budgetsport_hours_to_osm(r):
    """Budget Sport openingHours microdata is already OSM syntax
    ("Mo-Fr 10:00-20:00"). Join; fall back to the split day fields."""
    oh = r.get('openingHours')
    if isinstance(oh, list):
        parts = [d.get('microdata') for d in oh if isinstance(d, dict) and d.get('microdata')]
        if parts:
            return '; '.join(parts)
    parts = []
    for key, codes in (('opening_hours_mo_fri', ['Mo', 'Tu', 'We', 'Th', 'Fr']),
                       ('opening_hours_sat', ['Sa']), ('opening_hours_sun', ['Su'])):
        v = (r.get(key) or '').strip()
        m = re.match(r'^(\d{2}:\d{2})-(\d{2}:\d{2})$', v)
        if m:
            parts.append(f"{','.join(codes)} {m.group(1)}-{m.group(2)}")
    return '; '.join(parts)


def _coords(r):
    """lat/lng under any of the harvest spellings; None when absent or
    unparseable — never zero-filled, never invented."""
    for la, ln in (('lat', 'lng'), ('latitude', 'longitude')):
        try:
            if r.get(la) is None or r.get(ln) is None:
                continue
            return float(r[la]), float(r[ln])
        except (TypeError, ValueError):
            continue
    return None, None


def norm_row(src, r):
    """Per-source record -> unified match row, or None to skip. The skip
    reasons are doctrine: no coords, no name, EV-only, address-only."""
    lat, lng = _coords(r)
    if lat is None or lng is None:
        return None
    name = r.get('name')
    if not name:
        return None
    if src == 'abc' and not set(r.get('capabilities') or []) & ABC_KEEP_CAPS:
        return None  # EV-only chargers are not destinations
    row = {'src': src, 'name': name, 'lat': lat, 'lng': lng,
           'phone': r.get('phone') or '',
           'postcode': (r.get('postcode') or r.get('zipCode') or r.get('postal_code') or '').replace(' ', '').lower() or None,
           'hours_raw': None, 'hours_kind': 'none',
           'cats': {'restaurant', 'cafe', 'pub'}, 'cap': 150,
           'strict_pc': False, 'policy': 'existing'}
    if src in ('restel', 'abc'):
        row['hours_raw'] = r.get('hours')
        row['hours_kind'] = 'osm' if src == 'abc' else 'dict'
        if src == 'abc':
            # Fuel/convenience matches shops and stations, not just food.
            row['cats'] = {'restaurant', 'cafe', 'pub', 'shopping', 'transport'}
            row['cap'] = 100
    elif src in ('set_hki', 'set_fi'):
        row['policy'] = {'existing_identity_match_required': 'existing',
                         'search_only': 'search_only',
                         'host_link_review': 'host_review'}.get(r.get('map_policy'), 'existing')
        cat = r.get('category')
        if cat in SET_CATS:
            row['cats'] = SET_CATS[cat]
        else:
            row['strict_pc'] = True  # unclassified: any food/shop/service/hotel, postcode-required
            row['cats'] = {'restaurant', 'cafe', 'pub', 'shopping', 'services', 'hotel'}
            row['cap'] = 100
        if cat in ('service_station', 'ev_charging'):
            row['cap'] = 100
        if cat in ('supermarket', 'convenience_store', 'department_store'):
            # Adjacent-venue precedent (measured 130 m Atusa/S-market
            # near-miss): grocery rows are surveyed chain GPS, premises only.
            row['cap'] = 100
        row['hours_raw'] = r.get('weekly_opening_times')
        row['hours_kind'] = 'setweek'
        row['phone'] = r.get('phone') or ''
    elif src == 'rest_off':
        brand = r.get('brand') or ''
        row['cats'] = {'restaurant', 'cafe', 'pub'}
        if brand == 'Espresso House':
            row['hours_raw'] = r.get('hours')
            row['hours_kind'] = 'espresso'
        # Picnic hours_html stays raw (source: not permanent hours); F&B friends: no hours
    elif src == 'chain_exp':
        if not r.get('map_eligible'):
            return None
        row['cats'] = {'restaurant', 'cafe', 'pub'}
        if r.get('hours'):
            row['hours_raw'] = r.get('hours')
            row['hours_kind'] = 'dict_fi'
    elif src == 'biltema':
        # Name is city-only ("Espoo") — match on brand+coords+address instead.
        row['name'] = f"Biltema {r.get('city') or ''}".strip()
        row['cats'] = {'shopping'}
        row['cap'] = 150
        row['brand_gate'] = 'Biltema'
        # opensToday/closesToday are day-specific — never stable hours.
    elif src == 'budgetsport':
        row['cats'] = {'shopping'}
        row['hours_raw'] = r
        row['hours_kind'] = 'budgetsport'
    elif src == 'loc_norm':
        # Eligibility-checked retail (9 brands, city-only names like the
        # Biltema file): brand+coords+address matching, no hours/phone.
        brand = (r.get('brand') or '').strip()
        if not brand:
            return None
        if name.lower().startswith(brand.lower()):
            row['name'] = name
        else:
            row['name'] = f'{brand} {name}'.strip()
        row['cats'] = {'shopping'}
        row['brand_gate'] = brand
    return row


def convert_hours(row):
    k = row['hours_kind']
    if k == 'osm':
        return row['hours_raw'] or ''
    if k == 'dict':
        return restel_hours_to_osm(row['hours_raw'])
    if k == 'dict_fi':
        return restel_hours_to_osm(row['hours_raw'], DAYMAP_FI)
    if k == 'setweek':
        return setweek_hours_to_osm(row['hours_raw'])
    if k == 'espresso':
        return espresso_hours_to_osm(row['hours_raw'])
    if k == 'budgetsport':
        return budgetsport_hours_to_osm(row['hours_raw'])
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    rows = []
    per_src = {}
    ev_skipped = 0
    for src, path in SOURCES.items():
        try:
            data = json.load(open(path))
        except Exception as ex:
            print(f'chains_fi: {src} unreadable ({ex}), skipped')
            continue
        n0 = 0
        for r in data:
            lat, lng = _coords(r)
            if lat is None or lng is None:
                continue
            if not (s <= lat <= n and w <= lng <= e):
                continue
            row = norm_row(src, r)
            if row is None:
                ev_skipped += 1
                continue
            rows.append(row)
            n0 += 1
        per_src[src] = n0
    print(f'chains_fi rows in bbox: {len(rows)} {per_src} (doctrine-skipped: {ev_skipped})')

    pois = json.load(open(a.pois))
    for p in pois:
        for k in ('chain_brand', 'chain_source', 'chain_hours'):
            p.pop(k, None)
        p['sources'] = [x for x in p.get('sources', []) if x != 'chain']

    grid, cell = grid_index(rows, 'lat', 'lng') if rows else ({}, None)
    matched = hours_added = phones_added = forced_search = 0
    per_src_matched = {src: 0 for src in SOURCES}
    claimed_hki = {}  # poi_id -> [helsinki-row names that hit it]: finland rows defer
    for p in pois:
        if not p.get('name'):
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs, brow = None, 0, None
        if rows:
            for j in nearby(grid, cell, p['lat'], p['lng']):
                o = rows[j]
                if p.get('category') not in o['cats']:
                    continue
                d = dist_m(p['lat'], p['lng'], o['lat'], o['lng'])
                if d > o['cap']:
                    continue
                rpc = o.get('postcode')
                pc = bool(rpc and ppc and rpc == ppc)
                if o.get('strict_pc') and not pc:
                    continue
                if o.get('brand_gate'):
                    bt = toks(o['brand_gate'])
                    if bt and not (bt & toks(p['name'])):
                        continue  # city/mall-name overlap is not identity:
                                  # Masku is not Tokmanni (measured)
                ns = nscore(p['name'], o['name'])
                if accept(p['name'], o['name'], ns, d, pc):
                    sc = ns + (0.3 if pc else 0)
                    if sc > bs:
                        bs, best, brow = sc, o, o
        if best is None:
            continue
        # Helsinki-wins: a finland row defers when a same-name helsinki row
        # already claimed this POI (richer hours/URL on the helsinki side).
        if brow['src'] == 'set_fi' and p['id'] in claimed_hki:
            if any(nscore(best['name'], n) >= 0.99 for n in claimed_hki[p['id']]):
                continue
        if brow['src'] == 'set_hki':
            claimed_hki.setdefault(p['id'], []).append(best['name'])
        matched += 1
        per_src_matched[brow['src']] = per_src_matched.get(brow['src'], 0) + 1
        p['chain_brand'] = best['name']
        p['chain_source'] = brow['src']
        if best.get('phone') and not p.get('phone'):
            p['phone'] = best['phone']
            p['phone_source'] = 'chain'
            phones_added += 1
        if best.get('hours_raw') and not (p.get('opening_hours_osm') or p.get('atp_hours')
                                          or p.get('site_hours') or p.get('nhs_hours')
                                          or p.get('sm_hours') or p.get('chain_hours')):
            h = convert_hours(best)
            if h:
                p['chain_hours'] = h
                hours_added += 1
        if brow.get('policy') in ('search_only', 'host_review'):
            if not p.get('search_only'):
                p['search_only'] = True
                forced_search += 1
        if 'chain' not in p.get('sources', []):
            p['sources'].append('chain')
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['chains_fi'] = {'rows_in_bbox': len(rows), 'matched': matched,
                         'hours_added': hours_added, 'phones_added': phones_added,
                         'forced_search_only': forced_search,
                         'per_src_rows': per_src, 'per_src_matched': per_src_matched}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'chains_fi: {len(rows)} rows -> {matched} matched, +{hours_added} hours, '
          f'+{phones_added} phones, +{forced_search} forced-search {per_src_matched}')


if __name__ == '__main__':
    main()
