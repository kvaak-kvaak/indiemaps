#!/usr/bin/env python3
"""Overture contact backfill + high-confidence direct creation.

Backfill: websites + phones for listings that lack them (FSA carries no
websites, unmapped OSM nodes carry no contact). Fills ONLY empty fields,
never overwrites surveyed data, never invents.

Direct creation (user-approved 2026-10-08, ov-direct path): unmatched
Overture rows may become pins iff max(Meta-dataset confidence, Overture
confidence) >= 0.99 AND the category maps to pins or search-only (junk
classes excluded by map, default-exclude for unlisted). Positions are
parcel-grade (geo_precision overture + position_approx, stated not
surveyed). Ids from GERS ids (ov- + 16 hex). Quarantine applies. A new
creation path deliberately extends the recount gate (see build.py).

Identity note (recorded decision): overture_id is the Overture places-theme
feature ID. Overture feature IDs are GERS IDs only where the entity
participates in GERS (per Overture schema docs) — unverified per record, so
the field keeps its provenance name; consumers alias to gers_id at their own
boundary if their schema wants it.

  python3 overture.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json

Requires: pip install duckdb. Keyless (anonymous S3).
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overture-join'))
from join import norm, nscore, dist_m, grid_index, nearby  # noqa

RELEASE_PIN = '2026-08-19.0'  # fallback; discovery prefers newest published
S3BASE_TMPL = 's3://overturemaps-us-west-2/release/{rel}/theme=places/type=place/*.parquet'


def discover_release():
    """Newest published Overture release (Overture rotates old ones out;
    a hardcoded pin silently rots — measured: 2026-07-22.0 vanished within
    ~48h of a green run). Pure S3 listing, no credentials."""
    import urllib.request
    import re
    try:
        xml = urllib.request.urlopen(
            'https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/'
            '?list-type=2&prefix=release/&delimiter=%2F',
            timeout=30).read().decode()
        rels = sorted(set(re.findall(r'<Prefix>release/([^<]+)/</Prefix>', xml)))
        rels = [r for r in rels if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\d+', r)]
        if rels:
            return rels[-1]
    except Exception as e:
        print(f'release discovery failed ({str(e)[:80]}), using pin {RELEASE_PIN}')
    return RELEASE_PIN


def pull(w, s, e, n):
    import duckdb
    last = None
    for attempt in range(3):
        try:
            rel = discover_release() if attempt == 0 else RELEASE_PIN
            con = duckdb.connect()
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute("SET s3_region='us-west-2'; SET http_timeout=30000;")
            rows = con.execute(f"""SELECT id, names.primary AS name,
              (bbox.xmin+bbox.xmax)/2 AS lon, (bbox.ymin+bbox.ymax)/2 AS lat,
              addresses[1].postcode AS postcode, websites[1] AS website, phones[1] AS phone,
              socials AS socials,
              list_distinct([s2.dataset FOR s2 IN sources]) AS datasets
            FROM read_parquet('{S3BASE_TMPL.format(rel=rel)}')
            WHERE bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s}
              AND NOT list_contains(list_distinct([s2.dataset FOR s2 IN sources]), 'Microsoft')
              AND (len(websites) > 0 OR len(phones) > 0)
            """).fetchall()
            cols = ['id', 'name', 'lon', 'lat', 'postcode', 'website', 'phone', 'socials', 'datasets']
            out = [dict(zip(cols, r)) for r in rows]
            for o in out:
                o['release'] = rel
            return out
        except Exception as ex:
            last = ex
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f'Overture pull failed x3: {str(last)[:120]}')


def pull_raw(w, s, e, n, rel):
    """Bounded ORIGINAL Places rows (raw-input handoff): every record in
    the rectangle from the pinned release, before contact-only filtering
    and before the Microsoft exclusion. Full columns: coordinates,
    categories, confidence, contributor metadata and all available fields.
    Not consumed by the pipeline itself."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2'; SET http_timeout=30000;")
    rows = con.execute(f"""SELECT id, names.primary AS name,
      (bbox.xmin+bbox.xmax)/2 AS lon, (bbox.ymin+bbox.ymax)/2 AS lat,
      addresses AS addresses_raw, basic_category AS basic_category,
      taxonomy AS taxonomy, confidence AS confidence,
      websites AS websites, phones AS phones,
      socials AS socials, sources AS sources, brand AS brand,
      emails AS emails, operating_status AS operating_status,
      bbox AS bbox_raw
    FROM read_parquet('{S3BASE_TMPL.format(rel=rel)}')
    WHERE bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s}
    """).fetchall()
    cols = ['id', 'name', 'lon', 'lat', 'addresses', 'basic_category',
            'taxonomy', 'confidence', 'websites', 'phones', 'socials',
            'sources', 'brand', 'emails', 'operating_status', 'bbox_raw']
    out = [dict(zip(cols, r)) for r in rows]
    for o in out:
        o['release'] = rel
    return out


# Direct-creation class map (user-approved 2026-10-08): confidence gate
# (max Meta/Overture >= 0.99) admits; this map decides pins vs
# search-only vs excluded. Unlisted categories EXCLUDE (fail-closed:
# new classes never sneak in). Schools searchable ("meh"), stations /
# dealers / petrol pin (user decision); parking lots + manufacturers +
# ATMs excluded (measured junk).
OV_PIN = {
    'restaurant': 'restaurant', 'fast_food_restaurant': 'restaurant',
    'casual_eatery': 'restaurant', 'food_court': 'restaurant',
    'cafe': 'cafe', 'coffee_shop': 'cafe', 'bakery': 'cafe',
    'ice_cream_shop': 'cafe', 'bar': 'pub', 'pub': 'pub',
    'brewery': 'pub', 'nightclub': 'pub',
    'hotel': 'hotel', 'motel': 'hotel',
    'fashion_and_apparel_store': 'shopping', 'convenience_store': 'shopping',
    'food_and_beverage_store': 'shopping', 'hardware_home_and_garden_store': 'shopping',
    'electronics_store': 'shopping', 'flowers_and_gifts_store': 'shopping',
    'sporting_goods_store': 'shopping', 'shopping': 'shopping',
    'shopping_mall': 'shopping', 'department_store': 'shopping',
    'books_music_and_video_store': 'shopping', 'arts_crafts_and_hobby_store': 'shopping',
    'second_hand_store': 'shopping', 'laundry_service': 'shopping',
    'bank_or_credit_union': 'services', 'financial_service': 'services',
    'food_bank': 'services', 'post_office': 'services',
    'personal_or_beauty_service': 'services', 'personal_care_and_beauty_store': 'services',
    'wellness_service': 'services', 'complementary_and_alternative_medicine': 'services',
    'automotive_service': 'services', 'auto_dealer': 'services',
    'gas_station': 'services', 'fueling_station': 'services',
    'animal_or_pet_service': 'services',
    'gym': 'services', 'fitness_studio': 'services', 'sport_or_fitness_facility': 'services',
    'dental_clinic': 'health', 'outpatient_care_facility': 'health',
    'doctors_office': 'health', 'hospital': 'health', 'pharmacy_and_drug_store': 'health',
    'behavioral_or_mental_health_clinic': 'health', 'diagnostics_imaging_or_lab_service': 'health',
    'medical_service': 'health',
    'museum': 'culture', 'movie_theater': 'culture', 'library': 'culture',
    'christian_place_of_worship': 'culture', 'hindu_place_of_worship': 'culture',
    'jewish_place_of_worship': 'culture', 'muslim_place_of_worship': 'culture',
    'art_gallery': 'culture', 'performing_arts_venue': 'culture',
    'park': 'attraction', 'amusement_park': 'attraction', 'zoo': 'attraction',
    'train_station': 'transport', 'public_transit_facility_or_service': 'transport',
    'airport': 'transport',
}
OV_SEARCH = {
    'elementary_school', 'high_school', 'preschool', 'specialty_school',
    'place_of_learning', 'college_or_university',
    'real_estate_service', 'professional_service', 'b2b_office_and_professional_service',
    'technical_service', 'design_service', 'media_service', 'printing_service',
    'legal_service', 'accounting_service', 'insurance_service',
    'event_or_party_service', 'home_service', 'social_or_community_service',
    'civic_organization', 'family_service', 'senior_living_facility',
    'fire_station', 'police_station',
    'corporate_or_business_office', 'coworking_space',
}
OV_CONF_PASS = 0.99  # max(Meta confidence, Overture confidence)

ROOT = Path(__file__).resolve().parents[2]


def quarantine_ids():
    try:
        return set(json.load(open(ROOT / 'data' / 'quarantine.json')).get('ids', {}))
    except Exception:
        return set()


def ov_confidence(o):
    """max(Meta-dataset confidence, Overture top-level confidence)."""
    top = o.get('confidence') or 0
    meta = 0
    for s in (o.get('sources') or []):
        try:
            if isinstance(s, dict) and 'meta' in str(s.get('dataset', '')).lower():
                meta = max(meta, s.get('confidence') or 0)
        except Exception:
            pass
    try:
        return max(float(top), float(meta))
    except (TypeError, ValueError):
        return 0.0


def row_matches_any_poi(o, pois):
    """Creation anti-duplicate: does this raw row match ANY existing POI
    under the same matcher (name + distance + postcode, no contact needed)?
    Catches contact-bare rows the backfill pull can't see (measured: a
    contact-bare Pasha Kebab Bar 53m from its FSA namesake)."""
    for p in pois:
        if not p.get('name'):
            continue
        d = dist_m(p['lat'], p['lng'], o['lat'], o['lon'])
        ppc = norm_pc(p.get('postcode'))
        try:
            opc = norm_pc((o.get('addresses') or [{}])[0].get('postcode'))
        except Exception:
            opc = None
        pc = bool(opc and ppc and opc == ppc)
        if d > (250 if pc else 120):
            continue
        ns = nscore(p['name'], o.get('name') or '')
        if accept(p['name'], o.get('name') or '', ns, d, pc):
            return True
    return False


def norm_pc(p):
    return (p or '').replace(' ', '').lower() or None


# Social networks mapped from Overture socials[] URLs by domain. Bare
# mapper-entered handles (no URL) upgrade to full URLs; surveyed full URLs
# are never overwritten (bulk snapshots rot, mapper edits don't).
SOCIAL_DOMAINS = (('facebook', ('facebook.com',)),
                   ('instagram', ('instagram.com',)),
                   ('twitter', ('twitter.com', 'x.com')))


def pick_social(socials, domain_frag):
    for u in socials or []:
        if domain_frag in (u or '').lower():
            return u
    return None


def is_bare(v):
    v = (v or '').strip()
    return bool(v) and not v.lower().startswith('http')


# Prefer-Meta ranking (recorded product call): Meta rows carry a small score
# bonus as a freshness prior (FSQ bulk skews stale). 0.15 wins ties and
# near-ties but never overrides a clearly better FSQ match. FSQ stays as
# fallback: zero lost matches by construction. Measured basis: Meta won
# 88%/86% of current matches unassisted (Southend/Hackney).
META_BONUS = 0.15

STOP = {'and', 'of', 'de', 'la', 's'}
GENERIC = {'southend', 'Leigh', 'westcliff', 'chalkwell', 'shoebury', 'shoeburyness',
           'thorpe', 'essex', 'london', 'high', 'street', 'road', 'avenue',
           'broadway', 'parade', 'town', 'centre', 'center', 'branch', 'store',
           'station', 'sea', 'old', 'new', 'north', 'south', 'east', 'west', 'on'}


def toks(s):
    return set(w for w in norm(s).split() if len(w) >= 3 and w not in STOP)


def accept(pname, oname, ns, d, pc):
    shared = (toks(pname) & toks(oname)) - GENERIC
    if norm(pname) == norm(oname) and len(norm(pname)) >= 6:
        return True  # exact same name
    ca, cb = norm(pname).replace(' ', ''), norm(oname).replace(' ', '')
    if min(len(ca), len(cb)) >= 8 and (ca in cb or cb in ca):
        return True  # spaceless variant ('RedChilliezs' vs 'Red Chilliezs')
    if not shared:
        return False  # only generic town words in common (e.g. 'Southend')
    return ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    t0 = time.time()
    ov = pull(w, s, e, n)
    print(f'overture with contact: {len(ov)} ({time.time()-t0:.0f}s)')
    grid, cell = grid_index(ov, 'lat', 'lon')

    pois = json.load(open(a.pois))
    filled_web, filled_phone = 0, 0
    filled_soc = {'facebook': 0, 'instagram': 0, 'twitter': 0}
    upgraded_soc = {'facebook': 0, 'instagram': 0, 'twitter': 0}
    claimed = set()  # Overture ids matched to a POI (creation skips these)
    for p in pois:
        # clear previous backfill (re-run safe); manual/ surveyed values stay
        if p.get('website_source') == 'overture':
            del p['website']; del p['website_source']
        if p.get('phone_source') == 'overture':
            del p['phone']; del p['phone_source']
        for net, _ in SOCIAL_DOMAINS:
            if p.get(f'{net}_source') == 'overture':
                del p[net]; del p[f'{net}_source']
        p.pop('overture_id', None)
        p.pop('overture_match', None)
        p.pop('overture_datasets', None)
        p['sources'] = [s for s in p.get('sources', []) if s != 'overture']
        if p.get('website') and p.get('phone') and p.get('facebook') \
                and p.get('instagram') and p.get('twitter'):
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs = None, 0
        for j in nearby(grid, cell, p['lat'], p['lng']):
            o = ov[j]
            d = dist_m(p['lat'], p['lng'], o['lat'], o['lon'])
            opc = norm_pc(o.get('postcode'))
            pc = bool(opc and ppc and opc == ppc)
            if d > (250 if pc else 120):
                continue
            ns = nscore(p['name'], o['name'] or '')
            if accept(p['name'], o['name'] or '', ns, d, pc):
                sc = ns + (0.3 if pc else 0)
                if 'meta' in (o.get('datasets') or []):
                    sc += META_BONUS
                if sc > bs:
                    bs, best = sc, o
        if best is None:
            continue
        claimed.add(best.get('id'))
        contributed = False
        if not p.get('website') and best.get('website'):
            p['website'] = best['website']
            p['website_source'] = 'overture'
            filled_web += 1
            contributed = True
        if not p.get('phone') and best.get('phone'):
            p['phone'] = best['phone']
            p['phone_source'] = 'overture'
            filled_phone += 1
            contributed = True
        for net, frags in SOCIAL_DOMAINS:
            url = None
            for frag in frags:
                url = pick_social(best.get('socials'), frag)
                if url:
                    break
            if not url:
                continue
            if not p.get(net):
                p[net] = url
                p[f'{net}_source'] = 'overture'
                filled_soc[net] += 1
                contributed = True
            elif is_bare(p.get(net)):
                # Bare mapper handle -> full Overture URL. Surveyed full
                # URLs are never overwritten (bulk snapshots rot).
                p[net] = url
                p[f'{net}_source'] = 'overture'
                upgraded_soc[net] += 1
                contributed = True
        if contributed:
            p['overture_id'] = best['id']
            # Match diagnostics for the per-source debug UI: which Overture
            # row won (the id alone doesn't say what name matched), and which
            # dataset it came from (FSQ-vs-Meta freshness audits).
            p['overture_match'] = {'name': best.get('name'),
                                   'dist_m': round(dist_m(p['lat'], p['lng'], best['lat'], best['lon']))}
            p['overture_datasets'] = sorted(x for x in (best.get('datasets') or []) if x != 'Overture')
            if 'overture' not in p.get('sources', []):
                p['sources'].append('overture')
    # Direct creation (ov-direct): unmatched rows passing the confidence
    # gate and the class map become pins (or search-only). Same-matcher
    # anti-duplicate: anything any POI would match is claimed above.
    # Positions are parcel-grade (approx, stated). Re-run safe: stable
    # GERS-based ids make creation idempotent.
    created, created_ids, created_search, skipped_dupe = 0, [], 0, 0
    gate_tally = {'pass': 0, 'unlisted': 0}
    qids = quarantine_ids()
    known_ids = {p.get('id') for p in pois}
    try:
        raw_all = pull_raw(w, s, e, n, (ov[0].get('release') if ov else RELEASE_PIN))
    except Exception as ex:
        print(f'overture direct creation skipped ({str(ex)[:100]})')
        raw_all = []
    for o in raw_all:
        if not o.get('name') or o.get('id') in claimed:
            continue
        if row_matches_any_poi(o, pois):
            skipped_dupe += 1
            continue
        if ov_confidence(o) < OV_CONF_PASS:
            continue
        cat = o.get('basic_category')
        if cat in OV_PIN:
            cls, search_only = OV_PIN[cat], False
        elif cat in OV_SEARCH:
            cls, search_only = 'services', True
        else:
            gate_tally['unlisted'] += 1
            continue
        gate_tally['pass'] += 1
        oid = 'ov-' + str(o['id']).replace('-', '')[:16]
        if oid in known_ids or oid in qids:
            if oid in qids:
                print(f'quarantine: refusing to create {oid}')
            continue
        addr = ''
        try:
            a0 = (o.get('addresses') or [{}])[0] or {}
            addr = a0.get('freeform') or ''
        except Exception:
            pass
        pc = None
        try:
            pc = norm_pc(a0.get('postcode')) if addr else None
        except Exception:
            pass
        web = ph = ''
        try:
            ws, ps = o.get('websites') or [], o.get('phones') or []
            web = ws[0] if ws else ''
            ph = ps[0] if ps else ''
        except Exception:
            pass
        o_brand = ''
        try:
            bn = (o.get('brand') or {}).get('names') or []
            o_brand = (bn[0] or {}).get('primary', '') if bn else ''
        except Exception:
            pass
        label = {'restaurant': 'Restaurant', 'cafe': 'Café', 'pub': 'Pub / Bar',
                 'shopping': 'Shop', 'services': 'Services', 'health': 'Health',
                 'culture': 'Culture', 'hotel': 'Hotel', 'attraction': 'Attraction',
                 'transport': 'Transport', 'parking': 'Parking'}.get(cls, 'Services')
        rec = {
            'id': oid, 'name': o['name'], 'brand': o_brand,
            'category': cls, 'category_label': label,
            'lat': o.get('lat'), 'lng': o.get('lon'), 'geo_precision': 'overture',
            'position_approx': True,
            'address': addr, 'postcode': pc,
            'photos': [], 'description': '',
            'sources': ['overture'],
            'overture_id': o['id'],
            'overture_datasets': sorted(x.get('dataset') for x in (o.get('sources') or []) if isinstance(x, dict) and x.get('dataset')),
            'overture_confidence': round(ov_confidence(o), 3),
            'provisional_creation': 'ov-direct',
        }
        if web:
            rec['website'], rec['website_source'] = web, 'overture'
        if ph:
            rec['phone'], rec['phone_source'] = ph, 'overture'
        for net, frags in SOCIAL_DOMAINS:
            for frag in frags:
                url = pick_social(o.get('socials'), frag)
                if url:
                    rec[net], rec[f'{net}_source'] = url, 'overture'
                    break
        if search_only:
            rec['search_only'] = True
            created_search += 1
        pois.append(rec)
        known_ids.add(oid)
        created += 1
        created_ids.append(oid)
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['overture'] = {'release': (ov[0].get('release') if ov else RELEASE_PIN), 'contact_rows_in_bbox': len(ov),
                        'websites_filled': filled_web, 'phones_filled': filled_phone,
                        'socials_filled': filled_soc, 'socials_upgraded': upgraded_soc,
                        'meta_bonus': META_BONUS,
                        'created': created, 'created_search_only': created_search,
                        'created_ids': created_ids, 'skipped_dupe': skipped_dupe,
                        'gate_tally': gate_tally}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'backfilled websites={filled_web} phones={filled_phone} socials={filled_soc} upgraded={upgraded_soc} | created {created} ({created_search} search-only), skipped {skipped_dupe} same-store variants, gate {gate_tally}')
    # Raw-input handoff artifact (frozen-input comparison; not consumed by
    # the pipeline itself): every Places row in the rectangle, pinned
    # release, before contact-only filtering and the Microsoft exclusion.
    try:
        raw = pull_raw(w, s, e, n, meta['overture']['release'])
        raw_path = Path(a.pois).parent / 'overture-raw.json'
        json.dump(raw, open(raw_path, 'w'))
        print(f'overture raw rows in bbox: {len(raw)} -> {raw_path.name}')
    except Exception as ex:
        print(f'overture raw pull skipped ({str(ex)[:100]})')
    apply_aliases(a.pois, a.meta)


def absorb_alias(pois, e):
    """Verified-duplicate absorption: remove loser records into the
    survivor's fsa_alias history. Guards: survivor exists; each loser exists,
    shares the survivor's postcode, and isn't already gone. Anything else
    skips loudly — the registry never force-fits a moved world."""
    surv = next((x for x in pois if x.get('id') == e.get('id')), None)
    if surv is None:
        return False
    spc = (surv.get('postcode') or '').replace(' ', '').lower()
    changed = False
    for lid in e.get('absorb', []):
        loser = next((x for x in pois if x.get('id') == lid), None)
        if loser is None:
            continue
        if (loser.get('postcode') or '').replace(' ', '').lower() != spc:
            print(f"absorb SKIPPED (postcode drift): {lid} is {loser.get('postcode')!r}, survivor {spc!r}")
            continue
        surv.setdefault('fsa_alias', []).append({
            'fsa_id': loser.get('fsa_id'), 'name': loser.get('name'),
            'address': loser.get('address'), 'postcode': loser.get('postcode'),
            'ratingDate': loser.get('fsa_rating_date'),
            'absorbed_by_registry': True})
        pois.remove(loser)
        changed = True
        print(f"absorb APPLIED: {lid} ({loser.get('name')!r}) into {surv['id']}")
    return changed


def apply_aliases(pois_path, meta_path):
    """Verified-alias registry (data/aliases.json): human-confirmed renames
    the name-anchored rules structurally cannot see (TA-KO pattern: zero
    shared vocabulary). Applies ONLY on exact id + registered-name match —
    if the record's name drifted since verification, the world moved again
    and the entry is skipped loudly, never force-fitted. Display name goes
    to the operating name; the registered name, evidence and verifier stay
    on the record (alias object) for audit."""
    try:
        entries = json.load(open(Path(__file__).resolve().parents[2] / 'data' / 'aliases.json')).get('aliases', [])
    except Exception:
        return
    if not entries:
        return
    pois = json.load(open(pois_path))
    applied = 0
    for e in entries:
        if e.get('absorb'):
            if absorb_alias(pois, e):
                applied += 1
            continue
        p = next((x for x in pois if x.get('id') == e.get('id')), None)
        if p is None:
            continue  # different area: registry is global, packs are local
        if p.get('name') != e.get('registered_name'):
            print(f"alias SKIPPED (drift): {e.get('id')} now reads {p.get('name')!r}, expected {e.get('registered_name')!r}")
            continue
        p['fsa_name'] = e['registered_name']
        p['name'] = e['alias_name']
        if e.get('alias_website') and p.get('website') != e['alias_website']:
            p['website'] = e['alias_website']
            p['website_source'] = 'overture'  # string provenance: Overture Meta row, human-verified live
        p['alias'] = {'registered': e['registered_name'], 'verified': e.get('verified'),
                      'verified_by': e.get('verified_by'), 'evidence': e.get('evidence', [])}
        applied += 1
        print(f"alias APPLIED: {e['id']} now reads {e['alias_name']!r} (was {e['registered_name']!r})")
    if applied:
        json.dump(pois, open(pois_path, 'w'), indent=1)
        meta = json.load(open(meta_path))
        meta['overture']['aliases_applied'] = applied
        json.dump(meta, open(meta_path, 'w'), indent=1)


if __name__ == '__main__':
    main()
