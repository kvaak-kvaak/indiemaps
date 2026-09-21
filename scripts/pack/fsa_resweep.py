#!/usr/bin/env python3
"""FSA second pass (resweep): late, stricter-then-different FSA reconciliation.

Base (build.js) seeds every kept FSA row once, then enrichment stages pile on.
Two gaps remain by construction:

(a) NEW rows: FHRSIDs published after the base extract (extract-date compare).
(b) DROPPED rows: kept-type rows base couldn't locate (no geocode, postcodes.io
    miss) — retried here, because centroids resolve over time.
(c) MERGE-MISSES: fsa-only POIs vs osm-only POIs under deliberately DIFFERENT
    rules from base (base is distance-gated; this pass is premises-exact +
    name-agreeing, distance-blind). Turnover pairs (same premises, new name)
    must NOT merge here — that's the stale-occupant flag system's job.

Match-or-create against the pack's osm-only records uses base's accept rule
(fhrs:id exact > name>=0.5 > name>=0.34 with <60m-or-postcode), plus the same
food-service category veto. New records mirror the base POI shape exactly and
carry fsa_resweep:true so Phase-2 guarded creation can tell fresh-FSA records
from base ones. Unlocatable kept rows are exposed in meta for the same gate.

Non-England areas (--fsa none): skips green with a meta note.

  python3 fsa_resweep.py --bbox=W,S,E,N --pois <pois.json> --meta <meta.json> --fsa 893
"""
import argparse, json, re, sys, time, urllib.request
from pathlib import Path

UA = {'User-Agent': 'indiemaps-pack-builder/0.1 (+https://github.com/kvaak-kvaak/indiemaps)'}
FSA_EXCLUDE = {'School/college/university', 'Hospital/Childcare/Caring Premises',
               'Mobile caterer', 'Manufacturers/packers', 'Importers/Exporters',
               'Distributors/Transporters', 'Farm', 'Other catering premises'}
FSA_FOOD_TYPES = {'Restaurant/Cafe/Canteen', 'Takeaway/sandwich shop', 'Pub/bar/nightclub'}
VETO_AMENITY = {'bank', 'cinema', 'theatre', 'arts_centre', 'library',
                'place_of_worship', 'doctors', 'dentist', 'clinic', 'hospital', 'optician'}
VETO_SHOP = {'clothes', 'shoes', 'jewelry', 'watches', 'books', 'furniture',
             'electronics', 'bicycle', 'car', 'car_repair', 'car_parts', 'motorcycle',
             'carpet', 'paint', 'hairdresser', 'beauty', 'tattoo', 'laundry',
             'dry_cleaning', 'funeral_directors', 'estate_agent', 'travel_agency'}
DUP_STOP = {'southend', 'Leigh', 'westcliff', 'essex', 'london', 'hackney', 'high',
            'street', 'road', 'avenue', 'parade', 'town', 'centre', 'store', 'sea',
            'restaurant', 'cafe', 'pub', 'bar', 'takeaway', 'food', 'pizza'}


def norm(s):
    s = re.sub(r'\bltd\b|\blimited\b|\bplc\b|\bthe\b|\boy\b|\bab\b', '', (s or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9åäö ]', ' ', s.replace('&', 'and'))).strip()


def toks(s):
    return {w for w in norm(s).split() if len(w) >= 3}


def stem(w):
    return w[:-1] if w.endswith('s') and not w.endswith('ss') else w


def name_score(a, b):
    ta = {stem(w) for w in toks(a)} - DUP_STOP
    tb = {stem(w) for w in toks(b)} - DUP_STOP
    if not ta or not tb:
        ca, cb = norm(a).replace(' ', ''), norm(b).replace(' ', '')
        if min(len(ca), len(cb)) >= 6 and (ca in cb or cb in ca):
            return 0.9
        return 0.0
    inter = len(ta & tb)
    sc = inter / max(len(ta), len(tb))
    ca, cb = norm(a).replace(' ', ''), norm(b).replace(' ', '')
    if min(len(ca), len(cb)) >= 6 and (ca in cb or cb in ca):
        sc = max(sc, 0.9)
    return sc


def hn(addr):
    m = re.match(r'\s*(\d+[a-z]?)', addr or '', re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r',\s*(\d+[a-z]?)\b', addr or '')
    return m.group(1).lower() if m else None


def npc(pc):
    return (pc or '').replace(' ', '').lower() or None


def dist_m(a, b, c, d):
    return (((a - c) * 111320) ** 2 + ((b - d) * 62500) ** 2) ** 0.5


def fsa_category(t):
    if t == 'Restaurant/Cafe/Canteen':
        return ['restaurant', 'Restaurant / Café']
    if t == 'Takeaway/sandwich shop':
        return ['restaurant', 'Takeaway']
    if t == 'Pub/bar/nightclub':
        return ['pub', 'Pub / Bar']
    if t == 'Hotel/bed & breakfast/guest house':
        return ['hotel', 'Hotel / B&B']
    if t.startswith('Retailers'):
        return ['shopping', 'Supermarket' if 'upermarket' in t else 'Food shop']
    return ['services', t]


def geocode_postcodes(pcs):
    geo = {}
    pcs = sorted(set(pcs))
    for i in range(0, len(pcs), 100):
        batch = pcs[i:i + 100]
        try:
            req = urllib.request.Request(
                'https://api.postcodes.io/postcodes',
                data=json.dumps({'postcodes': batch}).encode(),
                headers={'Content-Type': 'application/json', **UA})
            j = json.load(urllib.request.urlopen(req, timeout=60))
            for q, res in zip(batch, j['result']):
                if res and res.get('result'):
                    geo[q] = (res['result']['latitude'], res['result']['longitude'])
        except Exception as e:
            print('geocode batch failed:', str(e)[:80])
        time.sleep(0.3)
    return geo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    ap.add_argument('--fsa', required=True)
    a = ap.parse_args()

    meta = json.load(open(a.meta))
    if a.fsa == 'none':
        meta['fsa_resweep'] = {'skipped': 'non-England area (no FSA authority)'}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        print('fsa_resweep: skipped (non-England)')
        return

    url = f'https://ratings.food.gov.uk/OpenDataFiles/FHRS{a.fsa}en-GB.json'
    d = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={**UA, 'Accept': 'application/json'})
            d = json.load(urllib.request.urlopen(req, timeout=120))
            break
        except Exception as e:
            print(f'FSA download attempt {attempt + 1} failed: {str(e)[:100]}')
            time.sleep(10 * (attempt + 1))
    if d is None:
        sys.exit(f'FSA resweep download failed: {url}')
    extract = d['FHRSEstablishment']['Header']['ExtractDate']
    base_extract = meta.get('fsa_extract_date')
    print(f'fsa_resweep: fresh extract {extract} (base was {base_extract})')
    all_rows = d['FHRSEstablishment']['EstablishmentCollection']

    pois = json.load(open(a.pois))
    known_fsa = {p.get('fsa_id') for p in pois if p.get('fsa_id')}
    for p in pois:
        for al in p.get('fsa_alias') or []:
            if al.get('fsa_id'):
                known_fsa.add(al['fsa_id'])

    kept, unlocatable = [], []
    for e in all_rows:
        if e.get('BusinessType') in FSA_EXCLUDE:
            continue
        g = e.get('Geocode') or {}
        addr = [x.strip() for x in
                [e.get('AddressLine1'), e.get('AddressLine2'),
                 e.get('AddressLine3'), e.get('AddressLine4')] if (x or '').strip()]
        rec = {'fsa_id': e['FHRSID'], 'name': (e.get('BusinessName') or '').strip(),
               'type': e.get('BusinessType'), 'ratingDate': e.get('RatingDate'),
               'addr': addr, 'postcode': (e.get('PostCode') or '').strip() or None,
               'lat': float(g['Latitude']) if g.get('Latitude') is not None else None,
               'lng': float(g['Longitude']) if g.get('Longitude') is not None else None}
        if rec['lat'] is None and rec['postcode']:
            kept.append(rec)
        elif rec['lat'] is None:
            unlocatable.append(rec)
        else:
            kept.append(rec)

    # Retry geocoding for unlocated kept rows (base drops these outright).
    missing = [r for r in kept if r['lat'] is None and r['postcode']]
    if missing:
        geo = geocode_postcodes([r['postcode'] for r in missing])
        for r in missing:
            if r['postcode'] in geo:
                r['lat'], r['lng'] = geo[r['postcode']]
    still_missing = [r for r in kept if r['lat'] is None]
    for r in still_missing:
        unlocatable.append(r)
    kept = [r for r in kept if r['lat'] is not None]

    orphans = [p for p in pois if p.get('sources') == ['osm']]
    used = set()
    created = merged_osm = 0
    new_ids = set()

    def accept_osm(rec, p):
        """Base accept-rule mirror (name>=0.5, or name>=0.34 with
        <60m-or-postcode), food-service category guard included. NOTE: base's
        fhrs:id exact pass can't run here — OSM tags aren't stored on pack
        POIs — so this mirror is name/postcode/distance only."""
        d = dist_m(rec['lat'], rec['lng'], p['lat'], p['lng'])
        if d > 150:
            return None
        ns = name_score(rec['name'], p.get('name') or '')
        if rec['type'] in FSA_FOOD_TYPES and p.get('category') not in (
                'restaurant', 'cafe', 'pub', 'shopping', 'services', 'health'):
            return None
        pc = npc(rec['postcode']) and npc(rec['postcode']) == npc(p.get('postcode'))
        if ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc)):
            return ns + (0.3 if pc else 0)
        return None

    for rec in kept:
        if rec['fsa_id'] in known_fsa:
            continue
        new_ids.add(rec['fsa_id'])
        best, bs = None, 0
        for p in orphans:
            if id(p) in used:
                continue
            sc = accept_osm(rec, p)
            if sc is not None and sc > bs:
                bs, best = sc, p
        if best is not None:
            merged_osm += 1
            used.add(id(best))
            best['fsa_id'] = rec['fsa_id']
            best['name'] = rec['name']
            best['address'] = ', '.join(rec['addr']) + (f", {rec['postcode']}" if rec['postcode'] else '')
            best['postcode'] = rec['postcode']
            if 'fsa' not in best.get('sources', []):
                best['sources'] = (best.get('sources', []) or []) + ['fsa']
            best['fsa_resweep'] = True
            if rec['ratingDate']:
                best['fsa_rating_date'] = rec['ratingDate']
        else:
            created += 1
            cat, label = fsa_category(rec['type'])
            pois.append({
                'id': f"fsa-{rec['fsa_id']}", 'fsa_id': rec['fsa_id'],
                'osm_type': None, 'osm_id': None, 'name': rec['name'],
                'category': cat, 'category_label': label,
                'lat': rec['lat'], 'lng': rec['lng'], 'geo_precision': 'postcode',
                'address': ', '.join(rec['addr']) + (f", {rec['postcode']}" if rec['postcode'] else ''),
                'postcode': rec['postcode'],
                'phone': '', 'email': '', 'website': '',
                'facebook': '', 'instagram': '', 'twitter': '',
                'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
                'brand_wikidata': None, 'wikipedia': None, 'wikidata': None,
                'amenities': [], 'photos': [], 'description': '',
                'sources': ['fsa'], 'fsa_resweep': True,
                **({'fsa_rating_date': rec['ratingDate']} if rec['ratingDate'] else {}),
            })

    # Second matching pass: fsa-only x osm-only, premises-exact + name-agree,
    # distance-blind (deliberately different from base). Turnover pairs
    # (same premises, dissimilar name) must NOT merge — flags own them.
    # Eligibility is corroboration-based, not exact-sources-based: later
    # enrichment appends (overture/ta/atp) must not disqualify either side.
    # Measured: the Beach Hut way gained 'overture' from backfill and went
    # invisible to an exact-equality pass.
    fsa_only = [p for p in pois if 'fsa' in (p.get('sources') or [])
                and 'osm' not in (p.get('sources') or [])
                and not p.get('fsa_resweep_merged')]
    orphans2 = [p for p in pois if 'osm' in (p.get('sources') or [])
                and not any(s in (p.get('sources') or []) for s in ('fsa', 'ch', 'servicemap'))
                and id(p) not in used]
    second_merges = 0
    for f in fsa_only:
        fpc, fhn = npc(f.get('postcode')), hn(f.get('address'))
        if not fpc or not fhn:
            continue
        for o in orphans2:
            if id(o) in used:
                continue
            if npc(o.get('postcode')) != fpc or (hn(o.get('address')) or None) != fhn:
                continue
            if name_score(f.get('name') or '', o.get('name') or '') < 0.5:
                continue
            used.add(id(o))
            second_merges += 1
            if o.get('phone') and not f.get('phone'):
                f['phone'] = o['phone']
            if o.get('website') and not f.get('website'):
                f['website'] = o['website']
                f['website_source'] = 'osm'
            if o.get('opening_hours_osm') and not f.get('opening_hours_osm'):
                f['opening_hours_osm'] = o['opening_hours_osm']
            if o.get('cuisine') and not f.get('cuisine'):
                f['cuisine'] = o['cuisine']
            for key in ('osm_type', 'osm_id'):
                if o.get(key) and not f.get(key):
                    f[key] = o[key]
            if 'osm' not in f.get('sources', []):
                f['sources'].append('osm')
            f['lat'], f['lng'] = o['lat'], o['lng']
            f['geo_precision'] = 'osm'
            f['fsa_resweep_merged'] = o['id']
            o['superseded_by'] = {'id': f['id'], 'name': f.get('name'),
                                  'via': 'fsa_resweep second pass'}
            break

    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta['fsa_resweep'] = {
        'extract_date': extract, 'base_extract_date': base_extract,
        'new_fhrs': sorted(new_ids), 'created': created,
        'merged_osm': merged_osm, 'second_pass_merges': second_merges,
        'unlocatable': [{'fhrs_id': r['fsa_id'], 'name': r['name'],
                         'postcode': r['postcode'],
                         'type': r['type'],
                         'ratingDate': r['ratingDate']} for r in unlocatable],
    }
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'fsa_resweep: {len(new_ids)} new FHRSIDs -> {created} created, '
          f'{merged_osm} merged into osm-only, {second_merges} second-pass merges, '
          f'{len(unlocatable)} unlocatable kept for the Phase-2 gate')

    third = third_pass_exact_name(pois)
    if third:
        json.dump(pois, open(a.pois, 'w'), indent=1)
        meta = json.load(open(a.meta))
        meta['fsa_resweep']['third_pass_merges'] = third
        json.dump(meta, open(a.meta, 'w'), indent=1)


def third_pass_exact_name(pois):
    """Distance-blind exact-name merge (measured: Beach Hut FSA pin sits
    ~1.7km from its surveyed OSM way — no distance gate can ever merge
    that). An fsa-only POI merges an osm-only record iff ALL hold:

    - normalized names agree EXACTLY (min 6 chars — no fuzzy, no chains
      by accident),
    - same postcode, or same housenumber with a shared street word,
    - area-unique: exactly one FSA row and one OSM record share the name
      across the whole pack (chains structurally excluded).

    Overture rows never qualify as position donors (not surveyed). Merges
    adopt the OSM position/precision and log loudly with the distance, so
    every long jump is reviewable. Returns the merge list."""
    fsa_only = [p for p in pois if 'fsa' in (p.get('sources') or [])
                and 'osm' not in (p.get('sources') or [])
                and not p.get('fsa_resweep_merged')]
    orphans = [p for p in pois if 'osm' in (p.get('sources') or [])
               and not any(s in (p.get('sources') or []) for s in ('fsa', 'ch', 'servicemap'))
               and not p.get('superseded_by')]
    by_name_f, by_name_o = {}, {}
    for p in fsa_only:
        k = norm(p.get('name') or '')
        if len(k) >= 6:
            by_name_f.setdefault(k, []).append(p)
    for p in orphans:
        k = norm(p.get('name') or '')
        if len(k) >= 6:
            by_name_o.setdefault(k, []).append(p)
    merged = []
    for k in sorted(set(by_name_f) & set(by_name_o)):
        fs, os_ = by_name_f[k], by_name_o[k]
        if len(fs) != 1 or len(os_) != 1:
            continue  # not area-unique: chains, doublings, human queue
        f, o = fs[0], os_[0]
        # Rule 0 — mapper attestation beats geometry: the OSM record
        # carries this exact FHRSID (sourced from FHRS Open Data by the
        # mapper) and names agree exactly. No distance or postcode
        # sanity applies: the mapper already did the hard verification.
        attested = (str(o.get('osm_fhrs_id') or '') == str(f.get('fsa_id')))
        fpc, opc = npc(f.get('postcode')), npc(o.get('postcode'))
        same_pc = bool(fpc and opc and fpc == opc)
        fhn, ohn = hn(f.get('address')), hn(o.get('address'))
        fw = {w for w in norm(f.get('address') or '').split() if len(w) >= 5}
        ow = {w for w in norm(o.get('address') or '').split() if len(w) >= 5}
        same_street = bool(fhn and ohn and fhn == ohn and (fw & ow))
        if not (attested or same_pc or same_street):
            continue
        d = dist_m(f['lat'], f['lng'], o['lat'], o['lng'])
        if o.get('phone') and not f.get('phone'):
            f['phone'] = o['phone']
        if o.get('website') and not f.get('website'):
            f['website'] = o['website']
            f['website_source'] = 'osm'
        if o.get('opening_hours_osm') and not f.get('opening_hours_osm'):
            f['opening_hours_osm'] = o['opening_hours_osm']
        if o.get('cuisine') and not f.get('cuisine'):
            f['cuisine'] = o['cuisine']
        for key in ('osm_type', 'osm_id', 'osm_touched', 'osm_version', 'osm_fhrs_id'):
            if o.get(key) and not f.get(key):
                f[key] = o[key]
        if 'osm' not in f.get('sources', []):
            f['sources'].append('osm')
        f['lat'], f['lng'] = o['lat'], o['lng']
        f['geo_precision'] = 'osm'
        f['fsa_resweep_merged'] = o['id']
        o['superseded_by'] = {'id': f['id'], 'name': f.get('name'),
                              'via': 'fsa_resweep third pass'}
        merged.append({'fsa': f['id'], 'osm': o['id'], 'name': f.get('name'),
                       'moved_m': round(d)})
        print(f"third-pass MERGED: {f.get('name')} [{f['id']}] <- {o['id']} "
              f"(moved {round(d)}m)")
    return merged


if __name__ == '__main__':
    main()
