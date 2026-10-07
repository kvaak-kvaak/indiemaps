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
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

UA = {'User-Agent': 'indiemaps-osmfetch/1.0 (+https://github.com/kvaak-kvaak/indiemaps)'}
NATIONAL = {'gb': 'https://download.geofabrik.de/europe/great-britain-latest.osm.pbf',
            'fi': 'https://download.geofabrik.de/europe/finland-latest.osm.pbf'}
# Mirror of osmQuery() tag classes in scripts/build.js — keep in sync.
# Nodes pull leisure; ways do not (Overpass has no leisure-way clause).
# Filtering happens in Python on exported elements, NOT via tags-filter:
# tags-filter drops untagged way member nodes, which silently deletes
# every building-mapped venue (measured twice: 320+ Southend ways lost).
AMENITY = set(('restaurant cafe pub bar nightclub fast_food ice_cream pharmacy '
               'doctors dentist cinema theatre arts_centre library place_of_worship '
               'clinic optician').split())
TOURISM = set('hotel guest_house hostel attraction museum gallery viewpoint'.split())
LEISURE = set('park nature_reserve miniature_golf sports_centre'.split())


def wanted(tags, is_way):
    if tags.get('amenity') in AMENITY:
        return True
    if 'shop' in tags:
        return True
    if tags.get('tourism') in TOURISM:
        return True
    if not is_way and tags.get('leisure') in LEISURE:
        return True
    return False


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
    return raw


def normalize_feature(ft, metalookup):
    """osmium-exported geojsonseq feature + OPL metadata -> Overpass-shaped
    element. Geometry+tags from the export; version/timestamp from OPL
    (the export carries no metadata). Missing metadata yields null and is
    counted loudly, never invented."""
    props = ft.get('properties') or {}
    tags = {k: v for k, v in props.items() if not k.startswith('@')}
    geom = ft.get('geometry') or {}
    gtype, coords = geom.get('type'), geom.get('coordinates') or []
    fid = str(ft.get('id', ''))
    # osmium ids: short prefixes (n316782455) with --add-unique-id=type_id
    if fid[:1] in ('n', 'w', 'r') and fid[1:].isdigit():
        otype = {'n': 'node', 'w': 'way', 'r': 'relation'}[fid[:1]]
        oid = int(fid[1:])
    elif '/' in fid:
        otype, num = fid.split('/', 1)
        try:
            oid = int(num)
        except (TypeError, ValueError):
            return None
    else:
        otype = 'node' if gtype == 'Point' else 'way'
        try:
            oid = int(fid)
        except (TypeError, ValueError):
            return None
    if otype == 'relation':
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
    ver, ts = metalookup.get((otype, oid), (None, None))
    if ver is None:
        ver = props.get('@version', props.get('version'))
        try:
            ver = int(ver) if ver is not None else None
        except (TypeError, ValueError):
            ver = None
    if ts is None:
        ts = props.get('@timestamp', props.get('timestamp'))
    return {'type': otype, 'id': oid, 'lat': lat, 'lon': lon,
            'tags': tags, 'timestamp': ts, 'version': ver}


OPL_PREFIX = re.compile(r'^([nwr])(\d+) v(\d+) .*? t(\S+)')


def read_opl_metadata(path):
    """{(type, id): (version, timestamp)} from `osmium cat -f opl`.
    Only the fixed prefix is parsed (immune to tag escaping); tags come
    from the geojsonseq export."""
    out = {}
    with open(path, errors='replace') as f:
        for line in f:
            m = OPL_PREFIX.match(line)
            if not m:
                continue
            otype = {'n': 'node', 'w': 'way', 'r': 'relation'}[m.group(1)]
            try:
                out[(otype, int(m.group(2)))] = (int(m.group(3)), m.group(4))
            except (TypeError, ValueError):
                continue
    return out


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
    national = ensure_national(Path(a.cache), a.country)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # Order is load-bearing: extract everything in bbox, locate ways
        # against the present nodes, export all, filter tags in Python.
        # osmium tags-filter drops untagged member nodes and silently
        # deletes building-mapped venues — never filter PBF-side.
        area_full = Path(tmp) / 'area-full.osm.pbf'
        sh('osmium', 'extract', '--overwrite', '-b',
           f'{w},{s},{e},{n}', '-o', str(area_full), str(national))
        area_loc = Path(tmp) / 'area-located.osm.pbf'
        sh('osmium', 'add-locations-to-ways', '--overwrite',
           '-o', str(area_loc), str(area_full))
        seq = Path(tmp) / 'area.geojsonseq'
        sh('osmium', 'export', '--overwrite', '--add-unique-id=type_id',
           '-f', 'geojsonseq', '-o', str(seq), str(area_loc))
        opl = Path(tmp) / 'area.opl'
        sh('osmium', 'cat', '--overwrite', '-f', 'opl', '-o', str(opl), str(area_loc))
        metalookup = read_opl_metadata(str(opl))
        print(f'osm_fetch: OPL metadata for {len(metalookup)} objects', flush=True)
        els, null_ts, skipped, tag_skipped = [], 0, 0, 0
        from collections import Counter
        geoms, rej = Counter(), Counter()
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
                props = ft.get('properties') or {}
                tags = {k: v for k, v in props.items() if not k.startswith('@')}
                gt = (ft.get('geometry') or {}).get('type')
                geoms[gt] += 1
                if not wanted(tags, gt != 'Point'):
                    tag_skipped += 1
                    if tags.get('name'):
                        rej[(gt, tags.get('amenity'), tags.get('shop'), tags.get('tourism'), tags.get('leisure'), tags.get('building'))] += 1
                    continue
                el = normalize_feature(ft, metalookup)
                if el is None:
                    skipped += 1
                    continue
                if el['timestamp'] is None:
                    null_ts += 1
                els.append(el)
    out = {'elements': els}
    json.dump(out, open(a.out, 'w'))
    print(f'osm_fetch: {len(els)} elements (tag-filtered {tag_skipped}, null-timestamp {null_ts}, skipped {skipped}) -> {a.out}', flush=True)
    print(f'osm_fetch: export geoms {dict(geoms)}; named-rejected sample {dict(list(rej.items())[:15])}', flush=True)
    if null_ts and null_ts == len(els):
        raise SystemExit('osm_fetch: EVERY element lacks a timestamp — export schema changed, refusing')


def osm_country(area_id):
    seg = (area_id.split('/') + ['', ''])[1].lower()
    return 'fi' if seg == 'fi' else 'gb'


if __name__ == '__main__':
    main()
