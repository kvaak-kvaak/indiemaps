#!/usr/bin/env python3
"""Tile-bytes determinism check: compare two tippecanoe outputs by tile
content (not file bytes — tippecanoe embeds the output path in metadata,
which would fail the comparison spuriously).

  python3 check_determinism.py <a.mbtiles> <b.mbtiles>
Exits 0 when identical, 1 otherwise.
"""
import hashlib
import sqlite3
import sys


def sig(path):
    con = sqlite3.connect(path)
    h = hashlib.sha256()
    rows = con.execute(
        'SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles '
        'ORDER BY zoom_level, tile_column, tile_row')
    for zt, xt, yt, td in rows:
        h.update(bytes([zt]) + int(xt).to_bytes(4, 'big')
                 + int(yt).to_bytes(4, 'big') + bytes(td))
    return h.hexdigest()[:16]


if __name__ == '__main__':
    a, b = sig(sys.argv[1]), sig(sys.argv[2])
    print('deterministic tile bytes:', a == b, a)
    sys.exit(0 if a == b else 1)
