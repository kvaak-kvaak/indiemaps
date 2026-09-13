#!/usr/bin/env python3
"""Assemble a GitHub release from built packs: flatten <slug>.json assets.

  python3 release.py --tag pois-2026-Q4 --out dist/
"""
import argparse, json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / 'packs'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--out', default='dist')
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    man = json.load(open(PACKS / 'manifest.json'))
    assets = []
    for p in man['packs']:
        src = PACKS / p['id'] / 'pois.json'
        if not src.exists():
            print('missing pack file:', p['id'])
            continue
        slug = p['id'].replace('/', '-') + '.json'
        shutil.copy(src, out / slug)
        assets.append({**p, 'file': slug})
    json.dump({'tag': a.tag, 'generated_at': man['generated_at'], 'packs': assets},
              open(out / 'manifest.json', 'w'), indent=1)
    print(f'{len(assets)} assets -> {out}')


if __name__ == '__main__':
    main()
