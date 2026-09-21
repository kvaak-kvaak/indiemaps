#!/usr/bin/env python3
"""Stale-dump enrichment (research parquet, keyless bulk read).

A 4-5 year old restaurant dump (~1M rows, 139 MB) is matched against pack
POIs to recover cuisines, hours, dietary flags and ratings for venues that
predate current sources. MATCH-FIRST: rows that match a pack POI enrich it;
unmatched rows are discarded — UNLESS Phase-2 gated creation applies (a
fresh-FSA record with no pack POI corroborates the row via
match_unlocatable). Created records carry provisional_creation:'ta_new',
FSA-verified names, dump coordinates (geo_precision 'dump', honest
downgrade), and cuisines only — never hours or ratings (both time-sensitive;
hours arrive via spider/backfill later). Resurrecting closures from stale
data stays forbidden: no FSA corroboration, no POI.

Staleness doctrine (the file carries no timestamps; vintage is
maintainer-declared c.2021):
- Names/locations are match keys only, never written (pack names are
  current-verified; the dump is stale by design — e.g. it holds the dead
  'Victory Mansion', not TA-KO).
- Hours apply ONLY where the record has zero hours from any current
  source (OSM/ATP/site/NHS/Servicemap). Current hours always win by
  absence of competition.
- Cuisines are compared, never merged (ta_cuisines + agree/extend/conflict).
- Only affirmative dietary flags are stored (Y); N/empty render as unknown.
- Ratings are display garnish only (numeric + count stored; emoji derived
  at render): a 5.0 on a defunct venue must never read as endorsement.
- All review-derived keys live under ta_* so OSM-compatible consumers
  strip them cleanly (no reviews carry hours in OSM's schema).

  python3 ta.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overture import toks, accept, nscore, dist_m, grid_index, nearby, norm_pc, match_unlocatable  # noqa

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / 'restaurants.parquet'
VINTAGE = 'c.2021 (maintainer-declared; file carries no timestamps)'
UK_PC = re.compile(r'([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})')
FI_PC = re.compile(r'\b(\d{5})\b')
DAY_ORDER = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
OSM_DAYS = {'monday': 'Mo', 'mon': 'Mo', 'tuesday': 'Tu', 'tue': 'Tu',
            'wednesday': 'We', 'wed': 'We', 'thursday': 'Th', 'thu': 'Th',
            'friday': 'Fr', 'fri': 'Fr', 'saturday': 'Sa', 'sat': 'Sa',
            'sunday': 'Su', 'sun': 'Su'}
TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$')


def dump_postcode(address):
    """Postcode from a freeform address (UK full, FI 5-digit)."""
    a = address or ''
    m = UK_PC.search(a.upper())
    if m:
        return m.group(1).replace(' ', '').lower() or None
    m = FI_PC.search(a)
    if m:
        return m.group(1) or None
    return None


def norm_cuisines(s):
    return [c.strip() for c in (s or '').split(',') if c.strip()]


def cuisine_verdict(pack_cuisines, dump_cuisines):
    """agree: dump adds nothing. extend: pack has none, dump has some.
    conflict: dump names cuisines the pack doesn't carry."""
    ps = {c.lower() for c in pack_cuisines if c}
    ds = {c.lower() for c in dump_cuisines if c}
    if not ds:
        return 'agree'
    if not ps:
        return 'extend'
    if ds - ps:
        return 'conflict'
    return 'agree'


def fi_hours_to_osm(obj):
    """Per-day JSON arrays ({"Mon": ["08:30-22:00"], "Tue": []}) to an
    OSM-syntax string. Empty arrays are closed days. Malformed ranges are
    dropped, never invented; unparseable input yields ''."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception:
            return ''
    if not isinstance(obj, dict):
        return ''
    table = {}
    for raw_day, ranges in obj.items():
        day = OSM_DAYS.get(str(raw_day).strip().lower()[:3], '')
        if not day or not isinstance(ranges, list):
            continue
        for r in ranges:
            m = TIME_RE.match(str(r or '').strip())
            if not m:
                continue
            try:
                o, c = (int(m.group(1)) * 60 + int(m.group(2)),
                        int(m.group(3)) * 60 + int(m.group(4)))
            except ValueError:
                continue
            if 0 <= o <= 24 * 60 and 0 <= c <= 24 * 60 and o != c:
                table.setdefault(day, []).append((o, c))
    if not table:
        return ''
    groups, cur = [], None
    for d in DAY_ORDER:
        if d not in table:
            if cur:
                groups.append(cur)
                cur = None
            continue
        h = table[d]
        if cur and cur[1] == h and DAY_ORDER.index(d) == DAY_ORDER.index(cur[2]) + 1:
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


def match_names(poi):
    """All names a POI may be known under: display, FSA-registered, alias."""
    names = [poi.get('name'), poi.get('fsa_name'),
             (poi.get('alias') or {}).get('registered')]
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    if not PARQUET.exists():
        # Research file, never committed: runs without it skip the stage
        # green (nothing to enrich from). The file reaches builders only
        # through an explicit distribution decision (release asset or
        # committed filtered extract) — never silently, never by default.
        print(f'stale dump absent ({PARQUET.name}), skipping ta stage')
        meta = json.load(open(a.meta))
        meta['ta'] = {'skipped': 'parquet absent', 'vintage': VINTAGE,
                      'matched': 0}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        return
    w, s, e, n = map(float, a.bbox.split(','))
    import duckdb
    t0 = time.time()
    rows = duckdb.connect().execute(f"""SELECT restaurant_name, address, latitude, longitude,
      cuisines, vegetarian_friendly, vegan_options, gluten_free,
      original_open_hours, avg_rating, total_reviews_count
    FROM read_parquet('{PARQUET}')
    WHERE latitude BETWEEN {s} AND {n} AND longitude BETWEEN {w} AND {e}""").fetchall()
    cols = ['name', 'address', 'lat', 'lng', 'cuisines', 'veg', 'vegan', 'gf',
            'hours', 'rating', 'nrev']
    rows = [dict(zip(cols, r)) for r in rows if r[0] and r[2] is not None and r[3] is not None]
    print(f'stale dump rows in bbox: {len(rows)} ({time.time()-t0:.0f}s)')

    pois = json.load(open(a.pois))
    # clear previous enrichment (re-run safe)
    for p in pois:
        for k in ('ta_cuisines', 'ta_cuisine_match', 'ta_hours', 'ta_vegetarian',
                  'ta_vegan', 'ta_gluten_free', 'ta_rating', 'ta_reviews'):
            p.pop(k, None)
        p['sources'] = [s for s in p.get('sources', []) if s != 'ta']

    grid, cell = grid_index(rows, 'lat', 'lng') if rows else ({}, None)
    matched = hours_added = diets_added = created = 0
    used_rows = set()
    cuisine_v = {'agree': 0, 'extend': 0, 'conflict': 0}
    rating_bands = {'5.0': 0, '4.5': 0, '4.0': 0, 'other': 0, 'none': 0}
    for p in pois:
        if p.get('category') not in ('restaurant', 'cafe', 'pub'):
            continue
        names = match_names(p)
        if not names:
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs, best_j = None, 0, None
        if rows:
            for j in nearby(grid, cell, p['lat'], p['lng']):
                o = rows[j]
                d = dist_m(p['lat'], p['lng'], o['lat'], o['lng'])
                if d > 150:
                    continue
                rpc = norm_pc(dump_postcode(o.get('address')))
                pc = bool(rpc and ppc and opc == ppc) if (opc := rpc) else False
                for nm in names:
                    ns = nscore(nm, o['name'] or '')
                    if accept(nm, o['name'] or '', ns, d, pc):
                        sc = ns + (0.3 if pc else 0)
                        if sc > bs:
                            bs, best, best_j = sc, o, j
        if best is None:
            continue
        used_rows.add(best_j)
        matched += 1
        pack_cuis = ([p.get('cuisine')] if p.get('cuisine') else []) + (p.get('site_cuisine') or [])
        dc = norm_cuisines(best.get('cuisines'))
        if dc:
            # Empty dump cuisines carry no claim — nothing stored, nothing counted.
            verdict = cuisine_verdict(pack_cuis, dc)
            cuisine_v[verdict] += 1
            p['ta_cuisines'] = dc
            p['ta_cuisine_match'] = verdict
        if not (p.get('opening_hours_osm') or p.get('atp_hours') or p.get('site_hours')
                or p.get('nhs_hours') or p.get('sm_hours')):
            h = fi_hours_to_osm(best.get('hours'))
            if h:
                p['ta_hours'] = h
                hours_added += 1
        diet = False
        if (best.get('veg') or '').strip().upper() == 'Y':
            p['ta_vegetarian'] = True
            diet = True
        if (best.get('vegan') or '').strip().upper() == 'Y':
            p['ta_vegan'] = True
            diet = True
        if (best.get('gf') or '').strip().upper() == 'Y':
            p['ta_gluten_free'] = True
            diet = True
        if diet:
            diets_added += 1
        try:
            rating = float(best.get('rating')) if best.get('rating') is not None else None
        except (TypeError, ValueError):
            rating = None
        if rating is None:
            rating_bands['none'] += 1
        else:
            p['ta_rating'] = rating
            try:
                p['ta_reviews'] = int(float(best.get('nrev') or 0))
            except (TypeError, ValueError):
                pass
            rating_bands['5.0' if rating == 5.0 else '4.5' if rating == 4.5 else
                         '4.0' if rating == 4.0 else 'other'] += 1
        if 'ta' not in p.get('sources', []):
            p['sources'].append('ta')
    if rows:
        # Gated creation (Phase 2): rows that enriched nothing may still
        # seed a POI — only via a fresh-FSA record with no pack POI.
        meta_pre = json.load(open(a.meta))
        unloc = (meta_pre.get('fsa_resweep') or {}).get('unlocatable', [])
        have_fsa = {p.get('fsa_id') for p in pois if p.get('fsa_id')}
        for j, o in enumerate(rows):
            if j in used_rows or o.get('lat') is None or o.get('lng') is None:
                continue
            f, sc = match_unlocatable(o.get('name'), norm_pc(dump_postcode(o.get('address'))), unloc)
            if f is None or f.get('fhrs_id') in have_fsa:
                continue
            created += 1
            have_fsa.add(f['fhrs_id'])
            dc = norm_cuisines(o.get('cuisines'))
            pois.append({
                'id': f"fsa-{f['fhrs_id']}", 'fsa_id': f['fhrs_id'],
                'osm_type': None, 'osm_id': None, 'name': f['name'],
                'category': 'restaurant', 'category_label': 'Restaurant / Café',
                'lat': o['lat'], 'lng': o['lng'], 'geo_precision': 'dump',
                'address': o.get('address') or '',
                'postcode': dump_postcode(o.get('address')),
                'phone': '', 'email': '', 'website': '',
                'facebook': '', 'instagram': '', 'twitter': '',
                'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
                'brand_wikidata': None, 'wikipedia': None, 'wikidata': None,
                'amenities': [], 'photos': [], 'description': '',
                'sources': ['ta'], 'provisional_creation': 'ta_new',
                **({'ta_cuisines': dc, 'ta_cuisine_match': 'extend'} if dc else {}),
                **({'fsa_rating_date': f['ratingDate']} if f.get('ratingDate') else {}),
            })
            print(f"ta-created: {f['name']} [{f['fhrs_id']}] via {o.get('name')} (sc={sc})")
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['ta'] = {'vintage': VINTAGE, 'rows_in_bbox': len(rows), 'matched': matched,
                  'hours_added': hours_added, 'diets_added': diets_added,
                  'cuisine_verdicts': cuisine_v, 'rating_bands': rating_bands,
                  'created': created}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'stale dump: {len(rows)} rows -> {matched} matched, +{hours_added} hours, +{diets_added} diets, cuisines {cuisine_v}, ratings {rating_bands}')


if __name__ == '__main__':
    main()
