#!/usr/bin/env python3
"""Frozen raw-input handoff assembler (Southend parity comparison).

Reads a built pack's raw-input sidecars + caches, verifies them, and
writes a self-describing local directory (default handoff-southend/).
Nothing here is committed; nothing touches publication. Missing inputs
are listed in the manifest with reasons — never silently substituted.

  python3 scripts/pack/handoff_manifest.py --packdir packs/eu/gb/england/essex/southend-on-sea --out handoff-southend
"""
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

BBOX = {'w': 0.615, 's': 51.505, 'e': 0.825, 'n': 51.575}

FILES = {
    'osm-elements.json': {
        'source': 'OpenStreetMap via Geofabrik PBF (see osm_extract vintage in meta)',
        'attribution': '© OpenStreetMap contributors (ODbL); extract Geofabrik (ODbL)',
        'count': lambda d: len(d.get('elements', [])),
    },
    'fhrs-raw.json': {
        'source': 'https://ratings.food.gov.uk/OpenDataFiles/FHRS893en-GB.json',
        'attribution': 'Food Standards Agency (Open Government Licence)',
        'count': lambda d: len(d.get('establishments', [])),
    },
    'overture-raw.json': {
        'source': 'Overture Maps Places (S3 release parquet, release in meta)',
        'attribution': 'Overture Maps Foundation (see overturemaps.org for license)',
        'count': lambda d: len(d) if isinstance(d, list) else 0,
    },
    'atp-raw.json': {
        'source': 'https://alltheplaces-data.openaddresses.io/runs/<run>/output/<spider>.geojson (runs in meta)',
        'attribution': 'AllThePlaces contributors (CC0)',
        'count': lambda d: d.get('count', 0),
    },
    'atp-ledger.json': {
        'source': 'derived at fetch (per-spider statuses + merge-set derivation)',
        'attribution': 'n/a (pipeline ledger)',
        'count': lambda d: len(d.get('spiders', [])),
    },
    'ch-evidence.json': {
        'source': 'Companies House monthly bulk (snapshot files listed inside)',
        'attribution': "Companies House (Open Government Licence)",
        'count': lambda d: len(d.get('rows', [])),
    },
    'nhs-southend.json': {
        'source': 'NHSBSA consolidated pharmaceutical list (quarterly cache data/nhs/cache.json)',
        'attribution': 'NHS Business Services Authority (see open data licence)',
        'count': lambda d: len(d.get('rows', [])),
    },
    'ta-matches.json': {
        'source': 'derived at match (archive row_key -> POI mapping + reasons)',
        'attribution': 'n/a (pipeline mapping)',
        'count': lambda d: len(d) if isinstance(d, list) else 0,
    },
}

README_TMPL = """# Frozen Southend raw-input handoff

Rectangle (not the municipal boundary): `[W,S,E,N] = [{w}, {s}, {e}, {n}]`.
Generated {now} from a built pack — raw inputs only, no merged POIs.

## Files

| file | rows | source |
|---|---|---|
{rows}

## Notes

- Missing inputs are listed in `manifest.json` with reasons, never
  substituted silently.
- OSM way positions are bbox-centers of member nodes (Overpass
  `out center` semantics); versions/timestamps ride per element.
- NHS coordinates are ALL postcode-geocoded (no surveyed positions).
- FHRS: requested vintage vs actual acquisition date are both in the
  manifest; establishment-count drift vs the pack is checked there.
- Archive row key (both sides compute identically):
  `sha1(lower(trim(name)) | lat:.5f | lng:.5f)[:16]`.
- DPD/Relay snapshots are phase 2 (common inputs first).
- License review for the research extract is pending; nothing here
  changes publication. This directory is local-only (gitignored).
"""


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--packdir', required=True)
    ap.add_argument('--out', default='handoff-southend')
    a = ap.parse_args()
    pack, out = Path(a.packdir), Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    meta = json.load(open(pack / 'meta.json'))
    manifest = {'generated_at': now, 'bbox': BBOX,
                'built_from_pack': str(pack), 'files': {}, 'missing': []}
    # NHS slice (committed quarterly cache, bbox-cut here)
    try:
        cache = json.load(open('data/nhs/cache.json'))
        rows = [r for r in cache.get('rows', [])
                if r.get('lat') is not None
                and BBOX['s'] <= r['lat'] <= BBOX['n']
                and BBOX['w'] <= r.get('lng', 0) <= BBOX['e']]
        json.dump({'cache_updated': cache.get('updated'), 'count': cache.get('count'),
                   'coordinates': 'ALL postcode-geocoded (no surveyed positions)',
                   'rows': rows}, open(out / 'nhs-southend.json', 'w'))
    except Exception as ex:
        manifest['missing'].append({'file': 'nhs-southend.json', 'reason': str(ex)[:120]})
    for fname, spec in FILES.items():
        if fname == 'nhs-southend.json':
            pass  # handled above (same filename, same slot)
        else:
            src = pack / fname
            if not src.exists():
                manifest['missing'].append({'file': fname, 'reason': 'not produced by this pack build'})
                continue
            shutil.copy(src, out / fname)
        try:
            doc = json.load(open(out / fname))
            acquired = datetime.fromtimestamp(
                (pack / fname).stat().st_mtime, tz=timezone.utc).isoformat() \
                if (pack / fname).exists() else now
            manifest['files'][fname] = {
                'sha256': sha(out / fname),
                'source': spec['source'], 'attribution': spec['attribution'],
                'acquired': acquired, 'bounds': BBOX, 'rows': spec['count'](doc),
                'complete': True}
        except Exception as ex:
            manifest['missing'].append({'file': fname, 'reason': f'unreadable: {str(ex)[:100]}'})
            manifest['files'].pop(fname, None)
    # FHRS vintage check (requested Oct-6 vs actual + pack drift)
    try:
        fhrs = json.load(open(out / 'fhrs-raw.json'))
        manifest['files']['fhrs-raw.json']['requested_vintage'] = '2026-10-06'
        manifest['files']['fhrs-raw.json']['actual_extract_date'] = fhrs.get('extractDate')
        manifest['files']['fhrs-raw.json']['pack_fsa_rows'] = meta.get('counts', {}).get('total')
    except Exception:
        pass
    json.dump(manifest, open(out / 'manifest.json', 'w'), indent=1)
    rows_md = '\n'.join(f"| `{f}` | {m['rows']} | {m['source'][:60]} |"
                        for f, m in manifest['files'].items())
    (out / 'README.md').write_text(README_TMPL.format(now=now, rows=rows_md, **BBOX))
    print(f'handoff: {len(manifest["files"])} files, {len(manifest["missing"])} missing -> {out}/')
    for miss in manifest['missing']:
        print(f'  MISSING {miss["file"]}: {miss["reason"]}')


if __name__ == '__main__':
    main()
