#!/usr/bin/env python3
"""Companies House new-business tripwire (UK, keyless bulk).

The monthly Free Company Data Product (CSV ZIPs, downloaded once per run
by CI into data/ch/) lists every live UK company with incorporation date,
SIC and registered office. Incorporation precedes hygiene inspection by
months, so for the Ltd slice this is an earlier existence signal than FSA
— a complement for new-business detection, never a substitute (no sole
traders; registered office may differ from trading address).

Creation is tightly caged — a pin appears ONLY when ALL hold:
  - food SIC (56101/56102/56302), live status, incorporated within
    NEW_THRESHOLD_DAYS of the run,
  - absent from FSA (same stripped name + postcode, alias-aware) AND
    absent from pack POIs (brand-anchored name + 150 m),
  - registered address shared by at most FORMATION_ADDR_MAX food
    companies (formation-agent/accountant offices must not become pins),
  - registered postcode geocodes (postcodes.io centroid, geo_precision
    'postcode' — never invented coordinates).
Otherwise the row corroborates a matching POI (company number, dates,
previous names) or is discarded. Re-run safe (clears first, like
overture).

  python3 companies.py --bbox=W,S,E,N --pois packs/<id>/pois.json --meta packs/<id>/meta.json
"""
import argparse, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overture import toks, accept, nscore, dist_m, grid_index, nearby, norm_pc  # noqa

ROOT = Path(__file__).resolve().parents[2]
# Snapshot dir overridable (tests, local extracts). Default is the CI
# cache dir populated by the workflow's download step.
CHDIR = Path(os.environ.get('CH_SNAPSHOT_DIR', ROOT / 'data' / 'ch'))
GEOCACHE = CHDIR / 'geocache.json'
NEW_THRESHOLD_DAYS = 365
FORMATION_ADDR_MAX = 5
SIC_OK = ('56101', '56102', '56302')
SIC_CAT = {'56101': ('restaurant', 'Restaurant'),
           '56102': ('restaurant', 'Restaurant'),
           '56302': ('pub', 'Pub / Bar')}
LEGAL_SUFFIX = re.compile(r'\s*\b(LTD|LIMITED|PLC|LLP|CIC|LP)\.?$', re.I)
UK_PC = re.compile(r'([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})')


def strip_legal(name):
    return LEGAL_SUFFIX.sub('', (name or '').strip()).strip()


def parse_date(s):
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime((s or '').strip(), fmt).date()
        except ValueError:
            continue
    return None


def postcodes_io_geocode(postcodes):
    """Batch UK postcode lookup -> {postcode: (lat, lng)}. Missing = absent.
    Results persist in data/ch/geocache.json (committed, postcodes don't
    move), so only unseen postcodes hit the free API each run."""
    try:
        cache = json.load(open(GEOCACHE))
    except Exception:
        cache = {}
    missing = sorted({p for p in postcodes if p and p not in cache})
    for i in range(0, len(missing), 100):
        batch = missing[i:i + 100]
        try:
            req = urllib.request.Request(
                'https://api.postcodes.io/postcodes',
                data=json.dumps({'postcodes': batch}).encode(),
                headers={'Content-Type': 'application/json',
                         'User-Agent': 'indiemaps-pack-builder/0.1'})
            with urllib.request.urlopen(req, timeout=30) as r:
                for q, res in zip(batch, json.load(r)['result']):
                    if res and res.get('result'):
                        cache[q] = [res['result']['latitude'], res['result']['longitude']]
            time.sleep(0.5)  # polite gap between batches
        except Exception:
            continue
    if missing:
        try:
            GEOCACHE.parent.mkdir(parents=True, exist_ok=True)
            json.dump(cache, open(GEOCACHE, 'w'), indent=0)
        except Exception:
            pass
    return {p: tuple(cache[p]) for p in postcodes if p in cache}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bbox', required=True)
    ap.add_argument('--pois', required=True)
    ap.add_argument('--meta', required=True)
    a = ap.parse_args()
    csvs = sorted(f for f in CHDIR.glob('*.csv') if f.name != 'food.csv')
    if not csvs:
        print('companies snapshot absent (data/ch/*.csv), skipping ch stage')
        meta = json.load(open(a.meta))
        meta['ch'] = {'skipped': 'snapshot absent', 'matched': 0}
        json.dump(meta, open(a.meta, 'w'), indent=1)
        return
    w, s, e, n = map(float, a.bbox.split(','))
    import csv as csvmod
    import duckdb
    t0 = time.time()
    # Shared food extract: one snapshot scan per run, reused by every area.
    # food.csv carries resolved fields (sic string, prev as JSON list);
    # rebuilt whenever the snapshot is newer. Per-area cost drops to a
    # small CSV read plus geocoding of unseen postcodes (cached).
    FOODCSV = CHDIR / 'food.csv'
    src_newest = max(f.stat().st_mtime for f in csvs)
    raw = None
    if FOODCSV.exists() and FOODCSV.stat().st_mtime >= src_newest:
        with open(FOODCSV, newline='', encoding='utf-8') as f:
            raw = []
            for row in csvmod.DictReader(f):
                try:
                    prev = json.loads(row.get('prev') or '[]')
                except Exception:
                    prev = []
                try:
                    lat = float(row.get('lat') or '')
                    lng = float(row.get('lng') or '')
                except (TypeError, ValueError):
                    continue
                raw.append({'name': row.get('name') or '',
                            'number': row.get('number') or '',
                            'addr': row.get('addr') or '',
                            'town': row.get('town') or '',
                            'postcode': row.get('postcode') or None,
                            'inc': row.get('inc') or None,
                            'sic': row.get('sic') or '',
                            'prev': prev if isinstance(prev, list) else [],
                            'lat': lat, 'lng': lng})
        print(f'companies food rows (cached extract): {len(raw)}')
    else:
        # Positional columns: real CSV headers contain dots (e.g.
        # RegAddress.PostCode) which DuckDB reads as table qualifiers, so
        # the header maps to safe c0..cN positions with explicit VARCHAR
        # types. A header mismatch fails loudly here with the actual header
        # shown (schema drift must be visible, never silently empty).
        with open(csvs[0], newline='', encoding='utf-8-sig') as f:
            header = next(csvmod.reader(f))
        low = [h.strip().lower() for h in header]

        def col(*cands):
            for cand in cands:
                for i, h in enumerate(low):
                    if h == cand.lower():
                        return i
            raise RuntimeError(
                f'CH snapshot missing column {cands[0]}; first headers: {header[:14]}')

        C = {
            'name': col('CompanyName'),
            'number': col('CompanyNumber'),
            'postcode': col('RegAddress.PostCode', 'PostCode'),
            'addr1': col('RegAddress.AddressLine1', 'AddressLine1'),
            'town': col('RegAddress.PostTown', 'PostTown'),
            'inc': col('IncorporationDate'),
        }
        C['sics'] = [i for i in range(len(header))
                     if 'sic' in low[i] and 'text' in low[i]][:4]
        if not C['sics']:
            C['sics'] = [i for i in range(len(header)) if low[i].startswith('sic')]
        if not C['sics']:
            raise RuntimeError(f'CH snapshot has no SIC column; first headers: {header[:14]}')
        st = [i for i in range(len(header)) if low[i] in ('companystatus', 'company status')]
        C['status'] = st[0] if st else None
        C['prev'] = [i for i in range(len(header)) if 'prev' in low[i] and 'name' in low[i]]
        coltypes = ', '.join(f"'c{i}': 'VARCHAR'" for i in range(len(header)))
        files = ', '.join(f"'{f}'" for f in csvs)
        sic_cond = ' OR '.join(f"starts_with(c{i}, '{p}')"
                               for i in C['sics'] for p in SIC_OK)
        status_cond = f"AND (c{C['status']} ILIKE '%ctive%')" if C['status'] is not None else ''
        prev_sel = ''.join(f', c{i}' for i in C['prev'])
        rows = duckdb.connect().execute(f"""SELECT c{C['name']}, c{C['number']},
          c{C['addr1']}, c{C['town']}, c{C['postcode']}, c{C['inc']},
          {', '.join(f'c{i}' for i in C['sics'])}{prev_sel}
        FROM read_csv([{files}], header=false, skip=1, columns={{{coltypes}}})
        WHERE ({sic_cond}) {status_cond}""").fetchall()
        raw = []
        for r in rows:
            sics = [str(x or '')[:5] for x in r[6:6 + len(C['sics'])]]
            prev = [str(x or '').strip() for x in r[6 + len(C['sics']):] if str(x or '').strip()]
            raw.append({'name': (r[0] or '').strip(), 'number': str(r[1] or '').strip(),
                        'addr': (r[2] or '').strip(), 'town': (r[3] or '').strip(),
                        'postcode': (r[4] or '').strip().upper() or None,
                        'inc': (r[5] or '').strip() or None,
                        'sic': next((x for x in sics if x[:5] in SIC_OK), ''),
                        'prev': prev})
        raw = [r for r in raw if r['name'] and r['sic']]
        # Geocode once globally (not per area): distinct registered
        # postcodes via the persistent cache; unlocatable rows (non-UK,
        # unknown postcodes) drop here since matching needs coordinates.
        geo = postcodes_io_geocode({r['postcode'] for r in raw if r['postcode']})
        located, unloc = [], 0
        for r in raw:
            g = geo.get(r['postcode'] or '')
            if g:
                r['lat'], r['lng'] = g
                located.append(r)
            else:
                unloc += 1
        raw = located
        with open(FOODCSV, 'w', newline='', encoding='utf-8') as f:
            wr = csvmod.DictWriter(f, fieldnames=['name', 'number', 'addr', 'town',
                                                  'postcode', 'inc', 'sic', 'prev',
                                                  'lat', 'lng'])
            wr.writeheader()
            for r in raw:
                wr.writerow({k: (json.dumps(v, ensure_ascii=False) if k == 'prev' else v)
                             for k, v in r.items()})
        print(f'companies food rows: {len(raw)} ({unloc} unlocatable, {time.time()-t0:.0f}s)')
    today = datetime.now(timezone.utc).date()
    recs = []
    for r in raw:
        inc = parse_date(r['inc'])
        age = (today - inc).days if inc else None
        recs.append({**r, 'inc': inc.isoformat() if inc else None, 'age': age})

    # Formation-agent suppression: addresses hosting many food companies
    # are offices, not restaurants.
    addr_count = {}
    for r in recs:
        key = ((r['addr'] + ' ' + (r['postcode'] or '')).lower().strip())
        if key:
            addr_count[key] = addr_count.get(key, 0) + 1

    pois = json.load(open(a.pois))
    # clear previous pass (re-run safe): drop ch-created records, strip
    # ch keys. Base records are otherwise untouched.
    dropped = sum(1 for p in pois if p['id'].startswith('ch-'))
    pois = [p for p in pois if not p['id'].startswith('ch-')]
    for p in pois:
        for k in ('ch_number', 'ch_incorporated', 'ch_sic', 'ch_prev_names'):
            p.pop(k, None)
        p['sources'] = [x for x in p.get('sources', []) if x != 'ch']
    if dropped:
        print(f'cleared {dropped} previous ch-created records')

    # FSA presence set: stripped names + aliases, keyed by postcode.
    fsa_names = set()
    for p in pois:
        if not p.get('fsa_id'):
            continue
        pc = norm_pc(p.get('postcode'))
        for nm in {p.get('name'), p.get('fsa_name'), (p.get('alias') or {}).get('registered')}:
            if nm and pc:
                fsa_names.add((strip_legal(nm).lower(), pc))

    def in_fsa(r):
        rpc = norm_pc(r['postcode'])
        if not rpc:
            return False
        return (strip_legal(r['name']).lower(), rpc) in fsa_names

    # Grid over locatable records only; indices address gpois (NOT pois —
    # mixing the two misaligns lookups).
    gpois = [p for p in pois if p.get('lat') is not None]
    grid, cell = grid_index(gpois, 'lat', 'lng')
    matched = created = suppressed = 0
    for r in recs:
        if r['lat'] is None or not (s <= r['lat'] <= n and w <= r['lng'] <= e):
            continue
        disp = strip_legal(r['name'])
        if not disp:
            continue
        # corroborate-or-create against current POIs
        ppc = norm_pc(r['postcode'])
        best, bs = None, 0
        for j in nearby(grid, cell, r['lat'], r['lng']):
            p = gpois[j]
            d = dist_m(p['lat'], p['lng'], r['lat'], r['lng'])
            if d > 150:
                continue
            ns = nscore(disp, p['name'] or '')
            opc = norm_pc(p.get('postcode'))
            pc = bool(opc and ppc and opc == ppc)
            if accept(disp, p['name'] or '', ns, d, pc):
                sc = ns + (0.3 if pc else 0)
                if sc > bs:
                    bs, best = sc, p
        if best is not None:
            matched += 1
            best['ch_number'] = r['number']
            best['ch_incorporated'] = r['inc']
            best['ch_sic'] = r['sic']
            if r['prev']:
                best['ch_prev_names'] = r['prev'][:10]
            if 'ch' not in best.get('sources', []):
                best['sources'].append('ch')
            continue
        # create only: new + FSA-absent + unshared address + locatable
        if r['age'] is None or r['age'] > NEW_THRESHOLD_DAYS:
            continue
        if in_fsa(r):
            continue
        addr_key = ((r['addr'] + ' ' + (r['postcode'] or '')).lower().strip())
        if addr_count.get(addr_key, 0) > FORMATION_ADDR_MAX:
            suppressed += 1
            continue
        cat, label = SIC_CAT.get(r['sic'][:5], ('restaurant', 'Restaurant'))
        addr = (r['addr'] + (f", {r['town']}" if r['town'] else '') +
                (f", {r['postcode']}" if r['postcode'] else '')).strip(', ')
        pois.append({
            'id': f"ch-{r['number']}", 'ch_number': r['number'],
            'ch_legal_name': r['name'], 'ch_incorporated': r['inc'],
            'ch_sic': r['sic'], 'ch_prev_names': r['prev'][:10],
            'fsa_id': None, 'osm_type': None, 'osm_id': None,
            'name': disp, 'category': cat, 'category_label': label,
            'lat': r['lat'], 'lng': r['lng'], 'geo_precision': 'postcode',
            'address': addr, 'postcode': r['postcode'],
            'phone': '', 'email': '', 'website': '',
            'facebook': '', 'instagram': '', 'twitter': '',
            'opening_hours_osm': '', 'opening_hours': {}, 'cuisine': '',
            'brand_wikidata': None, 'wikipedia': None, 'wikidata': None,
            'amenities': [], 'photos': [], 'description': '',
            'sources': ['ch'],
        })
        created += 1
    json.dump(pois, open(a.pois, 'w'), indent=1)
    meta = json.load(open(a.meta))
    meta['ch'] = {'snapshot_rows_food': len(recs), 'matched': matched,
                  'created': created, 'formation_suppressed': suppressed,
                  'threshold_days': NEW_THRESHOLD_DAYS}
    json.dump(meta, open(a.meta, 'w'), indent=1)
    print(f'companies: {len(recs)} food rows -> {matched} corroborated, {created} created, {suppressed} formation-suppressed')


if __name__ == '__main__':
    main()
