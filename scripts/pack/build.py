#!/usr/bin/env python3
"""POI pack builder — one command per area, fully unattended.

  python3 build.py --area eu/gb/england/essex/southend-on-sea
  python3 build.py --area ... --from atp          # resume at stage
  python3 build.py --area ... --sites             # opt-in: first-party site spider
  python3 build.py --all --skip-sites             # every area in areas.json
  python3 build.py --manifest                     # rebuild packs/manifest.json

Stages per pack (each cached; logs to packs/<id>/build.log):
  base     = FSA (England only) + OSM         -> pois.json, meta.json
  overture = contact backfill (websites/phones, keyless S3)
  nhs      = pharmacy hours+listings (England only, quarterly cache)
  atp      = AllThePlaces chain hours            -> merges into pois.json
  sites    = first-party site spider (opt-in)    -> site.json (+ merge if --sites)
  merge    = site-hours merge (runs when site.json exists)

Areas: areas.json (id -> name, bbox [w,s,e,n], fsa FHRS id or null).
Release layout (see docs/packs.md): packs/<id>/pois.json + manifest.json.
"""
import argparse, json, subprocess, sys, time
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
    stages = ['base', 'overture', 'nhs', 'atp', 'sites', 'merge']
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

    m = json.load(open(meta))
    pois_data = json.load(open(pois))
    n = len(pois_data)
    hours = sum(1 for p in pois_data
                if p.get('opening_hours_osm') or p.get('atp_hours')
                or p.get('site_hours') or p.get('nhs_hours'))
    # refresh aggregate counts (later stages add POIs/hours after base wrote them)
    m['counts'] = {
        'total': n,
        'fsa_only': sum(1 for p in pois_data if p.get('sources') == ['fsa']),
        'merged': sum(1 for p in pois_data if 'fsa' in p.get('sources', []) and 'osm' in p.get('sources', [])),
        'osm_only': sum(1 for p in pois_data if p.get('sources') == ['osm']),
        'nhs_added': sum(1 for p in pois_data if p.get('sources') == ['nhs']),
        'with_hours': hours,
    }
    json.dump(m, open(meta, 'w'), indent=1)
    log(packdir, f'DONE: {n} POIs, {hours} with hours')
    return {'id': area_id, 'hours': hours, 'total': n}


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
            'stages_ok': stages_ok,
            'sources': {k: v for k, v in m.items()
                        if k in ('fsa_extract_date', 'overture', 'nhs', 'atp', 'site')},
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
                    choices=['base', 'overture', 'nhs', 'atp', 'sites', 'merge'])
    ap.add_argument('--only',
                    choices=['base', 'overture', 'nhs', 'atp', 'sites', 'merge'])
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
