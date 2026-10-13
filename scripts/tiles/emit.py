#!/usr/bin/env python3
"""Tile-source emitter (Phase 0): pack pois.json -> pin GeoJSON + detail
shards + name shards + manifest fragment. tippecanoe bakes the pins to
PMTiles on CI; details/names ship as flat gzip shards (the brief's
documented fallback — no custom PMTiles writer risk on the Thursday path).

Pin properties (lean, short keys): id, name, class, subclass (OpenMapTiles
values from the OpenFreeMap liberty sprite — unmapped categories tabled),
rank (1 = most prominent, dense order over p_rank), flags (vegan, veg,
halal, gf, wheelchair, chain, takeaway =1 when true), pt (0 surveyed /
2 approximate; owner-attested arrives with spider Phase 1), minzoom
(from minzoom.yaml percentiles over p_rank).

Excluded from pins (doctrine): hazard tier, seafront-contradiction,
quarantined (never in packs anyway). search_only + parking ride along
(client filters); names index carries hazard/search-only with flags.

Detail records: schema versioned; rec_stars/rec_n/rec_bayes included
(cards lose scores otherwise); audit fields stay in packs only.

  python3 scripts/tiles/emit.py --pois <pois.json> --meta <meta.json> --out <dir> --minzoom scripts/tiles/minzoom.yaml
"""
import argparse
import gzip
import json
import math
from pathlib import Path

SCHEMA_VERSION = 1

# Our category (+cues) -> (OMT class, OMT subclass). Values verified against
# the liberty sprite (147 base icons). Unmapped categories tabled in the
# Phase 0 report — never guessed.
CLASS_MAP = {
    'restaurant': ('restaurant', ''),
    'cafe': ('cafe', ''),
    'pub': ('bar', ''),
    'hotel': ('lodging', ''),
    'camping': ('lodging', ''),  # overnight accommodation like hotel; OMT class already verified, no new sprite needed
    'attraction': ('attraction', ''),
    'culture': ('art_gallery', ''),
    'health': ('doctors', ''),
    'services': ('shop', ''),
    'shopping': ('shop', ''),
    'parking': ('parking', ''),
    'transport': ('bus', ''),
}
# Refined by name/cuisine cues (first hit wins):
# Refined by name/cuisine cues: (word-pattern, OMT class, OMT subclass).
# First hit wins; word boundaries throughout ('bank' must not match
# 'riverbank'). Unmatched pins fall back to CLASS_MAP.
SUBCLASS_RULES = [
    (r'\bpizza\b', 'restaurant', 'pizza'),
    (r'\bsushi\b', 'restaurant', 'sushi'),
    (r'\bburger\b|\bfast.?food\b', 'restaurant', 'fast_food'),
    (r'\bbakery\b|\bcake\b|\bpatisserie\b', 'cafe', 'bakery'),
    (r'\bice.?cream\b|\bgelato\b', 'cafe', 'ice_cream'),
    (r'\bbank\b|\bhalifax\b|\bnatwest\b|\bhsbc\b|\blloyds\b|\bbarclays\b|\bnationwide\b|\bsantander\b', 'services', 'bank'),
    (r'\bpharmacy\b|\bchemist\b|\bboots\b|\bsuperdrug\b', 'services', 'pharmacy'),
    (r'\bchurch\b|\bcathedral\b|\bchapel\b|\bmosque\b|\bsynagogue\b', 'culture', 'place_of_worship'),
    (r'\bschool\b|\bcollege\b|\bacademy\b', 'services', 'school'),
    (r'\bcinema\b', 'culture', 'cinema'),
    (r'\btheatre\b|\btheater\b', 'culture', 'theatre'),
    (r'\bmuseum\b', 'culture', 'museum'),
    (r'\blibrary\b', 'culture', 'library'),
    (r'\bhotel\b|\bguest.?house\b|\bb&b\b', 'hotel', 'lodging'),
    (r'\bfuel\b|\bpetrol\b|\bshell\b|\btexaco\b', 'services', 'fuel'),
    (r'\bsupermarket\b|\btesco\b|\bsainsbury|\basda\b|\bmorrisons\b|\baldi\b|\blidl\b', 'shopping', 'grocery'),
    (r'\bflorist\b|\bflower', 'shopping', 'florist'),
    (r'\bbook\b|\bwaterstones\b', 'shopping', 'gift'),
    (r'\bclothes\b|\bfashion\b|\bshoe\b', 'shopping', 'clothing_store'),
    (r'\bdentist\b|\bdental\b', 'health', 'dentist'),
    (r'\bvet\b|\bvets\b|\bpet\b', 'health', 'veterinary'),
    (r'\bgym\b|\bfitness\b', 'services', 'stadium'),
    (r'\bpark\b', 'attraction', 'park'),
    (r'\bpost.?office\b', 'services', 'post'),
    (r'\blaundry\b|\bdry.?clean', 'services', 'laundry'),
    (r'\bhairdresser\b|\bbarber\b|\bbeauty\b|\bnails\b|\btattoo\b', 'services', 'hairdresser'),
    (r'\boptician\b|\bspecsavers\b', 'health', 'doctors'),
    (r'\bkebab\b|\bturkish\b', 'restaurant', ''),
    (r'\bchinese\b|\bnoodle\b|\bwok\b', 'restaurant', ''),
    (r'\bindian\b|\bcurry\b|\btandoori\b', 'restaurant', ''),
    (r'\bfish\b|\bchippy\b', 'restaurant', ''),
    (r'\bchicken\b|\bperi\b|\bnando', 'restaurant', ''),
    (r'\bcoffee\b|\bcosta\b|\bstarbucks\b|\bnero\b', 'cafe', ''),
    (r'\bpub\b|\bbar\b|\btavern\b|\bwetherspoon\b|\bgreene.?king\b', 'pub', ''),
    (r'\bstation\b|\brailway\b', 'transport', ''),
]


def classify(p):
    import re
    blob = ((p.get('name') or '') + ' ' + (p.get('cuisine') or '') + ' ' +
            ' '.join(p.get('site_cuisine') or [])).lower()
    for pat, cls, sub in SUBCLASS_RULES:
        if re.search(pat, blob):
            return cls, sub
    cat = p.get('category', 'services')
    return CLASS_MAP.get(cat, ('shop', ''))


def pt_of(p):
    if p.get('geo_precision') == 'osm':
        return 0
    return 2  # batch/postcode/overture/atp: approximate until owner-attested arrives


def flags_of(p):
    f = {}
    if p.get('ta_vegan'):
        f['vegan'] = 1
    if p.get('ta_vegetarian'):
        f['veg'] = 1
    blob = ((p.get('cuisine') or '') + ' ' + ' '.join(p.get('site_cuisine') or [])).lower()
    if 'halal' in blob:
        f['halal'] = 1
    if p.get('ta_gluten_free'):
        f['gf'] = 1
    if p.get('ta_wheelchair'):
        f['wheelchair'] = 1
    if p.get('atp_spider') or p.get('chain_source'):
        f['chain'] = 1
    if 'takeaway' in blob or 'takeaway' in (p.get('category_label') or '').lower():
        f['takeaway'] = 1
    return f


def z14_tile(lat, lng):
    n = 2 ** 14
    xt = int((lng + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    yt = int((1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return xt, yt


def norm_name(s):
    import re
    import unicodedata
    t = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9 ]', ' ', t.lower()).strip()


def add_detail_and_names(p, cls, rank, details, names, hidden=False):
    xt, yt = z14_tile(p['lat'], p['lng'])
    rec = {'id': p['id'], 'name': p.get('name'), 'schema': SCHEMA_VERSION,
           'address': p.get('address'), 'postcode': p.get('postcode'),
           'phone': p.get('phone'), 'website': p.get('website'),
           'facebook': p.get('facebook'), 'instagram': p.get('instagram'),
           'twitter': p.get('twitter'),
           'hours': {k: p[k] for k in ('opening_hours_osm', 'atp_hours', 'site_hours',
                                       'nhs_hours', 'sm_hours', 'chain_hours', 'ta_hours') if p.get(k)},
           'facts': {'cuisine': p.get('cuisine'), 'site_cuisine': p.get('site_cuisine'),
                     'diet': [k for k in ('ta_vegetarian', 'ta_vegan', 'ta_gluten_free') if p.get(k)],
                     'features': [k for k in ('ta_wheelchair', 'ta_dog', 'ta_play', 'ta_music') if p.get(k)]},
           'rec_stars': p.get('ta_rec_stars'), 'rec_n': p.get('ta_rec_n'),
           'rec_bayes': p.get('ta_rec_bayes'),
           'sources': p.get('sources'), 'pos_tier': pt_of(p),
           'fsa_id': p.get('fsa_id'), 'osm_type': p.get('osm_type'), 'osm_id': p.get('osm_id'),
           'overture_id': p.get('overture_id'),
           'search_only': bool(p.get('search_only')),
           'hidden': hidden,
           'shard': f'{xt}/{yt}'}
    details.setdefault(f'{xt}/{yt}', {})[p['id']] = rec
    for variant in {p.get('name') or '', ((p.get('alias') or {}).get('registered') or '')}:
        if not variant.strip():
            continue
        toks = [t for t in norm_name(variant).split() if len(t) >= 2]
        if not toks:
            # Single-letter names (B&Q -> 'bq'): index the compacted form.
            compact = norm_name(variant).replace(' ', '')
            if len(compact) >= 2:
                toks = [compact]
        for tok in toks:
            names.append({'tok': tok[:3], 'tok_full': tok, 'id': p['id'],
                          'name': p.get('name'), 'lat': p['lat'], 'lng': p['lng'],
                          'cls': cls, 'rank': rank, 'hidden': hidden,
                          'search_only': bool(p.get('search_only'))})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pois', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--minzoom', default='scripts/tiles/minzoom.yaml')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pois = json.load(open(a.pois))
    try:
        import yaml
        table = (yaml.safe_load(open(a.minzoom)) or {}).get('percentiles', [])
    except Exception:
        table = [(2, 12), (10, 13), (30, 14), (60, 15), (100, 16)]

    drawable = [p for p in pois
                if not p.get('position_hazard')
                and p.get('unresolved_why') not in ('stale-orphan', 'seafront-contradiction')]
    # Pack-level duplicate ids (measured: 35 sm-* dupes in Helsinki) must
    # not double-draw: keep first = best rank. (The pack dupe itself is a
    # separate packs-track issue.)
    seen_ids, unique = set(), []
    for p in drawable:
        if p.get('id') in seen_ids:
            continue
        seen_ids.add(p.get('id'))
        unique.append(p)
    ranked = sorted(unique, key=lambda p: (-(p.get('p_rank') or 0), p.get('id')))
    n = len(ranked)
    # Name rank for hidden-tier pins: they sort after everything drawable
    # (never shown, only searched) — same order family, no separate scale.
    hidden = [p for p in pois if p not in drawable]
    hidden_rank0 = n
    feats, details, names = [], {}, []
    unmapped = set()
    for i, p in enumerate(ranked, 1):
        pct = 100.0 * i / max(n, 1)
        mz = 16
        for edge, z in sorted(table):
            if pct <= edge:
                mz = z
                break
        cls, sub = classify(p)
        if (cls, sub) == ('shop', ''):
            unmapped.add(p.get('category') + ':' + (p.get('category_label') or ''))
        props = {'id': p['id'], 'name': p.get('name'), 'class': cls,
                 'rank': i, 'pt': pt_of(p), 'minzoom': mz}
        if sub:
            props['subclass'] = sub
        props.update(flags_of(p))
        feats.append({'type': 'Feature',
                      'geometry': {'type': 'Point', 'coordinates': [p['lng'], p['lat']]},
                      'properties': props, 'tippecanoe': {'minzoom': mz}})
        add_detail_and_names(p, cls, i, details, names)
    # Hidden tier: searchable, never drawn. Details + names with flags so
    # search and Show-unresolved resolve them; ranks continue past drawable.
    hidden_ids = {p.get('id') for p in pois} - {p.get('id') for p in ranked}
    for j, p in enumerate([x for x in pois if x.get('id') in hidden_ids], 1):
        cls, sub = classify(p)
        add_detail_and_names(p, cls, n + j, details, names, hidden=True)
    geo = {'type': 'FeatureCollection', 'features': feats}
    json.dump(geo, open(out / 'pins.geojson', 'w'))
    ddir, ndir = out / 'details', out / 'names'
    ddir.mkdir(exist_ok=True)
    ndir.mkdir(exist_ok=True)
    for shard, recs in details.items():
        with gzip.open(ddir / (shard.replace('/', '_') + '.json.gz'), 'wt') as f:
            json.dump(recs, f)
        # Plain twin for static servers without gzip content-encoding
        # (reference demo fallback; production serves the .gz).
        json.dump(recs, open(ddir / (shard.replace('/', '_') + '.json'), 'w'))
    bypref = {}
    for e in names:
        bypref.setdefault(e['tok'], []).append(e)
    for pref, es in bypref.items():
        es.sort(key=lambda e: e['rank'])
        with gzip.open(ndir / (pref + '.json.gz'), 'wt') as f:
            json.dump(es, f)
        json.dump(es, open(ndir / (pref + '.json'), 'w'))
    frag = {'pins': len(feats), 'detail_shards': len(details),
            'name_shards': len(bypref), 'unmapped': sorted(unmapped)}
    json.dump(frag, open(out / 'emit.json', 'w'), indent=1)
    manifest = {'schema_version': SCHEMA_VERSION, 'built_at': None,
                'minzoom_table': 'scripts/tiles/minzoom.yaml',
                'archives': {
                    'pois_pmtiles': {'url': None, 'bytes': None, 'sha256': None,
                                     'minzoom': 10, 'maxzoom': 16},
                    'details': {'shards': len(details), 'format': 'json.gz (+plain twin)'},
                    'names': {'shards': len(bypref), 'format': 'json.gz (+plain twin)'}},
                'pins': len(feats), 'unmapped': sorted(unmapped)}
    json.dump(manifest, open(out / 'manifest.json', 'w'), indent=1)
    print(f'tiles emit: {len(feats)} pins, {len(details)} detail shards, '
          f'{len(bypref)} name shards, unmapped={sorted(unmapped)}')


if __name__ == '__main__':
    main()
