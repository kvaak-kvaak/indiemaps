#!/usr/bin/env python3
"""Fill a tile manifest with PMTiles build output (bytes + sha256).

  python3 fill_manifest.py <area-dir>
Expects manifest.json + pois.pmtiles in the directory.
"""
import datetime
import hashlib
import json
import sys
from pathlib import Path


def main():
    d = Path(sys.argv[1])
    m = json.load(open(d / 'manifest.json'))
    st = (d / 'pois.pmtiles').stat()
    h = hashlib.sha256()
    with open(d / 'pois.pmtiles', 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    m['built_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    m['archives']['pois_pmtiles'] = {
        'url': None, 'bytes': st.st_size, 'sha256': h.hexdigest(),
        'minzoom': 10, 'maxzoom': 16}
    json.dump(m, open(d / 'manifest.json', 'w'), indent=1)
    print('manifest filled:', st.st_size, 'bytes')


if __name__ == '__main__':
    main()
