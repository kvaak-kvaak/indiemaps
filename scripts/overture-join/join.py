"""Overture Places + OpenStreetMap join experiment.

Overture gives the POI base (names/coords/categories + real contact details);
OSM contributes what Overture lacks: opening_hours + extra contact tags.
Anything unverified stays empty — never invented.

Usage:
  python3 join.py --bbox -0.10,51.52,0.00,51.59 --out ./output/hackney
  python3 join.py --bbox -0.10,51.52,0.00,51.59 --skip-pull --overture output/hackney.overture.json --osm output/hackney.osm.json

Requires: pip install duckdb  (network: S3 + Overpass, both keyless)
"""
import argparse, json, re, time, urllib.request, urllib.parse
from collections import Counter
from pathlib import Path

RELEASE = '2026-07-22.0'
S3BASE = f's3://overturemaps-us-west-2/release/{RELEASE}/theme=places/type=place/*.parquet'
FOOD_CATS = {'restaurant', 'bar', 'cafe', 'casual_eatery', 'coffee_shop', 'pub',
             'fast_food', 'bakery', 'ice_cream', 'food_shop', 'supermarket',
             'convenience_store', 'grocery'}


def pull_overture(w, s, e, n):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2'; SET http_timeout=30000;")
    rows = con.execute(f"""SELECT id, names.primary AS name,
      basic_category AS basic_cat, categories.primary AS cat, confidence,
      operating_status, (bbox.xmin+bbox.xmax)/2 AS lon, (bbox.ymin+bbox.ymax)/2 AS lat,
      addresses[1].freeform AS addr, addresses[1].postcode AS postcode,
      websites[1] AS website, phones[1] AS phone
    FROM read_parquet('{S3BASE}')
    WHERE bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s}
      AND list_count(list_filter(sources, x -> x.dataset = 'Foursquare' AND x.property = '')) > 0
    """).fetchall()
    cols = ['id', 'name', 'basic_cat', 'cat', 'confidence', 'op_status',
            'lon', 'lat', 'addr', 'postcode', 'website', 'phone']
    return [dict(zip(cols, r)) for r in rows]


def pull_osm(w, s, e, n):
    q = (f'[out:json][timeout:25];(node["amenity"~"^(restaurant|cafe|pub|bar|fast_food|ice_cream|pharmacy|doctors|dentist|cinema|theatre|arts_centre|library|place_of_worship|clinic|optician)$"]({s},{w},{n},{e});'
         f'node["shop"]({s},{w},{n},{e});node["tourism"~"^(hotel|guest_house|hostel|attraction|museum|gallery|viewpoint)$"]({s},{w},{n},{e});'
         f'node["leisure"~"^(park|nature_reserve|miniature_golf|sports_centre)$"]({s},{w},{n},{e}););out 2000;')
    req = urllib.request.Request('https://overpass-api.de/api/interpreter?data=' + urllib.parse.quote(q),
                                 headers={'User-Agent': 'overture-osm-join/0.1'})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    return [x for x in d.get('elements', []) if x.get('tags', {}).get('name')]


def norm(s):
    s = re.sub(r'\bltd\b|\blimited\b|\bplc\b|\bthe\b|\boy\b|\bab\b', '', (s or '').lower())
    return re.sub(r'[^a-z0-9åäö ]', ' ', s.replace('&', 'and'))  # åäö retained (FI/SE names)

STOP = {'and', 'of', 'de', 'la', 's'}  # 's' = possessive artifact
GENERIC = {'southend', 'Leigh', 'westcliff', 'chalkwell', 'hackney', 'london',
           'high', 'street', 'road', 'avenue', 'broadway', 'parade', 'town',
           'centre', 'center', 'branch', 'store', 'station', 'sea', 'old',
           'new', 'north', 'south', 'east', 'west', 'great', 'on'}

def toks(s, minlen=3):
    return set(w for w in norm(s).split() if len(w) >= minlen and w not in STOP)

def toks_all(s):
    return set(w for w in norm(s).split() if len(w) >= 1 and w not in STOP)


def brand_gate(poi_name, brand):
    """Shared specific vocabulary with the feature's own brand (never branch).
    Kills concessions (Costa-in-Nisa) and branch-led area matches."""
    bt = toks_all(brand)
    if not bt:
        return False
    long_bt = {w for w in bt if len(w) >= 2}
    pt = toks_all(poi_name)
    if long_bt:
        return bool((pt & long_bt) - GENERIC - STOP)
    return 'initialism'  # e.g. B&M: only single-char tokens


def norm_url(u):
    if not u:
        return None
    s = str(u).strip().lower().replace('https://', '').replace('http://', '').replace('www.', '', 1).rstrip('/')
    if not s or any(c.isspace() for c in s):
        return None
    host, _, rest = s.partition('/')
    path = rest.split('?')[0].split('#')[0]
    return (host + ('/' + path if path else ''), bool(path))


def match_candidate(poi_name, plat, plon, ppc, fname, fbrand, fbranch, flat, flon, fpc, pwebsite=None, fwebsite=None, owikidata=None, fwikidata=None):
    """Brand-anchored match decision. Returns score or 0."""
    pc = bool(fpc and ppc and fpc == ppc)
    d = dist_m(plat, plon, flat, flon)
    if pwebsite and fwebsite:
        a, b = norm_url(pwebsite), norm_url(fwebsite)
        if a and b and a == b and a[1] and d < 500:
            return 1.8  # equal store-specific URLs (Check-The-Places rule)
    # Pass 0 (exact): shared Wikidata brand ID — deterministic, no fuzzy risk
    if owikidata and fwikidata and owikidata == fwikidata and d < 500:
        return 2.5 - d / 100000  # epsilon: nearest branch wins ties
    if d > (250 if pc else 120):
        return 0
    fn, pn = norm(fname), norm(poi_name)
    if len(fn) >= 6 and fn == pn and (pc or d < 60):
        return 2.0  # exact same name, same premises
    gate = brand_gate(poi_name, fbrand)
    ns = max(nscore(poi_name, fname), nscore(poi_name, fbrand),
             nscore(poi_name, fbranch) if fbranch else 0)
    if gate is True and (ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc))):
        return ns + (0.3 if pc else 0)
    if gate == 'initialism' and pc and d < 60:
        if len(set(toks_all(poi_name)) & set(toks_all(fbrand))) >= 2:
            return 0.6
    return 0

def nscore(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb: return 0
    score = len(ta & tb) / max(len(ta), len(tb))
    ca, cb = norm(a).replace(' ', ''), norm(b).replace(' ', '')
    if min(len(ca), len(cb)) >= 8 and (ca in cb or cb in ca):
        score = max(score, 0.9)
    return score

def dist_m(a, b, c, d): return (((a - c) * 111320) ** 2 + ((b - d) * 62500) ** 2) ** 0.5


def grid_index(items, lat_k, lon_k, cell=0.01):
    g = {}
    for i, it in enumerate(items):
        key = (round(it[lon_k] / cell), round(it[lat_k] / cell))
        g.setdefault(key, []).append(i)
    return g, cell


def nearby(grid, cell, lat, lon):
    cx, cy = round(lon / cell), round(lat / cell)
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out.extend(grid.get((cx + dx, cy + dy), []))
    return out


def join(ov, osm, radius=150):
    grid, cell = grid_index(osm, 'lat', 'lon')
    used, matches = set(), []
    for o in ov:
        best, bs = None, 0
        ow = o.get('brand_wikidata')
        for i in nearby(grid, cell, o['lat'], o['lon']):
            el = osm[i]
            if i in used: continue
            t = el['tags']
            d = dist_m(o['lat'], o['lon'], el['lat'], el['lon'])
            tw = t.get('brand:wikidata')
            if ow and tw and ow == tw and d < 500:
                sc = 3.0 - d / 100000  # exact brand QID (Chain Reaction method)
                if sc > bs:
                    bs, best = sc, i
                continue
            if d > radius: continue
            ns = nscore(o['name'], t['name'])
            pc = bool(o['postcode'] and t.get('addr:postcode') and
                      t['addr:postcode'].replace(' ', '').lower() == o['postcode'].replace(' ', '').lower())
            if ns >= 0.5 or (ns >= 0.34 and (d < 60 or pc)):
                sc = ns + (0.3 if pc else 0) + (0.1 if d < 50 else 0)
                if sc > bs: bs, best = sc, i
        if best is not None:
            used.add(best); matches.append((o, osm[best]))
    return matches


def load_atp(atp_dir, bbox):
    """Chain POIs from AllThePlaces spider GeoJSONs (CC0, first-party hours)."""
    import glob
    w, s, e, n = bbox
    feats = []
    for f in sorted(glob.glob(f'{atp_dir}/*.geojson')):
        spider = f.split('/')[-1].replace('.geojson', '')
        try:
            fc = json.load(open(f))
        except Exception:
            continue
        for ft in fc.get('features', []):
            if (ft.get('geometry') or {}).get('type') != 'Point':
                continue
            lon, lat = ft['geometry']['coordinates']
            if not (w <= lon <= e and s <= lat <= n):
                continue
            pr = ft.get('properties', {}) or {}
            feats.append({
                'spider': spider, 'brand': pr.get('brand') or pr.get('name') or spider,
                'name': pr.get('name') or pr.get('branch') or '',
                'branch': pr.get('branch') or '', 'lat': lat, 'lon': lon,
                'hours': pr.get('opening_hours') or '',
                'website': pr.get('website') or '',
                'wikidata': pr.get('brand:wikidata'),
                'nsi': pr.get('nsi_id'),
                'postcode': ((pr.get('addr:postcode') or '').replace(' ', '').lower() or None),
            })
    return feats


def join_atp(base, feats):
    """Match base POIs (overture dicts with name/lat/lon/postcode) to ATP chain
    features using the brand-anchored rule (see match_candidate)."""
    grid, cell = grid_index(feats, 'lat', 'lon')
    out = []
    for o in base:
        best, bs = None, 0
        opc = (o.get('postcode') or '').replace(' ', '').lower() or None
        for j in nearby(grid, cell, o['lat'], o['lon']):
            f = feats[j]
            sc = match_candidate(o['name'], o['lat'], o['lon'], opc,
                                 f['name'], f['brand'], f['branch'],
                                 f['lat'], f['lon'], f['postcode'],
                                 o.get('website'), f.get('website'),
                                 o.get('brand_wikidata'), f.get('wikidata'))
            if sc > bs:
                bs, best = sc, f
        if best is not None:
            out.append((o, best))
    return out


def atp_additive(base, feats):
    """ATP chain features with hours that match NOTHING in the base
    (name+proximity, lenient) — genuinely new coverage, not just enrichment."""
    grid, cell = grid_index(base, 'lat', 'lon')
    new = []
    for f in feats:
        if not f['hours']:
            continue
        found = False
        for i in nearby(grid, cell, f['lat'], f['lon']):
            o = base[i]
            if dist_m(f['lat'], f['lon'], o['lat'], o['lon']) > 150:
                continue
            if nscore(f['name'], o['name']) >= 0.34 or nscore(f['brand'], o['name']) >= 0.5:
                found = True
                break
        if not found:
            new.append(f)
    return new


def report(ov, osm, matches):
    matched = {id(o) for o, _ in matches}
    only_ov = [o for o in ov if id(o) not in matched]
    food = [o for o in ov if (o['basic_cat'] or '') in FOOD_CATS]
    food_matched = {o['name'] for o in food if id(o) in matched}
    stats = {
        'overture': len(ov), 'osm_nodes': len(osm), 'matched': len(matches),
        'match_rate': round(len(matches) / max(len(ov), 1), 4),
        'overture_only': len(only_ov),
        'food_overture': len(food),
        'food_matched': len(food_matched),
        'food_match_rate': round(len(food_matched) / max(len(food), 1), 4),
        'osm_adds_hours': sum(1 for _, e in matches if e['tags'].get('opening_hours')),
        'osm_adds_phone': sum(1 for _, e in matches if e['tags'].get('phone') or e['tags'].get('contact:phone')),
        'osm_adds_website': sum(1 for _, e in matches if e['tags'].get('website') or e['tags'].get('contact:website')),
        'overture_has_phone': sum(1 for o in ov if o['phone']),
        'overture_has_website': sum(1 for o in ov if o['website']),
        'overture_has_postcode': sum(1 for o in ov if o['postcode']),
    }
    return stats, only_ov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True, help='w,s,e,n')
    ap.add_argument('--out', required=True, help='output prefix')
    ap.add_argument('--skip-pull', action='store_true')
    ap.add_argument('--overture', default=None)
    ap.add_argument('--osm', default=None)
    ap.add_argument('--atp-dir', default=None, help='dir of ATP spider *.geojson')
    a = ap.parse_args()
    w, s, e, n = map(float, a.bbox.split(','))
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)

    if a.skip_pull:
        ov = json.load(open(a.overture)); osm = json.load(open(a.osm))
    else:
        t0 = time.time(); ov = pull_overture(w, s, e, n); print(f'overture: {len(ov)} ({time.time()-t0:.0f}s)')
        json.dump(ov, open(str(out) + '.overture.json', 'w'))
        t0 = time.time(); osm = pull_osm(w, s, e, n); print(f'osm nodes: {len(osm)} ({time.time()-t0:.0f}s)')
        json.dump(osm, open(str(out) + '.osm.json', 'w'))

    matches = join(ov, osm)
    stats, only_ov = report(ov, osm, matches)
    if a.atp_dir:
        w, s, e, n = map(float, a.bbox.split(','))
        afeats = load_atp(a.atp_dir, (w, s, e, n))
        atp_m = join_atp(ov, afeats)
        atp_hours = sum(1 for _, f in atp_m if f['hours'])
        stats['atp_feats_in_bbox'] = len(afeats)
        stats['atp_feats_with_hours'] = sum(1 for f in afeats if f['hours'])
        stats['overture_matched_atp'] = len(atp_m)
        stats['overture_hours_via_atp'] = atp_hours
        # OSM-matched pairs: ATP vs OSM hours head-to-head
        atp_by_name = {}
        for o, f in atp_m:
            if f['hours']:
                atp_by_name[o['name']] = f['hours']
        both, agree = 0, 0
        for o, t in matches:
            oh = t['tags'].get('opening_hours')
            ah = atp_by_name.get(o['name'])
            if oh and ah:
                both += 1
                if oh.replace(' ', '') == ah.replace(' ', ''):
                    agree += 1
        stats['pairs_with_both_hours'] = both
        stats['pairs_hours_agree'] = agree
        # overall hours coverage on the overture base
        osm_hours_names = {o['name'] for o, t in matches if t['tags'].get('opening_hours')}
        covered = len(osm_hours_names | set(atp_by_name))
        stats['overture_with_any_hours'] = covered
        stats['overture_hours_coverage'] = round(covered / max(len(ov), 1), 4)
        # ATP features with hours matching nothing in the base = new coverage
        additive = atp_additive(ov, [f for f in afeats if f['hours']])
        stats['atp_additive_new_pois'] = len(additive)
        stats['atp_additive_examples'] = [
            {'name': f['name'], 'brand': f['brand'], 'postcode': f.get('postcode'),
             'hours': f['hours'][:60]} for f in additive[:15]]
    print(json.dumps(stats, indent=1))
    json.dump(stats, open(str(out) + '.stats.json', 'w'), indent=1)
    json.dump([{'overture': o, 'osm_tags': e['tags'],
                'osm_lat': e['lat'], 'osm_lon': e['lon']} for o, e in matches],
              open(str(out) + '.matches.json', 'w'))
    print(f'\nwrote {out}.{{stats,matches}}.json')


if __name__ == '__main__':
    main()
