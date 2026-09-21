#!/usr/bin/env python3
"""Verify FSA pin positions against OSM (distance-blind by design).

Proximity cannot verify a displaced pin — the error IS displacement — so
this stage establishes identity without using distance, then lets the
surveyed OSM position overrule the batch FSA geocode. Two rules, both
requiring exact normalized-name agreement (min 6 chars):

- attested: the OSM record carries this exact FHRSID (mapper-sourced from
  FHRS Open Data) AND the FSA row is live. Distance/postcode sanity does
  not apply: the mapper already verified. Measured: Beach Hut, 1532 m.
- premises: exact name + area-unique (one FSA row + one OSM record share
  it across the pack: chains structurally excluded) + same postcode, or
  same housenumber with a shared street word.

Merges adopt the OSM position/precision and log loudly with the distance,
so every long jump is reviewable. No creation, no deletion, no moves
without a merge. Overture rows never qualify as position donors.

  python3 verify_positions.py --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json


def norm(s):
    import re
    s = re.sub(r'\bltd\b|\blimited\b|\bplc\b|\bthe\b|\boy\b|\bab\b', '', (s or '').lower())
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9åäö ]', ' ', s.replace('&', 'and'))).strip()


def hn(addr):
    import re
    m = re.match(r'\s*(\d+[a-z]?)', addr or '', re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r',\s*(\d+[a-z]?)\b', addr or '')
    return m.group(1).lower() if m else None


def npc(pc):
    return (pc or '').replace(' ', '').lower() or None


def dist_m(a, b, c, d):
    return (((a - c) * 111320) ** 2 + ((b - d) * 62500) ** 2) ** 0.5


def merge_into(f, o, via, d):
    if o.get('phone') and not f.get('phone'):
        f['phone'] = o['phone']
    if o.get('website') and not f.get('website'):
        f['website'] = o['website']
        f['website_source'] = 'osm'
    if o.get('opening_hours_osm') and not f.get('opening_hours_osm'):
        f['opening_hours_osm'] = o['opening_hours_osm']
    if o.get('cuisine') and not f.get('cuisine'):
        f['cuisine'] = o['cuisine']
    for key in ('osm_type', 'osm_id', 'osm_touched', 'osm_version', 'osm_fhrs_id'):
        if o.get(key) and not f.get(key):
            f[key] = o[key]
    if 'osm' not in f.get('sources', []):
        f['sources'].append('osm')
    f['lat'], f['lng'] = o['lat'], o['lng']
    f['geo_precision'] = 'osm'
    f['verified_position'] = via
    f['verified_from'] = o['id']
    o['superseded_by'] = {'id': f['id'], 'name': f.get('name'), 'via': via}
    print(f'verify-positions MERGED ({via}): {f.get("name")} [{f["id"]}] '
          f'<- {o["id"]} (moved {round(d)}m)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    pois = json.load(open(a.pois))
    meta = json.load(open(a.meta))
    attested, premises = [], []

    def eligible_f(p):
        s = p.get('sources', []) or []
        return 'fsa' in s and 'osm' not in s

    def eligible_o(p):
        s = p.get('sources', []) or []
        return ('osm' in s and not any(x in s for x in ('fsa', 'ch', 'servicemap'))
                and not p.get('superseded_by'))

    # Rule 0 — attested.
    orphans = [p for p in pois if eligible_o(p)]
    for o in orphans:
        if o.get('superseded_by'):
            continue
        fid = o.get('osm_fhrs_id')
        if not fid:
            continue
        fs = [p for p in pois if eligible_f(p) and str(p.get('fsa_id')) == str(fid)
              and norm(p.get('name') or '') == norm(o.get('name') or '')
              and len(norm(o.get('name') or '')) >= 6]
        if len(fs) != 1:
            continue
        f = fs[0]
        d = dist_m(f['lat'], f['lng'], o['lat'], o['lng'])
        merge_into(f, o, 'attested', d)
        attested.append({'fsa': f['id'], 'osm': o['id'], 'name': f.get('name'),
                         'moved_m': round(d)})

    # Rule 1 — premises (folded in: same code path, ~10 lines of logic).
    orphans = [p for p in pois if eligible_o(p)]
    by_f, by_o = {}, {}
    for p in pois:
        if not eligible_f(p):
            continue
        k = norm(p.get('name') or '')
        if len(k) >= 6:
            by_f.setdefault(k, []).append(p)
    for o in orphans:
        k = norm(o.get('name') or '')
        if len(k) >= 6:
            by_o.setdefault(k, []).append(o)
    for k in sorted(set(by_f) & set(by_o)):
        fs, os_ = by_f[k], by_o[k]
        if len(fs) != 1 or len(os_) != 1:
            continue
        f, o = fs[0], os_[0]
        if o.get('superseded_by'):
            continue
        fpc, opc = npc(f.get('postcode')), npc(o.get('postcode'))
        same_pc = bool(fpc and opc and fpc == opc)
        fhn, ohn = hn(f.get('address')), hn(o.get('address'))
        fw = {w for w in norm(f.get('address') or '').split() if len(w) >= 5}
        ow = {w for w in norm(o.get('address') or '').split() if len(w) >= 5}
        same_street = bool(fhn and ohn and fhn == ohn and (fw & ow))
        if not (same_pc or same_street):
            continue
        d = dist_m(f['lat'], f['lng'], o['lat'], o['lng'])
        merge_into(f, o, 'premises', d)
        premises.append({'fsa': f['id'], 'osm': o['id'], 'name': f.get('name'),
                         'moved_m': round(d)})

    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta['verify_positions'] = {'attested_merges': attested,
                                'premises_merges': premises}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'verify-positions: {len(attested)} attested, {len(premises)} premises merges')


if __name__ == '__main__':
    main()
