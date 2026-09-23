#!/usr/bin/env python3
"""Address-resolution gate: every rendered pin must resolve its address to
a surveyed OSM address-point. Lookup by string equality only — never
estimation (interpolation stays barred: computed positions are confident
invention).

For each record positioned by batch coordinates (geo_precision fsa or
postcode, no OSM corroboration — FSA rows, CH creations, NHS fallbacks):
- parse housenumber (+range/ambiguous detection) or named unit + street,
- exact match against the addr_index (number+street, or unit+street;
  postcode must not conflict when both sides carry one),
- on match: adopt surveyed coordinates (geo_precision 'osm-addr',
  donor recorded); ambiguous or unmatched: position_unresolved (hidden
  from default map payloads, reachable via search/by-id — the existing
  middle-path plumbing).

Deliberately OSM-only: the evaluated Overture fallback proved dormant —
Southend has zero address-level Overture counterparts for unplaced rows,
and Helsinki's 29k Overture rows carry no freeform addresses at all — so
no derived positions enter through any path. Ranges ("1208-1210") and
multi-candidate keys skip loudly, never guess. No creation, no deletion,
no moves without a donor. Idempotent (resolved records carry osm-addr
precision and are skipped on re-run). Fully offline (index file in).

  python3 resolve_positions.py --pois <pois> --meta <meta> --addr-index <addr_index.json>
"""
import argparse, json, re


def norm_street(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-zåäö ]', ' ', (s or '').lower())).strip()


def parse_address(addr):
    """-> ('numbered', number, range_end|None, street) |
          ('unit', unit, number|None, street) | ('named-only', ...) | (None,)"""
    a = (addr or '').strip()
    m = re.match(r'^\s*(\d+[a-z]?)\s*(?:[-–]\s*(\d+[a-z]?))?\s*,?\s*(.*)$', a, re.I)
    if m and re.search(r'[a-zåäö]', m.group(3), re.I):
        street = norm_street(m.group(3).split(',')[0])
        if street:
            return ('numbered', m.group(1).lower(),
                    m.group(2).lower() if m.group(2) else None, street)
    m = re.match(r'^([A-Za-z][A-Za-z .&\'-]*?)\s+(\d+[a-z]?)\s*,(.*)$', a)
    if m:
        street = norm_street(m.group(3).split(',')[0])
        return ('unit', m.group(1).strip().lower(), m.group(2).lower(), street)
    m = re.match(r'^([^,]+),\s*(.+)$', a)
    if m:
        return ('named-only', None, None, m.group(1).strip().lower()[:60])
    return (None, None, None, None)


def npc(pc):
    return (pc or '').replace(' ', '').lower() or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    ap.add_argument('--addr-index', required=True)
    a = ap.parse_args()
    pois = json.load(open(a.pois))
    idx = json.load(open(a.addr_index))
    by_key = {}
    for o in idx:
        st = norm_street(o.get('street'))
        if o.get('housenumber') and st:
            by_key.setdefault((o['housenumber'].lower(), st), []).append(o)

    resolved = unresolved = ambiguous = 0
    unresolvable = []
    for p in pois:
        p.pop('position_unresolved', None)
        p.pop('position_resolved', None)
        p.pop('addr_donor', None)
        if p.get('geo_precision') not in ('fsa', 'postcode'):
            continue
        if 'osm' in (p.get('sources', []) or []):
            continue
        kind, num, num2, street = parse_address(p.get('address'))
        if num2:
            ambiguous += 1
            p['position_unresolved'] = True
            unresolvable.append({'id': p['id'], 'name': p.get('name'),
                                 'why': 'range-ambiguous'})
            continue
        cands = []
        if kind == 'numbered' and street:
            cands = by_key.get((num, street), [])
        if kind == 'unit' and street:
            # named unit ("Kiosk 7, Western Esplanade"): match housename,
            # or the trailing number against housenumber on the same street
            unit_name = (p.get('address') or '').split(',')[0].strip().lower()
            cands = [o for o in idx
                     if norm_street(o.get('street')) == street
                     and ((o.get('housename') or '').lower() == unit_name
                          or (num and (o.get('housenumber') or '').lower() == num))]
        if not cands:
            unresolved += 1
            p['position_unresolved'] = True
            unresolvable.append({'id': p['id'], 'name': p.get('name'),
                                 'why': 'no-address-point'})
            continue
        # postcode must not conflict when both sides carry one
        ppc = npc(p.get('postcode'))
        cands = [o for o in cands
                 if not (ppc and npc(o.get('postcode')) and npc(o.get('postcode')) != ppc)]
        if len(cands) != 1:
            ambiguous += 1
            p['position_unresolved'] = True
            unresolvable.append({'id': p['id'], 'name': p.get('name'),
                                 'why': f'{len(cands)}-candidates'})
            continue
        o = cands[0]
        p['lat'], p['lng'] = o['lat'], o['lng']
        p['geo_precision'] = 'osm-addr'
        p['position_resolved'] = True
        p['addr_donor'] = {'type': o['type'], 'id': o['id']}
        resolved += 1
        print(f"resolved: {p.get('name')} [{p['id']}] <- {o['type']} {o['id']}")

    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['resolve_positions'] = {'index_size': len(idx), 'resolved': resolved,
                                 'unresolved': unresolved, 'ambiguous': ambiguous,
                                 'unresolvable': unresolvable[:200]}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'resolve_positions: {resolved} resolved, '
          f'{unresolved} unresolved, {ambiguous} ambiguous')


if __name__ == '__main__':
    main()
