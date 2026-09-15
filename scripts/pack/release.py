#!/usr/bin/env python3
"""Assemble a GitHub release from built packs: flatten <slug>.json assets.

  python3 release.py --tag pois-2026-Q4 --out dist/
  python3 release.py --tag pois-2026-Q4 --out dist/ --base-manifest <url|path>

--base-manifest unions with an already-published manifest (e.g. the current
release's manifest.json), so a single-area re-dispatch refreshes its own
packs without orphaning the others. New entries win per pack id; slugs are
area-derived and stable, so untouched packs keep resolving to their assets.
"""
import argparse, json, shutil, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / 'packs'


def load_base(spec):
    try:
        if spec.startswith(('http://', 'https://')):
            req = urllib.request.Request(spec, headers={'User-Agent': 'indiemaps-release/0.1'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        return json.load(open(spec))
    except Exception as e:
        print(f'base manifest unavailable ({e}) — assembling from local packs only')
        return {'packs': []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--out', default='dist')
    ap.add_argument('--base-manifest', default='',
                    help='URL or path of published manifest to union with (single-area dispatches)')
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
    if a.base_manifest:
        base = {p['id']: p for p in load_base(a.base_manifest).get('packs', [])}
        for x in assets:
            base[x['id']] = x  # rebuilt entries win
        assets = sorted(base.values(), key=lambda p: p['id'])
        print(f'union: {len(assets)} packs ({len([x for x in assets if x["id"] in {p["id"] for p in man["packs"]}])} rebuilt)')
    json.dump({'tag': a.tag, 'generated_at': man['generated_at'], 'packs': assets},
              open(out / 'manifest.json', 'w'), indent=1)
    print(f'{len(assets)} assets -> {out}')


if __name__ == '__main__':
    main()
