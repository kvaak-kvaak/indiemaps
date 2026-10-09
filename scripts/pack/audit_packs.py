#!/usr/bin/env python3
"""Release-gate audit across all built packs (read-only).

One table: totals, hidden tiers, creations, enrichment, failures, and the
two hard invariants (no shown pin without keep evidence, no unapproved
provisional creations). Exit 1 on any violation — this table IS the
release gate for the 64-pack cut.

  python3 scripts/pack/audit_packs.py [--packs packs]
"""
import argparse, json, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--packs', default='packs')
    a = ap.parse_args()
    root = Path(a.packs)
    rows, bad = [], 0
    for meta_f in sorted(root.glob('*/meta.json')) + sorted(root.glob('*/*/meta.json')) + \
            sorted(root.glob('*/*/*/meta.json')) + sorted(root.glob('*/*/*/*/meta.json')) + \
            sorted(root.glob('*/*/*/*/*/meta.json')):
        area = str(meta_f.parent.relative_to(root))
        pois_f = meta_f.parent / 'pois.json'
        if not pois_f.exists():
            continue
        m = json.load(open(meta_f))
        p = json.load(open(pois_f))
        c = m.get('counts', {})
        atp = m.get('atp', {})
        hid = sum(1 for x in p if x.get('position_hazard') or x.get('unresolved_why') == 'stale-orphan')
        bare = [x['id'] for x in p
                if not x.get('position_hazard')
                and x.get('unresolved_why') != 'stale-orphan'
                and not x.get('keep_evidence')]
        prov = [x['id'] for x in p if x.get('provisional_creation')
                and x.get('provisional_creation') not in ('atp-chain', 'ov-direct')]
        w = m.get('weights', {})
        rows.append({
            'area': area.split('/')[-1], 'total': len(p), 'hidden': hid,
            'atp_created': atp.get('created', '?'),
            'supp': len(atp.get('supplemented', {})),
            'failed': len(atp.get('failed_spiders', [])),
            'ta': (m.get('ta') or {}).get('matched', '?'),
            'prh': (m.get('prh') or {}).get('matched', '-'),
            'chain': (m.get('chains_fi') or {}).get('matched', '-'),
            'bare': len(bare), 'prov': len(prov),
            'verified': w.get('verified', '?'),
            'complete': m.get('atp', {}).get('complete', '?'),
        })
        if bare or prov:
            bad += 1
            print(f"VIOLATION {area}: bare={bare[:3]} prov={prov[:3]}")
    print(f"{'area':28s} {'total':>6s} {'hidden':>6s} {'cre':>4s} {'sup':>3s} {'fail':>4s} {'ta':>5s} {'prh':>4s} {'chn':>4s} {'bare':>4s} {'prov':>4s} {'verif':>5s}")
    for r in rows:
        print(f"{r['area']:28s} {r['total']:6d} {r['hidden']:6d} {str(r['atp_created']):>4s} "
              f"{r['supp']:3d} {r['failed']:4d} {str(r['ta']):>5s} {str(r['prh']):>4s} {str(r['chain']):>4s} {r['bare']:4d} {r['prov']:4d} {str(r['verified']):>5s}")
    print(f'{len(rows)} packs audited, {bad} violations')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
