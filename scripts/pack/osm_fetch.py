#!/usr/bin/env python3
"""OSM source via Geofabrik PBF (primary on CI) instead of live Overpass.

One national download per run (GB 2.2GB / FI 770MB, daily files, cached
under data/osm/), tags-filtered once with the EXACT tag set mirrored from
osmQuery() in scripts/build.js, then per-area bbox extract + export.
Emits Overpass-identical element JSON so everything downstream is
untouched (same schema: type/id/lat/lon/tags/timestamp/version; way
positions are bbox-centers, matching `out center` semantics).

Why: Overpass 504-storms fail dense tiles repeatedly (Camden 3x); PBF is
one polite bulk fetch per run, deterministic per extract vintage (stamped
in meta), and carries full way geometry + versions + timestamps, so the
touch/freshness logic is byte-identical in behavior.

Overpass stays behind --osm-source=overpass for fast local iteration.
Missing osmium binary degrades loudly to Overpass (local dev keeps
working); CI installs osmium-tool.

  python3 osm_fetch.py --country gb --bbox=W,S,E,N --out packdir/osm-elements.json [--cache data/osm]
"""
import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

UA = {'User-Agent': 'indiemaps-osmfetch/1.0 (+https://github.com/kvaak-kvaak/indiemaps)'}
NATIONAL = {'gb': 'https://download.geofabrik.de/europe/great-britain-latest.osm.pbf',
            'fi': 'https://download.geofabrik.de/europe/finland-latest.osm.pbf'}
# Mirror of osmQuery() tag classes in scripts/build.js — keep in sync.
# Any drift here is a coverage change; the Southend PBF-vs-Overpass proof
# diff guards it.
AMENITY = ('restaurant cafe pub bar nightclub fast_food ice_cream pharmacy '
           'doctors dentist cinema theatre arts_centre library place_of_worship '
           'clinic optician')
TOURISM = 'hotel guest_house hostel attraction museum gallery viewpoint'
LEISURE = 'park nature_reserve miniature_golf sports_centre'


def sh(*args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f'osmium failed: {" ".join(args)}\n{r.stderr[-2000:]}')
    return r


def ensure_national(cache, country):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    cache.mkdir(parents=True, exist_ok=True)
    (cache / '.gitignore').write_text('*\n')  # multi-GB extracts never commit
    raw = cache / f'{country}-latest.osm.pbf'
    stamp = cache / f'{country}.date'
    fresh = stamp.exists() and stamp.read_text().strip() == today
    if not raw.exists() or not fresh:
        # Fixed filenames + date sidecar: a restored cache from yesterday
        # re-downloads (never silently stale); same-day reruns reuse.
        print(f'osm_fetch: downloading {NATIONAL[country]} ({raw})', flush=True)
        req = urllib.request.Request(NATIONAL[country], headers=UA)
        with urllib.request.urlopen(req, timeout=600) as r, open(raw, 'wb') as f:
            shutil.copyfileobj(r, f, length=1024 * 1024)
        print(f'osm_fetch: {raw.stat().st_size / 1e9:.2f} GB', flush=True)
        stamp.write_text(today)
        filt = cache / f'{country}-shops.osm.pbf'
        if filt.exists():
            filt.unlink()  # tags-filter output belongs to the old extract
    filt = cache / f'{country}-shops.osm.pbf'
    if not filt.exists():
        amen = ','.join(AMENITY.split())
        tour = ','.join(TOURISM.split())
        leis = ','.join(LEISURE.split())
        print(f'osm_fetch: tags-filter {raw.name} -> {filt.name}', flush=True)
        sh('osmium', 'tags-filter', '--overwrite', '-R', '-o', str(filt), str(raw),
           f'n/amenity={amen}', 'n/shop', f'n/tourism={tour}', f'n/leisure={leis}',
           f'w/amenity={amen}', 'w/shop', f'w/tourism={tour}')
    return filt


def normalize_feature(ft):
    """osmium-exported geojsonseq feature -> Overpass-shaped element.
    Defensive: metadata keys vary (@-prefixed or plain); missing
    version/timestamp yields null and is counted loudly, never invented."""
    props = ft.get('properties') or {}
    tags = {k: v for k, v in props.items() if not k.startswith('@')}
    geom = ft.get('geometry') or {}
    gtype, coords = geom.get('type'), geom.get('coordinates') or []
    fid = str(ft.get('id', ''))
    if '/' in fid:
        otype, num = fid.split('/', 1)
    else:
        otype = 'node' if gtype == 'Point' else 'way'
        num = fid
    try:
        oid = int(num)
    except (TypeError, ValueError):
        return None
    if gtype == 'Point' and len(coords) >= 2:
        lon, lat = coords[0], coords[1]
    else:
        # bbox-center, matching Overpass `out center` semantics
        pts = coords[0] if (gtype == 'Polygon' and coords) else coords
        if not pts:
            return None
        xs = [p[0] for p in pts if len(p) >= 2]
        ys = [p[1] for p in pts if len(p) >= 2]
        if not xs:
            return None
        lon, lat = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ts = props.get('@timestamp', props.get('timestamp'))
    ver = props.get('@version', props.get('version'))
    try:
        ver = int(ver) if ver is not None else None
    except (TypeError, ValueError):
        ver = None
    return {'type': otype, 'id': oid, 'lat': lat, 'lon': lon,
            'tags': tags, 'timestamp': ts, 'version': ver}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--country', required=True, choices=['gb', 'fi'])
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--cache', default='data/osm')
    a = ap.parse_args()
    if not shutil.which('osmium'):
        raise SystemExit('osmium binary missing (CI installs osmium-tool)')
    w, s, e, n = a.bbox.split(',')
    filt = ensure_national(Path(a.cache), a.country)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        area = Path(tmp) / 'area.osm.pbf'
        sh('osmium', 'extract', '--overwrite', '-b',
           f'{w},{s},{e},{n}', '-o', str(area), str(filt))
        seq = Path(tmp) / 'area.geojsonseq'
        sh('osmium', 'export', '--overwrite', '--add-unique-id=type_id',
           '-f', 'geojsonseq', '-o', str(seq), str(area))
        with open(seq, 'rb') as dbg:
            sample = dbg.read(600)
        print(f'osm_fetch: export head: {sample!r}', flush=True)
        els, null_ts, skipped = [], 0, 0
        with open(seq) as f:
            for line in f:
                line = line.strip().lstrip('\x1e')
                if not line:
                    continue
                try:
                    ft = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                el = normalize_feature(ft)
                if el is None:
                    skipped += 1
                    continue
                if el['timestamp'] is None:
                    null_ts += 1
                els.append(el)
    out = {'elements': els}
    json.dump(out, open(a.out, 'w'))
    print(f'osm_fetch: {len(els)} elements (null-timestamp {null_ts}, skipped {skipped}) -> {a.out}', flush=True)
    if null_ts and null_ts == len(els):
        raise SystemExit('osm_fetch: EVERY element lacks a timestamp — export schema changed, refusing')


def osm_country(area_id):
    seg = (area_id.split('/') + ['', ''])[1].lower()
    return 'fi' if seg == 'fi' else 'gb'


if __name__ == '__main__':
    main()
