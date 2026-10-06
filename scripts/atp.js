/* Merge AllThePlaces chain data (CC0, first-party data) into the built listings.
 *
 * Fetch layer (ported from the verified sibling build's importer):
 * spider set derived from the run's published stats/_results.json (no hand
 * list to rot); per-spider country counts skip fully-accounted non-UK
 * exports; zero-feature exports are used as-is and NEVER backfilled from
 * older runs (an older pull can resurrect genuinely closed branches);
 * failed/missing exports take ONE targeted supplement from a second run,
 * with per-spider provenance. Every skip/failure is recorded in the
 * ledger; downloads are stream-verified (feature count must match the
 * published figure). Never invents: only chain-published strings are stored.
 *
 * Usage:
 *   node scripts/atp.js                                   # Southend bbox from build-meta
 *   node scripts/atp.js --bbox=-0.10,51.52,0.00,51.59     # custom bbox (no pois merge)
 *   node scripts/atp.js --refresh                         # re-download spider files
 *
 * Downloads per-spider GeoJSON (cached in data/atp/raw/<run>/, git-ignored),
 * keeps features inside the bbox, matches them to listings by name +
 * proximity + postcode, adds atp_hours/atp_brand (OSM hours keep priority
 * in the frontend), and creates pins for unmatched chain features.
 */
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.join(__dirname, '..', 'data');
const RAW = path.join(DATA, 'atp', 'raw');

const RUN_PRIMARY = '2026-09-05-13-32-25';
// Targeted supplements ONLY for failed/missing primary exports, newest
// first. Sep-19 stays ahead of the August runs: its 22 fills are
// verified-working (CI-green, audited) and must not silently re-resolve.
// Older runs fill remaining gaps only (measured 2026-10-06: jollyes_gb
// healthy in Aug-29, cef_gb in Aug-22; the rest fail in all 5 runs
// checked, Aug-15 through Sep-26).
const SUPPLEMENT_RUNS = ['2026-09-19-13-32-18', '2026-08-29-13-32-18', '2026-08-22-13-32-16'];
const runBase = run => `https://alltheplaces-data.openaddresses.io/runs/${run}`;
const UA = { 'User-Agent': 'Indiemaps-atp/1.0 (chain-hours merge; contact via repo)' };

// Merge-time exclusion: the fetch covers every UK-relevant spider, but these
// are not destinations and never become pins or matches. Everything else
// commercial (incl. charity shops, bookmakers, opticians, NCP car parks)
// merges. Deliberately explicit: auditability over cleverness.
const EXCLUDE_SPIDERS = new Set(`glasgow_city_council_kerb_grates_gb glasgow_city_council_street_lamps_gb glasgow_city_council_waste_baskets_gb sheffield_city_council_air_quality_gb sheffield_city_council_benches_gb sheffield_city_council_community_forestry_trees_gb sheffield_city_council_drain_nodes_gb sheffield_city_council_grit_bins_gb sheffield_city_council_litter_bins_gb sheffield_city_council_street_lights_gb sheffield_city_council_street_trees_gb naptan_gb national_rail_gb northern_powergrid_lv_supports_gb northern_railway_gb southeastern_railway_gb southern_railway_gb scotrail_gb transport_for_wales_gb traffic_england_gb traffic_scotland_gb church_of_england_gb church_of_scotland_gb gov_cma_fuel_gb gov_dfe_gias_gb gov_fuel_finder_gb gov_mot_gb nhs_england_gb nhs_scotland_gb changing_places_gb falco_bicycle_parking_gb connected_kerb_gb gridserve_gb insta_volt_gb osprey_gb esb_energy_gb evyve_gb genie_point_gb mer_gb smart_charge_gb believ_gb beev_gb chargy_gb charge_place_scotland_gb justpark_gb cashzone_gb`.split(/\s+/));
// Bare (non-suffixed) spiders with known GB/FI coverage, kept from the
// original hand list plus High-Street and Finnish additions. Everything
// else enters via suffix.
const BARE_KEEP = new Set(`sainsburys lidl mcdonalds burger_king subway pret_a_manger caffe_nero starbucks_eu superdrug argos currys primark poundland ikea shell specsavers waterstones hesburger st1`.split(/\s+/));

const rawArgs = process.argv.slice(2);
const args = {};
for (let i = 0; i < rawArgs.length; i++) {
  const m = rawArgs[i].match(/^--([^=]+)(=(.*))?$/);
  if (m) args[m[1]] = m[3] ?? (rawArgs[i + 1] && !rawArgs[i + 1].startsWith('--') ? rawArgs[++i] : true);
}
const ATP_CACHE = args.cache || RAW; // shared spider downloads across packs
const POIS_PATH = args.pois || path.join(DATA, 'pois.json');
const META_PATH = args.meta || path.join(DATA, 'build-meta.json');
const EXTRACT_PATH = args.extract || path.join(DATA, 'atp', 'extract.json');
// Quarantine (user-decided): listed ids are never created, whatever the
// spider says. The recount backstop (build.py) drops stragglers loudly.
let QUARANTINE = new Set();
try {
  QUARANTINE = new Set(Object.keys(JSON.parse(fs.readFileSync(path.join(DATA, 'quarantine.json'), 'utf8')).ids || {}));
} catch { /* absent list = nothing quarantined */ }

const meta = JSON.parse(fs.readFileSync(META_PATH, 'utf8'));
const bbox = args.bbox ? args.bbox.split(',').map(Number) : [meta.bbox.w, meta.bbox.s, meta.bbox.e, meta.bbox.n];
const [W, S, E, N] = bbox;
const inBbox = (lon, lat) => lon >= W && lon <= E && lat >= S && lat <= N;

fs.mkdirSync(ATP_CACHE, { recursive: true });
fs.writeFileSync(path.join(ATP_CACHE, '.gitignore'), '*\n');

async function fetchOk(url, timeoutMs = 60000) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), timeoutMs);
      const r = await fetch(url, { headers: UA, signal: ctrl.signal });
      clearTimeout(t);
      return r;
    } catch (e) {
      if (attempt === 2) throw e;
      await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
    }
  }
}
async function poolAll(items, limit, fn) {
  const out = [];
  for (let i = 0; i < items.length; i += limit) {
    out.push(...await Promise.all(items.slice(i, i + limit).map(fn)));
  }
  return out;
}

// ---- index-driven fetch (sibling-build importer semantics) ----
const primaryIdx = await (await fetchOk(`${runBase(RUN_PRIMARY)}/stats/_results.json`, 120000)).json();
const primaryRows = new Map(primaryIdx.results.map(r => [r.spider, r]));
// Merge set: every _gb spider minus infrastructure, every _fi spider
// (all 8 are commercial), plus curated bare names. Bbox does the
// geographic filtering — _fi spiders yield nothing outside Finland.
const mergeSpiders = [...new Set([
  ...[...primaryRows.keys()].filter(s => (s.endsWith('_gb') || s.endsWith('_fi')) && !EXCLUDE_SPIDERS.has(s)),
  ...[...BARE_KEEP].filter(s => primaryRows.has(s)),
])].sort();
console.log(`merge set: ${mergeSpiders.length} spiders (excluded ${EXCLUDE_SPIDERS.size} infra)`);
let supplementIdx = null; // lazy: fetched only if a supplement is actually needed
async function supplementRow(spider) {
  // Returns {row, run} from the newest supplement run with a healthy
  // export (features>0 — an errors>0/zero-feature row is a failure, not
  // data, and is skipped, never used).
  if (!supplementIdx) {
    supplementIdx = new Map();
    for (const run of SUPPLEMENT_RUNS) {
      try {
        const j = await (await fetchOk(`${runBase(run)}/stats/_results.json`, 120000)).json();
        for (const r of j.results) {
          if (r.features && !supplementIdx.has(r.spider)) supplementIdx.set(r.spider, { row: r, run });
        }
      } catch { /* unreachable run: remaining runs still tried */ }
    }
  }
  return supplementIdx.get(spider);
}
function countriesOf(stats) {
  const c = {};
  for (const [k, v] of Object.entries(stats || {})) {
    if (k.startsWith('atp/country/')) c[k.slice('atp/country/'.length)] = v;
  }
  return c;
}
async function fetchSpider(spider, row, run) {
  // Returns {status, feats} — feats carry .run provenance. Statuses:
  // ok | empty_export | outside_uk | failed_* (all recorded in the ledger).
  if (!row) return { status: 'missing_in_primary', feats: [] };
  // errors>0 with zero features = the spider FAILED (supplement case);
  // errors==0 with zero features = genuinely empty (used as-is, never
  // backfilled — an older pull could resurrect closed branches).
  if (!row.features) {
    return row.errors
      ? { status: `failed_spider_errors_${row.errors}`, feats: [], reported: 0 }
      : { status: 'empty_export', feats: [], reported: 0 };
  }
  try {
    let countries = {};
    try {
      const r = await fetchOk(`${runBase(run)}/stats/${spider}.json`, 30000);
      if (r.ok) countries = countriesOf(await r.json());
    } catch { /* stats best-effort: unknown coverage still fetches */ }
    const knownNonUk = Object.keys(countries).length > 0
      && Object.keys(countries).every(c => !['GB', 'UK', 'FI', 'unknown', 'Unknown', 'None', ''].includes(c))
      && Object.values(countries).reduce((a, b) => a + b, 0) === row.features;
    if (knownNonUk) return { status: 'outside_uk_by_country_counts', feats: [], countries };
    const file = path.join(ATP_CACHE, run, `${spider}.geojson`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    if (!fs.existsSync(file) || args.refresh) {
      const r = await fetchOk(`${runBase(run)}/output/${spider}.geojson`);
      if (!r.ok) return { status: `failed_http_${r.status}`, feats: [], countries };
      fs.writeFileSync(file, Buffer.from(await r.arrayBuffer()));
    }
    const fc = JSON.parse(fs.readFileSync(file, 'utf8'));
    const all = fc.features || [];
    if (all.length !== row.features) {
      // Incomplete/truncated export: retry once fresh, then fail loudly.
      try { fs.unlinkSync(file); } catch {}
      const r2 = await fetchOk(`${runBase(run)}/output/${spider}.geojson`);
      if (r2.ok) fs.writeFileSync(file, Buffer.from(await r2.arrayBuffer()));
      const fc2 = JSON.parse(fs.readFileSync(file, 'utf8'));
      if ((fc2.features || []).length !== row.features) {
        return { status: `failed_incomplete_export_got_${(fc2.features || []).length}_expected_${row.features}`, feats: [], countries };
      }
      return collectFeats(spider, fc2.features, run, row, countries);
    }
    return collectFeats(spider, all, run, row, countries);
  } catch (e) {
    return { status: `failed_${String(e.cause || e.message || e).slice(0, 80)}`, feats: [] };
  }
}
function collectFeats(spider, all, run, row, countries) {
  const feats = [];
  for (const ft of all) {
    if (ft.geometry?.type !== 'Point') continue;
    const [lon, lat] = ft.geometry.coordinates;
    if (!inBbox(lon, lat)) continue;
    const p = ft.properties || {};
    feats.push({
      spider, run, brand: p.brand || p.name || spider, name: p.name || p.branch || '',
      branch: p.branch || '', lat, lng: lon,
      opening_hours: p.opening_hours || '',
      website: p.website || '',
      phone: p.phone || p['contact:phone'] || '',
      amenity: p.amenity || '', shop: p.shop || '', tourism: p.tourism || '', cuisine: p.cuisine || '',
      housenumber: p['addr:housenumber'] || '', street: p['addr:street'] || '',
      wikidata: p['brand:wikidata'] || null,
      nsi: p['nsi_id'] || null,
      postcode: (p['addr:postcode'] || '').replace(/\s/g, '').toLowerCase() || null,
    });
  }
  return { status: 'ok', feats, reported: row.features, countries };
}

const feats = [];
const ledger = [];
await poolAll(mergeSpiders, 8, async spider => {
  const row = primaryRows.get(spider);
  let res = await fetchSpider(spider, row, RUN_PRIMARY);
  let run = RUN_PRIMARY;
  // Targeted supplement: failed/missing primary exports ONLY. Empty and
  // outside-UK verdicts are used as-is — never backfilled from history.
  if (res.status === 'missing_in_primary' || res.status.startsWith('failed_')) {
    const sup = await supplementRow(spider);
    if (sup) {
      const sres = await fetchSpider(spider, sup.row, sup.run);
      if (sres.status === 'ok') { res = sres; run = sup.run; }
      else ledger.push({ spider, run: sup.run, status: `supplement_${sres.status}`, reported: sup.row.features, bbox_feats: 0 });
    }
  }
  feats.push(...res.feats);
  ledger.push({ spider, run, status: res.status, reported: res.reported ?? row?.features ?? 0, bbox_feats: res.feats.length });
});
ledger.sort((a, b) => a.spider < b.spider ? -1 : 1);
const failed = ledger.filter(l => l.status.startsWith('failed_') || l.status === 'missing_in_primary');
console.log(`${feats.length} ATP features in bbox ${bbox.join(',')} from ${mergeSpiders.length} spiders`);
console.log(`with hours: ${feats.filter(f => f.opening_hours).length} | ledger: ${ledger.filter(l => l.status === 'ok').length} ok, ${ledger.filter(l => l.status === 'empty_export').length} empty, ${ledger.filter(l => l.status === 'outside_uk_by_country_counts').length} non-uk, ${ledger.filter(l => l.run !== RUN_PRIMARY && l.status === 'ok').length} supplemented, ${failed.length} failed`);

fs.mkdirSync(path.dirname(EXTRACT_PATH), { recursive: true });
const extractObj = { runs: { primary: RUN_PRIMARY, supplements: SUPPLEMENT_RUNS }, bbox, count: feats.length, feats };
const extractStr = JSON.stringify(extractObj);
fs.writeFileSync(EXTRACT_PATH, extractStr);
const snapshotSha = crypto.createHash('sha256').update(extractStr).digest('hex');

// ---- match to built listings ----
const normName = s => (s || '').toLowerCase().replace(/\bltd\b|\blimited\b|\bplc\b|\bthe\b/g, '').replace(/&/g, 'and').replace(/[^a-z0-9åäö ]/g, ' ').replace(/\s+/g, ' ').trim(); // åäö retained (FI/SE names)
const STOP = new Set(['and', 'of', 'de', 'la', 's']); // 's' = possessive artifact ("Wendy's" vs "McDonald's")
const GENERIC = new Set(['southend', 'Leigh', 'westcliff', 'chalkwell', 'shoebury', 'shoeburyness', 'thorpe', 'essex', 'london', 'high', 'street', 'road', 'avenue', 'broadway', 'parade', 'town', 'centre', 'center', 'branch', 'store', 'station', 'sea', 'old', 'new', 'north', 'south', 'east', 'west', 'great', 'little', 'upper', 'lower', 'saint', 'on']);
const tokens = (s, minLen = 3) => new Set(normName(s).split(' ').filter(w => w.length >= minLen && !STOP.has(w)));
const tokensAll = s => new Set(normName(s).split(' ').filter(w => w.length >= 1 && !STOP.has(w)));
const concat = s => normName(s).replace(/ /g, '');
function normUrl(u) {
  try {
    let s = String(u || '').trim().toLowerCase().replace(/^(https?:)?\/\//, '').replace(/^www\./, '').replace(/\/$/, '');
    if (!s || /\s/.test(s)) return null;
    const [host, ...rest] = s.split('/');
    const path = rest.join('/').split(/[?#]/)[0];
    return { key: host + (path ? '/' + path : ''), deep: path.length > 0 };
  } catch { return null; }
}
function nameScore(a, b) {
  const ta = tokens(a), tb = tokens(b);
  if (!ta.size || !tb.size) return 0;
  let inter = 0;
  for (const w of ta) if (tb.has(w)) inter++;
  let score = inter / Math.max(ta.size, tb.size);
  const ca = concat(a), cb = concat(b);
  if (Math.min(ca.length, cb.length) >= 8 && (ca.includes(cb) || cb.includes(ca))) score = Math.max(score, 0.9);
  return score;
}

function brandGate(poiName, brand) {
  const bt = tokensAll(brand);
  if (!bt.size) return false;
  const longBt = [...bt].filter(w => w.length >= 2);
  const pt = tokensAll(poiName);
  if (longBt.length) return longBt.some(w => pt.has(w) && !GENERIC.has(w));
  return 'initialism'; // e.g. B&M
}

function matchCandidate(p, f) {
  const ppc = p.postcode ? p.postcode.replace(/\s/g, '').toLowerCase() : null;
  const pc = !!(f.postcode && ppc && f.postcode === ppc);
  const d = distM(p.lat, p.lng, f.lat, f.lng);
  // Pass 0 (exact): shared Wikidata brand ID — deterministic, no fuzzy risk.
  // Chain Reaction / NSI / OSM brand:wikidata all key on these.
  if (p.brand_wikidata && f.wikidata && p.brand_wikidata === f.wikidata && d < 500) {
    return { score: 2.5 - d / 100000, method: 'wikidata' }; // epsilon: nearest branch wins ties
  }
  // Pass 1 (Check-The-Places style): equal store-specific URLs = same place.
  // Bare homepages never qualify (every branch shares them).
  if (p.website && f.website) {
    const a = normUrl(p.website), b = normUrl(f.website);
    if (a && b && a.key === b.key && a.deep && d < 500) return { score: 1.8, method: 'website' };
  }
  if (d > (pc ? 250 : 120)) return null;
  const fn = normName(f.name), pn = normName(p.name);
  if (fn.length >= 6 && fn === pn && (pc || d < 60)) return { score: 2.0 - d / 100000, method: 'exact-name' };
  const gate = brandGate(p.name, f.brand);
  const ns = Math.max(nameScore(p.name, f.name), nameScore(p.name, f.brand), f.branch ? nameScore(p.name, f.branch) : 0);
  if (gate === true && (ns >= 0.5 || (ns >= 0.34 && (d < 60 || pc)))) return { score: ns + (pc ? 0.3 : 0), method: 'name' };
  if (gate === 'initialism' && pc && d < 60) {
    const shared = [...tokensAll(p.name)].filter(w => tokensAll(f.brand).has(w));
    if (shared.length >= 2) return { score: 0.6, method: 'initialism' };
  }
  return null;
}
const distM = (a, b, c, d) => Math.hypot((a - c) * 111320, (b - d) * 62500);

const pois = JSON.parse(fs.readFileSync(POIS_PATH, 'utf8'));
let matched = 0, webMatched = 0, qidMatched = 0, hoursAdded = 0;
const hoursBefore = pois.filter(p => p.opening_hours_osm).length;
for (const p of pois) {
  delete p.hygiene; // hygiene scores retired
  // Created chain pins keep their provenance unless re-matched below: their
  // atp_* fields ARE the record (stripping them on a matcher-gap rerun left
  // sourceless pins — measured with Costa Express). A re-match refreshes
  // everything, upgrading provenance.
  const createdPin = p.atp_method === 'created-chain';
  if (!createdPin) {
    delete p.atp_hours; delete p.atp_brand; delete p.atp_spider; delete p.atp_method; delete p.atp_wikidata; delete p.atp_nsi; delete p.atp_match; // re-merge from scratch
    p.sources = (p.sources || []).filter(s => s !== 'atp');
  }
  const ptoks = tokens(p.name);
  const ptoksAll = tokens(p.name, 1);
  let best = null, bestScore = 0, bestMethod = '', candidates = 0;
  for (const f of feats) {
    const r = matchCandidate(p, f);
    if (r) { candidates++; if (r.score > bestScore) { bestScore = r.score; best = f; bestMethod = r.method; } }
  }
  if (best) {
    matched++;
    best._matched = true;
    if (bestMethod === 'website') webMatched++;
    if (bestMethod === 'wikidata') qidMatched++;
    p.atp_spider = best.spider;
    p.atp_brand = best.brand;
    p.atp_method = bestMethod;
    // Match diagnostics for the per-source debug UI.
    p.atp_match = { score: Math.round(bestScore * 100) / 100, method: bestMethod, candidates };
    if (best.wikidata) p.atp_wikidata = best.wikidata;
    if (best.nsi) p.atp_nsi = best.nsi;
    if (best.opening_hours && !p.atp_hours) {
      p.atp_hours = best.opening_hours;
      if (!p.opening_hours_osm) hoursAdded++;
    }
    if (!p.sources.includes('atp')) p.sources.push('atp');
  }
}
const hoursAfter = pois.filter(p => p.opening_hours_osm || p.atp_hours).length;

// ---- creation pass: unmatched chain features become pins ----
// User-approved 2026-10-06: every spider may create. The creation gate is
// chain-spider provenance (first-party published positions, same trust
// class as ATP hours) + the anti-duplicate check below — NOT proximity
// alone. A feature any existing POI would match is a same-store naming
// variant and is skipped, never created. Re-run safe: stable ids
// (nsi preferred, content hash otherwise) make creation idempotent —
// second runs match the created pin via the normal matcher and skip.
function djb2(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}
// Mirror of mapOsmCategory in scripts/build.js — keep in sync. Tags decide
// first (chain features carry real OSM-style tags); spider keywords cover
// tagless features only; everything else lands honestly in services.
function atpCategory(f) {
  const a = f.amenity, s = f.shop, t = f.tourism;
  if (['restaurant', 'fast_food', 'food_court'].includes(a)) return ['restaurant', 'Restaurant'];
  if (['cafe', 'ice_cream'].includes(a)) return ['cafe', 'Café'];
  if (['pub', 'bar', 'biergarten', 'nightclub'].includes(a)) return ['pub', 'Pub / Bar'];
  if (['pharmacy', 'doctors', 'dentist', 'clinic', 'hospital', 'optician', 'veterinary'].includes(a)) return ['health', 'Health'];
  if (['charity_shop'].includes(a)) return ['shopping', 'Charity shop'];
  if (['theatre', 'cinema', 'arts_centre', 'library', 'place_of_worship'].includes(a) || t === 'museum' || t === 'gallery') return ['culture', 'Culture'];
  if (t === 'hotel' || t === 'guest_house' || t === 'hostel') return ['hotel', 'Hotel'];
  if (t === 'attraction' || t === 'viewpoint') return ['attraction', 'Attraction'];
  if (['parking', 'parking_space', 'bicycle_parking', 'motorcycle_parking'].includes(a)) return ['parking', 'Parking'];
  if (['fuel'].includes(a)) return ['transport', 'Transport'];
  if (/^(st1|neste|teboil)\b/.test(f.spider)) return ['transport', 'Transport'];
  if (['bank', 'bureau_de_change', 'money_transfer'].includes(a)) return ['services', 'Services'];
  if (s) return ['shopping', 'Shop'];
  if (a || t) return ['services', 'Services'];
  const sp = f.spider;
  if (/greggs|mcdonalds|burger_king|hesburger|subway|pizza|kfc|nandos|wagamama|itsu|leon|tortilla|wendys|five_guys|pret|gails|wetherspoon|frankie|harvester|beefeater|toby_carvery|stonehouse|tgi_fridays|ask_italian|wildwood|fireaway|bella_italia|las_iguanas|chiquito|popeyes|taco_bell|creams|wimpy|morleys|pepes|sams_chicken|chicken_cottage|kokoro|zambrero|banana_tree|giggling_squid|franco_manca|pho|cafe_rouge|chef_and_brewer|farmhouse_inns|nicholsons|vintage_inns|ember_inns|hungry_horse|greene_king|youngs|fullers|lounges|belhaven|bar_and_block|miller_and_carter|table_table|hall_and_woodhouse|pubs|inns|tavern|brewery|taproom/.test(sp)) return ['restaurant', 'Restaurant'];
  if (/costa|starbucks|caffe_nero|nero|coffee|cafe|tea|bird_blend|coffee_1|cornish_bakery|patisserie_valerie|paul_gb|benugo/.test(sp)) return ['cafe', 'Café'];
  if (/premier_inn|travelodge|village_hotels|holiday_inn|yha/.test(sp)) return ['hotel', 'Hotel'];
  if (/specsavers|vision_express|leightons|optical_express|my_dentist|damira|bupa|cvs_vets|vets4pets|medivet|dentist|dental|audika|amplifon|scrivens|boots_opticians|opticians/.test(sp)) return ['health', 'Health'];
  // Tagless retail: the shop tag is usually present, but a missing tag must
  // not dump Tesco into services. Bounded keyword list, services otherwise.
  if (/tesco|sainsburys|asda|morrisons|aldi|lidl|spar|londis|budgens|costcutter|nisalocal|keystore|scotmid|coop_food|booker|family_shopper|iceland|heron_foods|farmfoods|argos|currys|primark|poundland|poundstretcher|home_bargains|b_and_m|qd_stores|ikea|dunelm|wickes|halfords|screwfix|pets_at_home|jollyes|card_factory|cardzone|cards_direct|scribbler|boots|superdrug|savers|rowlands|weldricks|day_lewis|matalan|peacocks|new_look|river_island|jd_sports|footasylum|decathlon|go_outdoors|millets|cotswold|cex|ryman|timpson|john_lewis|frasers|fenwick|house_of_fraser|marks_and_spencer|next|clarks|waterstones|the_works|entertainer|smythstoys|toys_r_us|yoursclothing|bonmarche|roman_originals|mint_velvet|whistles|reiss|moss|saltrock|white_stuff|fatface|crew_clothing|weird_fish|shoe_zone|schuh|soletrader|pavers|wynsors|charles_clinkard|hotter|deichmann|jigsaw|h_samuel|fraser_hart|beaverbrooks|warren_james|f_hinds|goldsmiths|oxfam|bhf|cancer_research|sue_ryder|salvation_army|shelter|marie_curie|debra|barnardos|hobbycraft|dfs|scs|oak_furnitureland|furniture_village|bensons|dreams|tapi|topps_tiles|wren_kitchens|magnet|toolstation|travis_perkins|jewson|huws_gray|leyland|dulux|crown_decorating|o2_gb|three_gb|ee_gb|fonehouse|ismash|max_spielmann|richer_sounds|sevenoaks|phone|mobile|book|stationery|fashion|clothes|boutique|jewell|furniture|florist|garden_centre|pets|toy|charity|shopping|retail|store|market|outlet|mall|supermarket|convenience|grocery|department|kioski|intersport|mazda|puuilo|tokmanni|halpa/.test(sp)) return ['shopping', 'Shop'];
  return ['services', 'Services'];
}
const normPc = pc => (pc || '').replace(/\s/g, '').toLowerCase() || null;
let created = 0, skippedDupe = 0, hosted = 0, skippedQuarantine = 0;
const createdIds = [];
const knownIds = new Set(pois.map(p => p.id));
for (const f of feats) {
  if (f._matched || !normName(f.name)) continue;
  // Anti-duplicate: if ANY existing record matches this feature under the
  // same matcher, it is the same store under a variant name — skip.
  if (pois.some(p => matchCandidate(p, f))) { skippedDupe++; continue; }
  // Stable ids MUST include coordinates: nsi_id is brand-level (every
  // Subway shares 'subway-6a374d') — nsi-only ids collapsed whole chains
  // into one pin and silently dropped the rest (measured: Vantaa Dixi
  // wearing Southend's id). Format: atp-<spider>-<nsi|spider>-<hash6>.
  const ref = ((f.nsi || '').replace(/[^a-z0-9_-]/gi, '').slice(0, 24) || f.spider)
    + '-' + djb2(`${f.spider}|${normName(f.name)}|${f.lat.toFixed(5)}|${f.lng.toFixed(5)}`);
  const id = `atp-${f.spider}-${ref}`;
  if (knownIds.has(id)) continue; // already created by an earlier run
  if (QUARANTINE.has(id)) { console.log(`quarantine: refusing to create ${id}`); skippedQuarantine++; continue; }
  const [category, category_label] = atpCategory(f);
  const addr = [f.housenumber, f.street].filter(Boolean).join(' ');
  const rec = {
    id, name: f.name, brand: f.brand,
    ...(f.wikidata ? { brand_wikidata: f.wikidata } : {}),
    category, category_label,
    lat: f.lat, lng: f.lng, geo_precision: 'atp',
    address: addr + (f.postcode ? `${addr ? ', ' : ''}${f.postcode}` : ''),
    postcode: f.postcode,
    ...(f.website ? { website: f.website, website_source: 'atp' } : {}),
    ...(f.phone ? { phone: f.phone, phone_source: 'atp' } : {}),
    ...(f.opening_hours ? { atp_hours: f.opening_hours } : {}),
    ...(f.cuisine ? { cuisine: f.cuisine } : {}),
    photos: [], description: '',
    sources: ['atp'],
    atp_spider: f.spider, atp_brand: f.brand, atp_method: 'created-chain',
    ...(f.wikidata ? { atp_wikidata: f.wikidata } : {}),
    ...(f.nsi ? { atp_nsi: f.nsi } : {}),
    provisional_creation: 'atp-chain',
  };
  // Concession attach: same postcode + housenumber as exactly one
  // store-class record → lives on the host card, no standalone pin.
  // Zero or multiple hosts → standalone pin (ambiguity never attaches).
  if (rec.postcode && f.housenumber) {
    const hn = f.housenumber.trim().toLowerCase();
    const hosts = pois.filter(p => p.category === 'shopping' && !p.hosted_in
      && normPc(p.postcode) === rec.postcode
      && /^\s*(\d+[a-z]?)/i.exec(p.address || '')?.[1]?.toLowerCase() === hn);
    if (hosts.length === 1) {
      rec.hosted_in = hosts[0].id;
      rec.search_only = true;
      hosted++;
    }
  }
  pois.push(rec);
  knownIds.add(id);
  created++;
  createdIds.push(id);
}
fs.writeFileSync(POIS_PATH, JSON.stringify(pois, null, 2));

meta.atp = { runs: { primary: RUN_PRIMARY, supplements: SUPPLEMENT_RUNS }, spiders: mergeSpiders.length, feats_in_bbox: feats.length, feats_with_hours: feats.filter(f => f.opening_hours).length, matched, web_matched: webMatched, wikidata_matched: qidMatched, hours_added: hoursAdded, hours_before: hoursBefore, hours_after: hoursAfter, created, hosted, skipped_dupe: skippedDupe, created_ids: createdIds, snapshot_sha: snapshotSha, complete: failed.length === 0, failed_spiders: failed.map(f => `${f.spider}:${f.status}`), supplemented: Object.fromEntries(ledger.filter(l => l.run !== RUN_PRIMARY && l.status === 'ok').map(l => [l.spider, l.run])), spider_runs: Object.fromEntries(ledger.filter(l => l.status === 'ok').map(l => [l.spider, l.run])) };
fs.writeFileSync(META_PATH, JSON.stringify(meta, null, 2));
console.log(`matched ${matched} listings (${webMatched} via website, ${qidMatched} via wikidata) | hours ${hoursBefore} → ${hoursAfter} (+${hoursAdded} from chains) | created ${created} (${hosted} hosted), skipped ${skippedDupe} same-store variants, ${skippedQuarantine} quarantined`);
