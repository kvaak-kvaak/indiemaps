/* Merge AllThePlaces chain hours (CC0, first-party data) into the built listings.
 *
 * Usage:
 *   node scripts/atp.js                                   # Southend bbox from build-meta
 *   node scripts/atp.js --bbox=-0.10,51.52,0.00,51.59     # custom bbox (no pois merge)
 *   node scripts/atp.js --refresh                         # re-download spider files
 *
 * Downloads per-spider GeoJSON (cached in data/atp/raw/, git-ignored),
 * keeps features inside the bbox, matches them to listings by name +
 * proximity + postcode, and adds atp_hours/atp_brand (OSM hours keep priority
 * in the frontend). Never invents: only chain-published strings are stored.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.join(__dirname, '..', 'data');
const RAW = path.join(DATA, 'atp', 'raw');

const RUN_ID = '2026-09-05-13-32-25';
const RUN_BASE = `https://alltheplaces-data.openaddresses.io/runs/${RUN_ID}/output`;

const SPIDERS = `tesco_gb sainsburys asda_gb morrisons_gb aldi_sud_gb lidl spar_gb
londis_gb greggs_gb mcdonalds burger_king subway pizza_hut_gb papa_johns_gb
five_guys_gb nandos_gb_ie wagamama_gb pizza_express_gb zizzi_gb_ie gails_bakery_gb
pret_a_manger caffe_nero starbucks_eu costa_coffee_gg_gb_im_je itsu_gb leon_gb
tortilla_gb boots_gb superdrug well_gb j_d_wetherspoon greene_king_pubs_gb
hungry_horse_gb ember_inns_gb vintage_inns_gb toby_carvery_gb stonehouse_gb
barclays_gb hsbc_uk_gb lloyds_bank_gb natwest_gb nationwide_gb halifax_gb tsb_gb
wendys_gb halfords_gb kwik_fit_gb screwfix_gb pets_at_home_gb argos currys primark poundland
home_bargains_gb b_and_m_gb ikea dunelm_gb wickes_gb card_factory_gb texaco_gb_ie
shell bp_pulse_gb`.split(/\s+/);

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

const meta = JSON.parse(fs.readFileSync(META_PATH, 'utf8'));
const bbox = args.bbox ? args.bbox.split(',').map(Number) : [meta.bbox.w, meta.bbox.s, meta.bbox.e, meta.bbox.n];
const [W, S, E, N] = bbox;
const inBbox = (lon, lat) => lon >= W && lon <= E && lat >= S && lat <= N;

fs.mkdirSync(ATP_CACHE, { recursive: true });
fs.writeFileSync(path.join(ATP_CACHE, '.gitignore'), '*\n');

const feats = [];
for (const spider of SPIDERS) {
  const file = path.join(ATP_CACHE, `${spider}.geojson`);
  if (!fs.existsSync(file) || args.refresh) {
    const r = await fetch(`${RUN_BASE}/${spider}.geojson`);
    if (!r.ok) { console.log(`${spider}: HTTP ${r.status}, skipped`); continue; }
    fs.writeFileSync(file, Buffer.from(await r.arrayBuffer()));
  }
  let fc;
  try { fc = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { continue; }
  for (const ft of fc.features || []) {
    if (ft.geometry?.type !== 'Point') continue;
    const [lon, lat] = ft.geometry.coordinates;
    if (!inBbox(lon, lat)) continue;
    const p = ft.properties || {};
    feats.push({
      spider, brand: p.brand || p.name || spider, name: p.name || p.branch || '',
      branch: p.branch || '', lat, lng: lon,
      opening_hours: p.opening_hours || '',
      website: p.website || '',
      wikidata: p['brand:wikidata'] || null,
      nsi: p['nsi_id'] || null,
      postcode: (p['addr:postcode'] || '').replace(/\s/g, '').toLowerCase() || null,
    });
  }
}
console.log(`${feats.length} ATP features in bbox ${bbox.join(',')}`);
console.log(`with hours: ${feats.filter(f => f.opening_hours).length}`);

fs.mkdirSync(path.dirname(EXTRACT_PATH), { recursive: true });
fs.writeFileSync(EXTRACT_PATH, JSON.stringify({ run_id: RUN_ID, bbox, count: feats.length, feats }, null, 2));

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
  delete p.atp_hours; delete p.atp_brand; delete p.atp_spider; delete p.atp_method; delete p.atp_wikidata; delete p.atp_nsi; delete p.atp_match; // re-merge from scratch
  p.sources = (p.sources || []).filter(s => s !== 'atp');
  const ptoks = tokens(p.name);
  const ptoksAll = tokens(p.name, 1);
  let best = null, bestScore = 0, bestMethod = '', candidates = 0;
  for (const f of feats) {
    const r = matchCandidate(p, f);
    if (r) { candidates++; if (r.score > bestScore) { bestScore = r.score; best = f; bestMethod = r.method; } }
  }
  if (best) {
    matched++;
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
fs.writeFileSync(POIS_PATH, JSON.stringify(pois, null, 2));

meta.atp = { run_id: RUN_ID, spiders: SPIDERS.length, feats_in_bbox: feats.length, feats_with_hours: feats.filter(f => f.opening_hours).length, matched, web_matched: webMatched, wikidata_matched: qidMatched, hours_added: hoursAdded, hours_before: hoursBefore, hours_after: hoursAfter };
fs.writeFileSync(META_PATH, JSON.stringify(meta, null, 2));
console.log(`matched ${matched} listings (${webMatched} via website, ${qidMatched} via wikidata) | hours ${hoursBefore} → ${hoursAfter} (+${hoursAdded} from chains)`);
