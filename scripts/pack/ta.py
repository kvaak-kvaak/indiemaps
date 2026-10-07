#!/usr/bin/env python3
"""Stale-dump enrichment (committed filtered extract, keyless bulk read).

data/ta-extract.parquet is cut from the 1.08M-row c.2021 research dump
(UK nations + Finland, 24 columns — see docs/HANDOFF.md for the schema
rationale). Committed and versioned, so every builder (CI included) reads
identical bytes; sha recorded in meta.ta.

MATCH ONLY: rows that match nothing are discarded, and no POI is ever
created — resurrecting closures from stale data is exactly what this
stage must not do.

Staleness doctrine:
- Names/locations are match keys only, never written (pack names are
  current-verified; the dump is stale by design).
- Hours apply ONLY where the record has zero hours from any current
  source (OSM/ATP/site/NHS/Servicemap). Current hours always win by
  absence of competition.
- Cuisines are compared, never merged (ta_cuisines + agree/extend/conflict).
- Only affirmative dietary flags are stored (Y); N/empty render as unknown.
- Reviews become RECOMMEND INPUTS, not stars: rec_up/rec_down/rec_n feed
  the Laplace+decay formula at render (shared with Mangrove). Legacy rows
  enter undecayed; decay starts when fresh reviews arrive (no pre-decay
  cliff — see HANDOFF). Averages were discarded at extract build as
  ambiguous middle.
- Feature flags (wheelchair/dog/playground/live-music) stored
  affirmative-only, unrendered for now.
- All review-derived keys live under ta_* so OSM-compatible consumers
  strip them cleanly (no reviews carry hours in OSM's schema).

  python3 ta.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overture import toks, accept, nscore, dist_m, grid_index, nearby, norm_pc  # noqa

ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / 'data' / 'ta-extract.parquet'
# rec_vintage/rec_formula live in meta.ta (extract-level, single source of
# truth). The app computes decay at render from there; pins carry only
# the counts.
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
        # Committed extract: absent only if someone deleted it — loud skip.
        print(f'stale extract absent ({PARQUET}), skipping ta stage')
        meta = json.load(open(a.meta))
        meta['ta'] = {'skipped': 'extract absent',
                      'matched': 0}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        return
    import hashlib
    sha = hashlib.sha256(open(PARQUET, 'rb').read()).hexdigest()[:16]
    w, s, e, n = map(float, a.bbox.split(','))
    import duckdb
    t0 = time.time()
    db = duckdb.connect()
    # Empirical prior for five-level Bayesian ranking (stated openly in
    # meta.ta, never hidden): extract-global mean star value. Ranking-only —
    # the adjusted score is never displayed as an observed percentage.
    prior = db.execute(f"""SELECT (5*SUM(rec_stars[1])+4*SUM(rec_stars[2])+3*SUM(rec_stars[3])+2*SUM(rec_stars[4])+SUM(rec_stars[5]))/(1.0*SUM(rec_n))
    FROM read_parquet('{PARQUET}') WHERE rec_n>0""").fetchone()[0] or 4.0
    C = 10
    rows = db.execute(f"""SELECT restaurant_name, address, latitude, longitude,
      cuisines, vegetarian_friendly, vegan_options, gluten_free,
      original_open_hours, rec_stars, rec_n,
      feat_wheelchair, feat_dog, feat_play, feat_music
    FROM read_parquet('{PARQUET}')
    WHERE latitude BETWEEN {s} AND {n} AND longitude BETWEEN {w} AND {e}""").fetchall()
    cols = ['name', 'address', 'lat', 'lng', 'cuisines', 'veg', 'vegan', 'gf',
            'hours', 'rec_stars', 'rec_n',
            'feat_wheelchair', 'feat_dog', 'feat_play', 'feat_music']
    rows = [dict(zip(cols, r)) for r in rows if r[0] and r[2] is not None and r[3] is not None]
    print(f'stale dump rows in bbox: {len(rows)} ({time.time()-t0:.0f}s)')

    pois = json.load(open(a.pois))
    # clear previous enrichment (re-run safe)
    for p in pois:
        for k in ('ta_cuisines', 'ta_cuisine_match', 'ta_hours', 'ta_vegetarian',
                  'ta_vegan', 'ta_gluten_free', 'ta_rec_stars', 'ta_rec_n',
                  'ta_rec_bayes',
                  'ta_wheelchair', 'ta_dog', 'ta_play', 'ta_music',
                  'ta_rec_up', 'ta_rec_down', 'ta_rating', 'ta_reviews'):
            p.pop(k, None)
        p['sources'] = [s for s in p.get('sources', []) if s != 'ta']

    grid, cell = grid_index(rows, 'lat', 'lng') if rows else ({}, None)
    matched = hours_added = diets_added = feats_added = 0
    cuisine_v = {'agree': 0, 'extend': 0, 'conflict': 0}
    for p in pois:
        if p.get('category') not in ('restaurant', 'cafe', 'pub'):
            continue
        names = match_names(p)
        if not names:
            continue
        ppc = norm_pc(p.get('postcode'))
        best, bs = None, 0
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
                            bs, best = sc, o
        if best is None:
            continue
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
        # Recommend inputs: the full 5-step vector (weighted scoring needs
        # the excellent/very-good split — collapsed up/down cannot compute
        # it). rec_n is the overall tally (averages included in the
        # denominator, out of the weights). Decay multiplies per level at
        # render, armed on fresh inflow.
        try:
            stars = [int(x or 0) for x in (best.get('rec_stars') or [])][:5]
            while len(stars) < 5:
                stars.append(0)
        except (TypeError, ValueError):
            stars = [0, 0, 0, 0, 0]
        try:
            n = int(best.get('rec_n') or 0)
        except (TypeError, ValueError):
            n = 0
        if any(stars) or n:
            p['ta_rec_stars'] = stars
            p['ta_rec_n'] = n or sum(stars)
            # Five-level Bayesian ranking score (Miller-style, empirical
            # prior above). Orders pins and gates the badge; NEVER shown
            # as an observed percentage — cards show positive share.
            e5, v4, a3, p2, t1 = stars
            nn = n or sum(stars) or 1
            p['ta_rec_bayes'] = round((C * prior + 5 * e5 + 4 * v4 + 3 * a3 + 2 * p2 + t1) / (C + nn), 3)
        feats = False
        if best.get('feat_wheelchair'):
            p['ta_wheelchair'] = True
            feats = True
        if best.get('feat_dog'):
            p['ta_dog'] = True
            feats = True
        if best.get('feat_play'):
            p['ta_play'] = True
            feats = True
        if best.get('feat_music'):
            p['ta_music'] = True
            feats = True
        if feats:
            feats_added += 1
        if 'ta' not in p.get('sources', []):
            p['sources'].append('ta')
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['ta'] = {'extract': PARQUET.name, 'extract_sha': sha,
                  'rec_vintage': '2021-06-01', 'rec_formula': 'weighted-5step-v1',
                  'rec_prior_mean': round(prior, 3), 'rec_prior_c': C,
                  'rows_in_bbox': len(rows), 'matched': matched,
                  'hours_added': hours_added, 'diets_added': diets_added,
                  'feature_flags_added': feats_added,
                  'cuisine_verdicts': cuisine_v}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'stale dump: {len(rows)} rows -> {matched} matched, +{hours_added} hours, +{diets_added} diets, +{feats_added} feature flags, cuisines {cuisine_v}')


if __name__ == '__main__':
    main()
