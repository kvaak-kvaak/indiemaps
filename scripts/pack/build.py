#!/usr/bin/env python3
"""POI pack builder — one command per area, fully unattended.

  python3 build.py --area eu/gb/england/essex/southend-on-sea
  python3 build.py --area ... --from atp          # resume at stage
  python3 build.py --area ... --sites             # opt-in: first-party site spider
  python3 build.py --all --skip-sites             # every area in areas.json
  python3 build.py --manifest                     # rebuild packs/manifest.json

Stages per pack (each cached; logs to packs/<id>/build.log):
  base     = FSA (England only) + OSM         -> pois.json, meta.json
  servicemap = Helsinki municipal units (servicemap_muni areas only)
  overture = contact backfill (websites/phones, keyless S3)
  nhs      = pharmacy hours+listings (England only, quarterly cache)
  atp      = AllThePlaces chain hours            -> merges into pois.json
  sites    = first-party site spider (opt-in)    -> site.json (+ merge if --sites)
  merge    = site-hours merge (runs when site.json exists)
  ta       = stale-dump enrichment, match-only ("ta" areas only:
             cuisines compare, gap hours, dietary flags, recommend inputs)

Areas: areas.json (id -> name, bbox [w,s,e,n], fsa FHRS id or null).
Release layout (see docs/packs.md): packs/<id>/pois.json + manifest.json.
"""
import argparse, json, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / 'packs'
AREAS = json.load(open(Path(__file__).parent / 'areas.json'))


def log(packdir, msg):
    line = f'{datetime.now(timezone.utc):%H:%M:%S} {msg}'
    print(line, flush=True)
    with open(packdir / 'build.log', 'a') as f:
        f.write(line + '\n')


def run(cmd, packdir):
    log(packdir, '$ ' + ' '.join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    with open(packdir / 'build.log', 'a') as f:
        f.write(r.stdout[-6000:] + r.stderr[-3000:])
    if r.returncode != 0:
        log(packdir, f'STAGE FAILED (exit {r.returncode})')
        sys.exit(1)


def mark_stage(packdir, meta_path, stage):
    """Record completed stages in meta.json so partial packs are visible."""
    try:
        m = json.load(open(meta_path))
        m.setdefault('stages_ok', [])
        if stage not in m['stages_ok']:
            m['stages_ok'].append(stage)
        json.dump(m, open(meta_path, 'w'), indent=1)
    except Exception as e:
        log(packdir, f'could not mark stage {stage}: {e}')


def build(area_id, args):
    if area_id not in AREAS:
        sys.exit(f'unknown area {area_id!r} (see areas.json)')
    a = AREAS[area_id]
    packdir = PACKS / area_id
    packdir.mkdir(parents=True, exist_ok=True)
    bbox = ','.join(map(str, a['bbox']))
    pois, meta = str(packdir / 'pois.json'), str(packdir / 'meta.json')
    stages = ['base', 'verify_positions', 'hazard_roads', 'servicemap', 'ch', 'overture', 'nhs', 'atp', 'sites', 'merge', 'ta']
    if args.only:
        stages = [args.only]
    elif args.from_stage:
        stages = stages[stages.index(args.from_stage):]
    log(packdir, f'=== pack {area_id} ({a["name"]}) stages={stages} ===')

    if 'base' in stages:
        cmd = ['node', 'scripts/build.js', f'--bbox={bbox}', '--pois', pois, '--meta', meta]
        cmd += ['--fsa', str(a['fsa']) if a.get('fsa') else 'none']
        run(cmd, packdir)
        mark_stage(packdir, meta, 'base')
    if 'verify_positions' in stages:
        # No network: attested + premises merges on pack data only.
        # Runs before ch so Companies House matching sees true positions.
        run(['python3', 'scripts/pack/verify_positions.py',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'verify_positions')
    if 'hazard_roads' in stages:
        # Coastal interpolation-hazard detector (cached coastline fetch).
        # Runs after verify so merged (surveyed-position) records are
        # exempt by construction.
        run(['python3', 'scripts/pack/hazard_roads.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'hazard_roads')
    if 'servicemap' in stages and a.get('servicemap_muni'):
        run(['python3', 'scripts/pack/servicemap.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta,
             '--municipality', a['servicemap_muni']], packdir)
        mark_stage(packdir, meta, 'servicemap')
    if 'ch' in stages:
        run(['python3', 'scripts/pack/companies.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'ch')
    if 'overture' in stages:
        # NOTE: bbox passed as --bbox=<v> (equals form): argparse treats a
        # space-separated negative longitude as a flag and aborts. Custom
        # parsers (build.js/atp.js) tolerate both forms; keep '=' everywhere.
        run(['python3', 'scripts/pack/overture.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'overture')
    if 'nhs' in stages:
        run(['python3', 'scripts/pack/nhs.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'nhs')
    if 'atp' in stages:
        run(['node', 'scripts/atp.js', f'--bbox={bbox}', '--pois', pois, '--meta', meta,
             '--extract', str(packdir / 'atp-extract.json'),
             '--cache', str(ROOT / 'data' / 'atp' / 'raw')], packdir)
        mark_stage(packdir, meta, 'atp')
    if 'sites' in stages and (args.sites or args.sites_all or args.only == 'sites'):
        pois_data = json.load(open(pois))
        food = [p for p in pois_data
                if p.get('website') and p['category'] in ('restaurant', 'cafe', 'pub')]
        if args.sites_all:
            urls = food
        else:
            # residual only: chains are covered better by ATP; overlaps only
            # buy conflict detection, which is opt-in via --sites-all
            urls = [p for p in food if not (p.get('opening_hours_osm') or p.get('atp_hours'))]
        urls = [{'name': p['name'], 'website': p['website'],
                 'postcode': p.get('postcode'), 'id': p['id']} for p in urls]
        site_in = packdir / 'site-in.json'
        json.dump(urls, open(site_in, 'w'))
        log(packdir, f'site spider: {len(urls)} food sites with URLs')
        if urls:
            slug = area_id.replace('/', '-')
            run(['python3', 'scripts/site-hours/site-hours.py', '--in', str(site_in),
                 '--out', str(packdir / 'site.json'), '--workers', '24',
                 '--resume', '--max-age', '80',  # refresh quarterly, resume crashes freely
                 '--dead-cache', str(ROOT / 'data' / 'dead-domains' / f'{slug}.json')], packdir)
            mark_stage(packdir, meta, 'sites')
    if 'merge' in stages and (packdir / 'site.json').exists():
        run(['node', 'scripts/merge-site.js', '--in', str(packdir / 'site.json'),
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'merge')
    if 'ta' in stages and a.get('ta'):
        run(['python3', 'scripts/pack/ta.py', f'--bbox={bbox}',
             '--pois', pois, '--meta', meta], packdir)
        mark_stage(packdir, meta, 'ta')

    m = json.load(open(meta))
    pois_data = json.load(open(pois))
    pois_data, quarantined = apply_quarantine(pois_data, packdir)
    n = len(pois_data)
    hours = sum(1 for p in pois_data
                if p.get('opening_hours_osm') or p.get('atp_hours')
                or p.get('site_hours') or p.get('nhs_hours')
                or p.get('sm_hours') or p.get('ta_hours'))
    # refresh aggregate counts (later stages add POIs/hours after base wrote them)
    prev = m.get('counts', {})
    m['counts'] = {
        'total': n,
        'fsa_only': sum(1 for p in pois_data if p.get('sources') == ['fsa']),
        'merged': sum(1 for p in pois_data if 'fsa' in p.get('sources', []) and 'osm' in p.get('sources', [])),
        'osm_only': sum(1 for p in pois_data if p.get('sources') == ['osm']),
        'nhs_added': sum(1 for p in pois_data if p.get('sources') == ['nhs']),
        'ch_added': sum(1 for p in pois_data if p.get('sources') == ['ch']),
        'with_hours': hours,
        # base-written diagnostics survive the recount:
        'osm_nodes_pulled': prev.get('osm_nodes_pulled'),
        'osm_raw_response': prev.get('osm_raw_response'),
        'fsa_repinned': prev.get('fsa_repinned', 0),
        'fsa_duplicates_linked': prev.get('fsa_duplicates_linked', 0),
        'fsa_duplicates_queued': prev.get('fsa_duplicates_queued', 0),
        'osm_duplicates_merged': sum(
            1 for p in pois_data if p.get('osm_absorbed')),
    }
    stale = flag_stale_occupants(pois_data, packdir)
    m['counts']['stale_flagged'] = stale
    approx = flag_position_approx(pois_data)
    m['counts']['position_approx'] = approx
    # Orphan freshness rule (user decision): OSM-sourced records with no
    # FSA/CH/Servicemap corroboration stay visible iff touched within 6
    # months; older or untimestamped hide (fail closed — unassessable
    # freshness is not freshness). Same middle-path flag, distinct reason
    # so audits separate stale orphans from batch misses. Runs AFTER all
    # other flaggers so nothing re-pops these flags.
    now = datetime.now(timezone.utc)
    m['counts']['orphan_hidden'] = flag_stale_orphans(pois_data, now)
    m['counts']['quarantined'] = quarantined
    kept = flag_keep_evidence(pois_data, now)
    m['counts']['kept_by_rule'] = kept
    m['weights'] = area_weights(area_id, pois_data)
    assert_position_invariants(pois_data, m, packdir)
    json.dump(pois_data, open(pois, 'w'), indent=1)
    json.dump(m, open(meta, 'w'), indent=1)
    log(packdir, f'DONE: {n} POIs, {hours} with hours')
    return {'id': area_id, 'hours': hours, 'total': n}


def _norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9åäö ]', ' ', (s or '').lower())).strip()


def _hn(addr):
    """Premises number: leading ('18, Stoke…', '66-68 High St') else first
    post-comma ('The Slug And Lettuce, 6 - 8 Southchurch Road'). FSA lines
    often lead with the premises name, so leading-only misses them."""
    m = re.match(r'\s*(\d+[a-z]?)', addr or '', re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r',\s*(\d+[a-z]?)\b', addr or '')
    return m.group(1).lower() if m else None


def _same_brand(a, b):
    ta = {w for w in _norm(a).split() if len(w) >= 6}
    if any(w in {w for w in _norm(b).split() if len(w) >= 6} for w in ta):
        return True
    # Short distinctive tokens ('abi' in ABI Convenience Store vs ABI Foods):
    # same-brand short names must count as agreement, or current occupants
    # get flagged stale. Category/town words excluded (same flaw class as
    # the Swagger mis-merge).
    stop = {'and', 'the', 'of', 'pub', 'bar', 'inn', 'cafe', 'restaurant',
            'hotel', 'shop', 'store', 'food', 'southend', 'Leigh', 'london',
            'high', 'street', 'road', 'sea', 'old', 'new', 'north', 'south',
            'east', 'west', 'hackney', 'essex'}
    sa = {w for w in _norm(a).split() if len(w) >= 3 and w not in stop}
    sb = {w for w in _norm(b).split() if len(w) >= 3 and w not in stop}
    if sa & sb:
        return True
    # Exact equality short-circuits everything ('mu' vs 'MU' — base tokens
    # drop ≤2-letter words, so short names can never merge; they still
    # must not be flagged as different occupants).
    na, nb = _norm(a), _norm(b)
    if na and na == nb:
        return True
    # Brand-prefix with generic remainder ('Co-op Food' vs 'Co-op'):
    # same brand, not a rename.
    for x, y in ((na, nb), (nb, na)):
        if len(y) >= 4 and x.startswith(y + ' ') and all(
                w in stop or len(w) < 3 for w in x[len(y) + 1:].split()):
            return True
    ca, cb = na.replace(' ', ''), nb.replace(' ', '')
    return min(len(ca), len(cb)) >= 4 and ca == cb


def osm_touch_fresh(p, now, days=182):
    """Shared freshness helper (single threshold site — orphan rule and
    keep-evidence both read this, never separate constants). True iff the
    record carries a parseable OSM touch timestamp within `days`."""
    try:
        if p.get('osm_touched'):
            touched = datetime.fromisoformat(
                p['osm_touched'].replace('Z', '+00:00'))
            return (now - touched).days < days
    except (ValueError, TypeError):
        pass
    return False


def apply_quarantine(pois_data, packdir):
    """User-decided, fail-closed: listed ids leave the pack no matter which
    stage produced them — creation paths refuse them first (see
    companies.py, atp.js), this is the backstop. Loud + counted."""
    try:
        qids = json.load(open(ROOT / 'data' / 'quarantine.json')).get('ids', {})
    except Exception:
        qids = {}
    quarantined = [p['id'] for p in pois_data if p.get('id') in qids]
    if quarantined:
        pois_data = [p for p in pois_data if p.get('id') not in qids]
        print(f'quarantine: dropped {quarantined}')
        log(packdir, f'quarantine: dropped {quarantined}')
    return pois_data, quarantined


def flag_keep_evidence(pois_data, now):
    """Keep-evidence composition (ported from the verified sibling build):
    every rendered pin must carry at least one leg, each recorded with rule
    + human meaning and rendered in the detail panel. Legs: ATP contributor,
    FSA-linked, CH-corroborated, recent OSM touch, NHS-listed, Servicemap
    record. Hidden-tier pins (hazard, stale-orphan) keep whatever evidence
    they have — evidence explains, hiding decides. Re-run safe."""
    kept = {'atp': 0, 'fsa': 0, 'ch': 0, 'recent_osm': 0, 'nhs': 0,
            'servicemap': 0}
    for p in pois_data:
        s = p.get('sources', []) or []
        ev = []
        if 'atp' in s:
            ev.append({'rule': 'atp-contributor',
                       'meaning': 'Chain-published data via AllThePlaces'})
        if 'fsa' in s:
            ev.append({'rule': 'fsa-linked',
                       'meaning': 'Food Standards Agency open data — '
                                  'registration evidence, not proof of opening'})
        if 'ch' in s or p.get('ch_number'):
            ev.append({'rule': 'ch-corroborated',
                       'meaning': 'Companies House: incorporated business, '
                                  'uninspected'})
        if 'osm' in s and osm_touch_fresh(p, now):
            ev.append({'rule': 'recent-osm-touch',
                       'meaning': 'Map record touched within six months — '
                                  'retention, not proof of opening'})
        if 'nhs' in s:
            ev.append({'rule': 'nhs-listed',
                       'meaning': 'NHS listed pharmacy (England)'})
        if 'servicemap' in s:
            ev.append({'rule': 'servicemap-record',
                       'meaning': 'City of Helsinki Service Map (CC BY 4.0)'})
        if ev:
            p['keep_evidence'] = ev
            # Tier (coarse, capped): corroborating-leg count, never a
            # quality verdict. Browse sorts by it; search does not.
            p['tier'] = min(len(ev), 3)
            for e in ev:
                key = {'atp-contributor': 'atp', 'fsa-linked': 'fsa',
                       'ch-corroborated': 'ch', 'recent-osm-touch': 'recent_osm',
                       'nhs-listed': 'nhs',
                       'servicemap-record': 'servicemap'}[e['rule']]
                kept[key] += 1
        else:
            p.pop('keep_evidence', None)
            p['tier'] = 0
    return kept


def area_weights(area_id, pois_data):
    """Weighting (restored v1, lean): per-pack honesty flag + auto
    OSM-hours signal + tier histogram.

    The OSM hours-tagging rate (fraction of OSM-sourced records carrying
    opening_hours_osm) is computable everywhere with no ground truth, and
    measures hours-trust ONLY — never existence/recall. Country preferences
    come from data/source-weights.yaml (hand table, n=2 measured); missing
    PyYAML or missing country degrades to defaults + verified:false, loudly.
    Weights settle enrichment ties only, never creation (see YAML invariant).
    """
    osm_sourced = [p for p in pois_data if 'osm' in p.get('sources', [])]
    rate = (sum(1 for p in osm_sourced if p.get('opening_hours_osm'))
            / len(osm_sourced)) if osm_sourced else 0.0
    seg = (area_id.split('/') + ['', ''])[1]
    country = {'gb': 'GB', 'fi': 'FI'}.get(seg.lower(), seg.upper() or '??')
    table, version, entry = {}, 0, None
    try:
        import yaml
        table = yaml.safe_load(open(ROOT / 'data' / 'source-weights.yaml')) or {}
        version = table.get('version', 0)
        entry = (table.get('countries') or {}).get(country)
    except Exception as e:
        print(f'weights: YAML unreadable ({str(e)[:60]}), defaults + verified:false')
    tiers = {}
    for p in pois_data:
        tiers[p.get('tier', 0)] = tiers.get(p.get('tier', 0), 0) + 1
    out = {'table_version': version, 'country': country,
           'verified': bool(entry and entry.get('verified')),
           # YAML dates deserialize to date objects — stringify so meta.json
           # stays plain-JSON serializable.
           'spot_checks': json.loads(json.dumps((entry or {}).get('spot_checks', []),
                                                default=str)),
           'osm_hours_rate': round(rate, 3),
           'tier_histogram': tiers,
           'hours_order': ((entry or {}).get('hours')
                           or (table.get('defaults') or {}).get('hours', []))}
    print(f"weights: {country} verified={out['verified']} osm_hours_rate={out['osm_hours_rate']} tiers={tiers}")
    return out


def assert_position_invariants(pois_data, m, packdir):
    """Regression gate (must-improve-or-fail): position-honesty invariants
    that fail the area build loudly. Stateless per pack — no baselines.
    - Every batch-precision pin without OSM corroboration carries
      position_approx (the flag repair class: silent precision claims).
    - No provisional_creation records anywhere (creation rights are still
      barred project-wide; any future creation path must update this gate
      deliberately, never slip past it). APPROVED PATH: 'atp-chain'
      (chain-spider provenance + same-matcher anti-duplicate, user-approved
      2026-10-06) — anything else still fails.
    - Every verified_position merge has a matching meta.verify_positions
      entry (auditable moves only).
    - Every position_hazard record names its triggering road class."""
    bad_approx = [p['id'] for p in pois_data
                  if p.get('geo_precision') in ('fsa', 'postcode')
                  and 'osm' not in (p.get('sources', []) or [])
                  and not p.get('position_approx')]
    if bad_approx:
        raise SystemExit(f'POSITION GATE: {len(bad_approx)} batch pins lack '
                         f'position_approx, e.g. {bad_approx[:5]}')
    prov = [p['id'] for p in pois_data
            if p.get('provisional_creation')
            and p.get('provisional_creation') != 'atp-chain']
    if prov:
        raise SystemExit(f'POSITION GATE: {len(prov)} provisional creations '
                         f'without an approved creation path, e.g. {prov[:5]}')
    logged = {(e.get('fsa'), e.get('osm')) for e in
              (m.get('verify_positions') or {}).get('attested_merges', [])
              + (m.get('verify_positions') or {}).get('premises_merges', [])}
    unlogged = [p['id'] for p in pois_data if p.get('verified_position')
                and (p['id'], p.get('verified_from')) not in logged]
    if unlogged:
        raise SystemExit(f'POSITION GATE: {len(unlogged)} verified merges '
                         f'without meta audit entries, e.g. {unlogged[:5]}')
    haz = [p['id'] for p in pois_data if p.get('position_hazard')
           and not (p.get('hazard_road') or {}).get('class')]
    if haz:
        raise SystemExit(f'POSITION GATE: {len(haz)} hazard records without '
                         f'road class, e.g. {haz[:5]}')
    # Every SHOWN pin carries keep evidence (shown = neither hazard-hidden
    # nor stale-orphan-hidden, mirroring the server predicate). A shown
    # pin with no leg is an explainability bug: fail loudly, never ship it.
    bare = [p['id'] for p in pois_data
            if not p.get('position_hazard')
            and p.get('unresolved_why') != 'stale-orphan'
            and not p.get('keep_evidence')]
    if bare:
        raise SystemExit(f'POSITION GATE: {len(bare)} shown pins without '
                         f'keep evidence, e.g. {bare[:5]}')
    log(packdir, f'position gate: OK '
                  f'({m.get("counts", {}).get("position_approx", 0)} approx, '
                  f'{sum(1 for p in pois_data if p.get("position_hazard"))} hazard)')


def flag_stale_orphans(pois_data, now):
    """User-decided orphan rule: an OSM-sourced record with no FSA/CH/
    Servicemap corroboration renders iff its OSM object was touched within
    182 days; older or untimestamped records hide (fail closed).
    Same middle-path flag as batch misses, reason 'stale-orphan' so audits
    separate the two populations. User-approved 2026-10-06: ANY
    chain-published (ATP) match exempts — the chain confirms the store,
    regardless of match method. Re-run safe (clears first)."""
    n = 0
    for p in pois_data:
        p.pop('unresolved_why', None)
        s = p.get('sources', []) or []
        if 'osm' not in s or any(x in s for x in ('fsa', 'ch', 'servicemap', 'atp')):
            continue
        if osm_touch_fresh(p, now):
            continue
        n += 1
        p['position_unresolved'] = True
        p['unresolved_why'] = 'stale-orphan'
    return n


def flag_position_approx(pois_data):
    """Honest precision for single-source FSA pins (measured: same-postcode
    rows share one batch geocode, members up to ~1km off). A record whose
    only position source is FSA is postcode-area accurate, never
    premises-accurate — flagged for display, never moved, never hidden.
    Verified/proximity-merged records (OSM position) are untouched.
    Re-run safe (clears first)."""
    n = 0
    for p in pois_data:
        p.pop('position_approx', None)
        if p.get('geo_precision') in ('fsa', 'postcode') \
                and 'osm' not in (p.get('sources', []) or []):
            p['position_approx'] = True
            n += 1
    return n


def flag_stale_occupants(pois_data, packdir):
    """Stale-occupant flags (Slug pattern): an OSM-only food/shop record at
    the exact premises (same postcode + housenumber) of a current FSA or
    Servicemap occupant under a dissimilar name is the previous tenant, not
    a second business. Flagged, linked, de-emphasized downstream — never
    deleted. Name-agreeing orphans are merge-misses (matcher phase), not
    staleness: left alone. Re-run safe (clears first)."""
    for p in pois_data:
        p.pop('superseded_by', None)
        if isinstance(p.get('supersedes'), list):
            del p['supersedes']
    ors = [p for p in pois_data if set(p.get('sources', [])) <= {'osm', 'ta'}
           and p.get('category') in ('restaurant', 'cafe', 'pub', 'shopping', 'services')]
    occ = [p for p in pois_data if 'fsa' in p.get('sources', []) or 'servicemap' in p.get('sources', [])]
    # Exactly-one-occupant guard: food courts/markets host several current
    # vendors at one address — flagging there would be wrong. Shared sites
    # across use-classes (Kwik Fit + Pallavas) are excluded by the category
    # agreement check below, not by deletion.
    occ_count = {}
    for q in occ:
        qpc = (q.get('postcode') or '').replace(' ', '').lower()
        qhn = _hn(q.get('address'))
        if qpc and qhn:
            occ_count[(qpc, qhn)] = occ_count.get((qpc, qhn), 0) + 1
    # Category agreement is by trade family, not exact label: a pub becoming
    # a restaurant (Slug and Lettuce -> Skylahs Bar) is the same hospitality
    # trade and flags correctly; a car repair shop sharing the site with a
    # restaurant (Kwik Fit + Pallavas) is a different use-class and skips.
    FOOD = {'restaurant', 'cafe', 'pub'}
    n = 0
    for o in ors:
        opc = (o.get('postcode') or '').replace(' ', '').lower()
        ohn = _hn(o.get('address'))
        if not opc or not ohn:
            continue
        if occ_count.get((opc, ohn), 0) != 1:
            continue
        for q in occ:
            if q['id'] == o['id']:
                continue
            if (q.get('postcode') or '').replace(' ', '').lower() != opc:
                continue
            if _hn(q.get('address')) != ohn:
                continue
            oc, qc = o.get('category'), q.get('category')
            if not (oc == qc or (oc in FOOD and qc in FOOD)):
                continue  # different use-class, possibly shared site
            if _same_brand(o.get('name'), q.get('name')):
                continue  # agreement = possible merge-miss, not staleness
            o['superseded_by'] = {'id': q['id'], 'name': q.get('name')}
            q.setdefault('supersedes', [])
            if not any(x.get('id') == o['id'] for x in q['supersedes']):
                q['supersedes'].append({'id': o['id'], 'name': o.get('name')})
            n += 1
            log(packdir, f"stale occupant: {o.get('name')} [{o['id']}] previously at {q.get('name')} [{q['id']}] premises")
            break
    return n


def manifest():
    packs = []
    for area_id, a in AREAS.items():
        packdir = PACKS / area_id
        meta_f, pois_f = packdir / 'meta.json', packdir / 'pois.json'
        if not (meta_f.exists() and pois_f.exists()):
            continue
        m = json.load(open(meta_f))
        counts = m.get('counts', {})
        stages_ok = m.get('stages_ok', [])
        packs.append({
            'id': area_id, 'name': a['name'], 'bbox': a['bbox'],
            'file': f'{area_id.split("/")[-1]}.json',
            'bytes': pois_f.stat().st_size,
            'total': counts.get('total'), 'built_at': m.get('built_at'),
            'complete': all(s in stages_ok for s in ('base', 'overture', 'nhs', 'atp')),
            'verified': (m.get('weights') or {}).get('verified', False),
            'stages_ok': stages_ok,
            'sources': {k: v for k, v in m.items()
                        if k in ('fsa_extract_date', 'overture', 'nhs', 'atp', 'site', 'servicemap', 'ta', 'ch')},
        })
    man = {'generated_at': datetime.now(timezone.utc).isoformat(),
           'packs': sorted(packs, key=lambda p: p['id'])}
    json.dump(man, open(PACKS / 'manifest.json', 'w'), indent=1)
    print(f'manifest: {len(packs)} packs')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--area')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--from', dest='from_stage',
                    choices=['base', 'verify_positions', 'hazard_roads', 'servicemap', 'ch', 'overture', 'nhs', 'atp', 'sites', 'merge', 'ta'])
    ap.add_argument('--only',
                    choices=['base', 'verify_positions', 'hazard_roads', 'servicemap', 'ch', 'overture', 'nhs', 'atp', 'sites', 'merge', 'ta'])
    ap.add_argument('--sites', action='store_true',
                    help='site spider, residual only (POIs with no OSM/ATP hours)')
    ap.add_argument('--sites-all', action='store_true',
                    help='site spider over all food sites incl. covered ones (conflict monitoring)')
    ap.add_argument('--skip-sites', action='store_true')
    ap.add_argument('--manifest', action='store_true')
    args = ap.parse_args()
    if args.manifest:
        return manifest()
    ids = list(AREAS) if args.all else ([args.area] if args.area else [])
    if not ids:
        sys.exit('give --area ID, --all, or --manifest')
    failed = []
    for i, aid in enumerate(ids):
        t0 = time.time()
        try:
            build(aid, args)
        except SystemExit as e:
            # per-area isolation: one flaky area must not kill the run,
            # but the run must not pretend it succeeded either
            failed.append(aid)
            print(f'{aid}: FAILED ({e})')
        print(f'{aid} done in {time.time()-t0:.0f}s ({i+1}/{len(ids)})')
    if failed:
        print(f'\nFAILED AREAS ({len(failed)}):')
        for aid in failed:
            print(f'  {aid}')
        print('re-run with: --areas "' + ' '.join(failed) + '"')
        sys.exit(1)


if __name__ == '__main__':
    main()
