/* Real-data ingestion pipeline — NO fabricated data.
 *
 * Sources (all real, all keyless):
 *  1. FSA Food Hygiene Ratings, Southend-on-Sea (FHRS893) — real business names,
 *     addresses, postcodes and coordinates. (Rating values are not used.)
 *  2. OpenStreetMap via Overpass (Southend bbox) — real names, coordinates,
 *     addr:* tags, opening_hours, phone/website/contact:* tags.
 *  3. postcodes.io — real postcode-centroid coordinates for FSA rows missing a geocode.
 *
 * Merge: FSA rows matched to nearby OSM nodes by name similarity + distance.
 * Anything not verifiable is left EMPTY (frontend renders "unknown") — never invented.
 *
 * Run: npm run build:data
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.join(__dirname, '..', 'data');

const rawArgs = process.argv.slice(2);
const args = {};
for (let i = 0; i < rawArgs.length; i++) {
  const m = rawArgs[i].match(/^--([^=]+)(=(.*))?$/);
  if (m) args[m[1]] = m[3] ?? (rawArgs[i + 1] && !rawArgs[i + 1].startsWith('--') ? rawArgs[++i] : true);
}

// Southend-on-Sea unitary bbox (Leigh → Shoebury) by default; override per pack.
const BBOX = args.bbox
  ? (() => { const [w, s, e, n] = args.bbox.split(',').map(Number); return { s, w, n, e }; })()
  : { s: 51.505, w: 0.615, n: 51.575, e: 0.825 };

// FSA local-authority file id (FHRS893 = Southend-On-Sea). Use --fsa none outside England.
const FSA_ID = args.fsa || '893';
const FSA_URL = `https://ratings.food.gov.uk/OpenDataFiles/FHRS${FSA_ID}en-GB.json`;
const POIS_OUT = args.pois || path.join(DATA, 'pois.json');
const META_OUT = args.meta || path.join(DATA, 'build-meta.json');

// FSA business types excluded from a public-facing map (not customer destinations,
// or no fixed visitable premises)
const FSA_EXCLUDE = new Set([
  'School/college/university',
  'Hospital/Childcare/Caring Premises',
  'Mobile caterer',
  'Manufacturers/packers',
  'Importers/Exporters',
  'Distributors/Transporters',
  'Farm',
  'Other catering premises',
]);

function fsaCategory(t) {
  if (t === 'Restaurant/Cafe/Canteen') return ['restaurant', 'Restaurant / Café'];
  if (t === 'Takeaway/sandwich shop') return ['restaurant', 'Takeaway'];
  if (t === 'Pub/bar/nightclub') return ['pub', 'Pub / Bar'];
  if (t === 'Hotel/bed & breakfast/guest house') return ['hotel', 'Hotel / B&B'];
  if (t.startsWith('Retailers')) return ['shopping', t.includes('upermarket') ? 'Supermarket' : 'Food shop'];
  return ['services', t];
}

function mapOsmCategory(tags = {}) {
  const a = tags.amenity, s = tags.shop, t = tags.tourism, l = tags.leisure;
  if (['restaurant', 'fast_food', 'food_court'].includes(a)) return ['restaurant', 'Restaurant'];
  if (['cafe', 'ice_cream'].includes(a)) return ['cafe', 'Café'];
  if (['pub', 'bar', 'biergarten'].includes(a)) return ['pub', 'Pub / Bar'];
  if (['pharmacy', 'doctors', 'dentist', 'clinic', 'hospital', 'optician'].includes(a)) return ['health', 'Health'];
  if (['theatre', 'cinema', 'arts_centre', 'library', 'place_of_worship'].includes(a) || t === 'museum' || t === 'gallery') return ['culture', 'Culture'];
  if (t === 'hotel' || t === 'guest_house' || t === 'hostel') return ['hotel', 'Hotel'];
  if (t === 'attraction' || t === 'viewpoint' || l === 'park' || l === 'nature_reserve' || l === 'miniature_golf') return ['attraction', 'Attraction'];
  if (s) return ['shopping', 'Shop'];
  if (a || t) return ['services', 'Services'];
  return ['services', 'Services'];
}

const normName = s => (s || '').toLowerCase()
  .replace(/\bltd\b|\blimited\b|\bplc\b|\bthe\b|\boy\b|\bab\b/g, '')
  .replace(/&/g, 'and')
  .replace(/[^a-z0-9åäö ]/g, ' ') // åäö retained: Finnish/Swedish names must survive normalization
  .replace(/\s+/g, ' ')
  .trim();
const tokens = s => new Set(normName(s).split(' ').filter(w => w.length > 2));
const concat = s => normName(s).replace(/ /g, '');
function nameScore(a, b) {
  const ta = tokens(a), tb = tokens(b);
  if (!ta.size || !tb.size) return 0;
  let inter = 0;
  for (const w of ta) if (tb.has(w)) inter++;
  let score = inter / Math.max(ta.size, tb.size); // overlap coefficient vs longer name
  // spacing variants: "peterboat" vs "peter boat inn"
  const ca = concat(a), cb = concat(b);
  if (Math.min(ca.length, cb.length) >= 8 && (ca.includes(cb) || cb.includes(ca))) score = Math.max(score, 0.9);
  return score;
}
const distM = (a, b, c, d) => Math.hypot((a - c) * 111320, (b - d) * 62500);

const OSM_ENDPOINTS = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'];
const sleep = ms => new Promise(r => setTimeout(r, ms));

function osmQuery(s, w, n, e) {
  return `[out:json][timeout:25];(node["amenity"~"^(restaurant|cafe|pub|bar|fast_food|ice_cream|pharmacy|doctors|dentist|cinema|theatre|arts_centre|library|place_of_worship|clinic|optician)$"](${s},${w},${n},${e});node["shop"](${s},${w},${n},${e});node["tourism"~"^(hotel|guest_house|hostel|attraction|museum|gallery|viewpoint)$"](${s},${w},${n},${e});node["leisure"~"^(park|nature_reserve|miniature_golf|sports_centre)$"](${s},${w},${n},${e}););out 400;`;
}

async function queryOnce(q) {
  for (const ep of OSM_ENDPOINTS) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 30000);
      const r = await fetch(ep + '?data=' + encodeURIComponent(q), { headers: { 'User-Agent': 'yellowpages-map-prototype/0.1' }, signal: ctrl.signal });
      clearTimeout(t);
      if (!r.ok) continue;
      const j = await r.json();
      return (j.elements || []).filter(el => el.lat != null && el.lon != null);
    } catch { /* next endpoint */ }
  }
  return null;
}

function splitBbox(s, w, n, e) {
  const mw = (w + e) / 2, ms = (s + n) / 2;
  return [[s, w, ms, mw], [s, mw, ms, e], [ms, w, n, mw], [ms, mw, n, e]];
}

async function fetchBox(s, w, n, e, depth) {
  for (let round = 0; round < 3; round++) {
    const raw = await queryOnce(osmQuery(s, w, n, e));
    if (raw) {
      // Cap guard on the RAW count: 'out 400' truncates before name
      // filtering, so a capped response with unnamed nodes slips past a
      // named-only check and silently drops qualifying POIs (measured:
      // Wendy's + Nagawa missing from a 388-named pull). Named filtering
      // happens after the tiling decision.
      if (raw.length >= 400 && depth < 2) {
        // silent truncation guard: 'out 400' caps dense bboxes (e.g.
        // Westminster keeps only 400 arbitrary OSM nodes). Sub-tile and merge.
        console.log(`OSM cap hit (${s},${w},${n},${e}) — sub-tiling`);
        const seen = new Map(), sums = { raw: 0 };
        for (const [ts, tw, tn, te] of splitBbox(s, w, n, e)) {
          const sub = await fetchBox(ts, tw, tn, te, depth + 1);
          for (const el of sub.list) seen.set(el.id, el);
          sums.raw += sub.raw;
        }
        return { list: [...seen.values()], raw: sums.raw };
      }
      return { list: raw.filter(el => el.tags?.name), raw: raw.length };
    }
    if (round < 2) await sleep(15000 * (round + 1)); // Overpass storms pass
  }
  throw new Error(`Overpass unavailable: ${s},${w},${n},${e}`);
}

async function fetchOsm() {
  const { s, w, n, e } = BBOX;
  try {
    return await fetchBox(s, w, n, e, 0);
  } catch (e) {
    // last resort for ultra-dense bboxes (e.g. City of London): tile blindly.
    // Tiles go back through fetchBox so cap-splitting still applies at depth.
    console.log('full-bbox Overpass query failing — sub-tiling 2x2');
    const seen = new Map();
    let raw = 0;
    for (const [ts, tw, tn, te] of splitBbox(s, w, n, e)) {
      try {
        const sub = await fetchBox(ts, tw, tn, te, 1);
        for (const el of sub.list) seen.set(el.id, el);
        raw += sub.raw;
      } catch (e2) {
        throw new Error(`Overpass tile failed: ${ts},${tw},${tn},${te}`);
      }
    }
    console.log(`tiled query: ${seen.size} unique nodes`);
    return { list: [...seen.values()], raw };
  }
}

// Real postcode coordinates for FSA rows missing a geocode (postcode centroid)
async function geocodeMissing(rows) {
  const missing = rows.filter(r => r.lat == null && r.postcode);
  console.log(`geocoding ${missing.length} postcodes via postcodes.io…`);
  for (let i = 0; i < missing.length; i += 100) {
    const batch = missing.slice(i, i + 100);
    try {
      const r = await fetch('https://api.postcodes.io/postcodes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ postcodes: batch.map(b => b.postcode) }),
      });
      if (!r.ok) continue;
      const j = await r.json();
      j.result.forEach((res, k) => {
        if (res.result) {
          batch[k].lat = res.result.latitude;
          batch[k].lng = res.result.longitude;
          batch[k].geo_precision = 'postcode';
        }
      });
    } catch { /* leave unknown */ }
    await new Promise(r => setTimeout(r, 200));
  }
}

let fsa = null;
if (FSA_ID !== 'none') {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      fsa = await (await fetch(FSA_URL)).json();
      break;
    } catch (e) {
      if (attempt === 2) throw new Error(`FSA download failed: ${FSA_URL}`);
      await new Promise(r => setTimeout(r, 10000 * (attempt + 1)));
    }
  }
}
const all = fsa ? fsa.FHRSEstablishment.EstablishmentCollection : [];
const extractDate = fsa ? fsa.FHRSEstablishment.Header.ExtractDate : null;
if (fsa) console.log(`FSA extract ${extractDate}, ${all.length} establishments`);
else console.log('FSA skipped (--fsa none): OSM-only base');

const kept = [];
for (const e of all) {
  if (FSA_EXCLUDE.has(e.BusinessType)) continue;
  const g = e.Geocode || {};
  const addr = [e.AddressLine1, e.AddressLine2, e.AddressLine3, e.AddressLine4].map(x => (x || '').trim()).filter(Boolean);
  kept.push({
    fsa_id: e.FHRSID,
    name: (e.BusinessName || '').trim(),
    type: e.BusinessType,
    address_lines: addr,
    postcode: (e.PostCode || '').trim() || null,
    lat: g.Latitude != null ? Number(g.Latitude) : null,
    lng: g.Longitude != null ? Number(g.Longitude) : null,
    geo_precision: g.Latitude != null ? 'fsa' : null,
  });
}
console.log(`kept ${kept.length} public-facing FSA rows (excluded ${all.length - kept.length})`);

await geocodeMissing(kept);
const geoKept = kept.filter(k => k.lat != null);
console.log(`with coordinates: ${geoKept.length} (dropped ${kept.length - geoKept.length} with no location)`);

const { list: osm, raw: osmRaw } = await fetchOsm();
console.log(`OSM named nodes: ${osm.length} (raw response: ${osmRaw})`);

// Match FSA ↔ OSM
let matched = 0;
const usedOsm = new Set();
for (const k of geoKept) {
  let best = null, bestScore = 0, bestDist = null, candidates = 0;
  for (const el of osm) {
    if (usedOsm.has(el.id)) continue;
    const d = distM(k.lat, k.lng, el.lat, el.lon);
    if (d > 150) continue;
    candidates++;
    const ns = nameScore(k.name, el.tags.name);
    const postcodeHit = k.postcode && el.tags['addr:postcode'] && el.tags['addr:postcode'].replace(/\s/g, '').toLowerCase() === k.postcode.replace(/\s/g, '').toLowerCase();
    const score = ns + (postcodeHit ? 0.3 : 0) + (d < 50 ? 0.1 : 0);
    const accept = ns >= 0.5 || (ns >= 0.34 && (d < 60 || postcodeHit));
    if (accept && score > bestScore) { bestScore = score; best = el; bestDist = d; }
  }
  if (best) { k.osm = best; usedOsm.add(best.id); matched++; }
  // Match diagnostics for the per-source debug UI: why this row did or
  // did not merge (candidates = OSM nodes within 150m).
  k.osm_match = best
    ? { score: Math.round(bestScore * 100) / 100, dist_m: Math.round(bestDist), candidates }
    : { score: null, dist_m: null, candidates };
}
console.log(`FSA↔OSM matched: ${matched}`);

function osmContact(tags) {
  return {
    phone: tags.phone || tags['contact:phone'] || '',
    email: tags.email || tags['contact:email'] || '',
    website: tags.website || tags['contact:website'] || '',
    facebook: tags['contact:facebook'] || '',
    instagram: tags['contact:instagram'] || '',
    twitter: tags['contact:twitter'] || '',
  };
}
function osmAddress(tags) {
  const parts = [tags['addr:housenumber'], tags['addr:street'], tags['addr:suburb'] || tags['addr:district'], tags['addr:city'] || tags['addr:town'] || tags['addr:village'], tags['addr:postcode']].filter(Boolean);
  return parts.join(', ');
}

const pois = [];
for (const k of geoKept) {
  const [cat, label] = fsaCategory(k.type);
  const tags = k.osm?.tags || {};
  const [osmCat, osmLabel] = k.osm ? mapOsmCategory(tags) : [null, null];
  // Prefer OSM's more specific category for pubs/cafés when FSA says generic restaurant
  const category = (cat === 'restaurant' && osmCat && ['pub', 'cafe'].includes(osmCat)) ? osmCat : cat;
  const category_label = (cat === 'restaurant' && osmCat && ['pub', 'cafe'].includes(osmCat)) ? osmLabel : label;
  const contact = osmContact(tags);
  const address = k.address_lines.join(', ') + (k.postcode ? `, ${k.postcode}` : '');
  pois.push({
    id: `fsa-${k.fsa_id}`,
    fsa_id: k.fsa_id,
    osm_type: k.osm ? 'node' : null,
    osm_id: k.osm ? k.osm.id : null,
    name: k.name,
    category, category_label,
    lat: k.osm ? k.osm.lat : k.lat,   // OSM position is survey-accurate; FSA geocode otherwise
    lng: k.osm ? k.osm.lon : k.lng,
    geo_precision: k.osm ? 'osm' : k.geo_precision,
    address, postcode: k.postcode,
    ...contact,
    opening_hours_osm: tags.opening_hours || '',
    opening_hours: {},
    cuisine: tags.cuisine || '',
    brand_wikidata: tags['brand:wikidata'] || null,
    // OSM-asserted article links only (never name-guessed downstream):
    // wikipedia is 'lang:Title', wikidata is 'Q…'.
    wikipedia: tags.wikipedia || null,
    wikidata: tags.wikidata || null,
    amenities: [tags.cuisine && `Cuisine: ${tags.cuisine}`, tags.takeaway === 'yes' && 'Takeaway', tags.outdoor_seating === 'yes' && 'Outdoor seating', tags.wheelchair === 'yes' && 'Wheelchair accessible'].filter(Boolean),
    photos: [],
    description: '',
    sources: k.osm ? ['fsa', 'osm'] : ['fsa'],
    osm_match: k.osm_match,
  });
}
// Unmatched OSM nodes (non-food + anything FSA missed) — real tags only
for (const el of osm) {
  if (usedOsm.has(el.id)) continue;
  const t = el.tags;
  const [category, osmLabel] = mapOsmCategory(t);
  pois.push({
    id: `osm-node-${el.id}`,
    fsa_id: null,
    osm_type: 'node', osm_id: el.id,
    name: t.name,
    category, category_label: t.cuisine ? `${osmLabel} · ${t.cuisine.split(';')[0]}` : osmLabel,
    lat: el.lat, lng: el.lon, geo_precision: 'osm',
    address: osmAddress(t), postcode: t['addr:postcode'] || null,
    ...osmContact(t),
    opening_hours_osm: t.opening_hours || '',
    opening_hours: {},
    cuisine: t.cuisine || '',
    brand_wikidata: t['brand:wikidata'] || null,
    wikipedia: t.wikipedia || null,
    wikidata: t.wikidata || null,
    amenities: [t.cuisine && `Cuisine: ${t.cuisine}`, t.takeaway === 'yes' && 'Takeaway', t.outdoor_seating === 'yes' && 'Outdoor seating', t.wheelchair === 'yes' && 'Wheelchair accessible'].filter(Boolean),
    photos: [],
    description: '',
    sources: ['osm'],
  });
}

pois.sort((a, b) => a.name.localeCompare(b.name));
fs.mkdirSync(path.dirname(POIS_OUT), { recursive: true });
fs.writeFileSync(POIS_OUT, JSON.stringify(pois, null, 2));
fs.mkdirSync(path.dirname(META_OUT), { recursive: true });
fs.writeFileSync(META_OUT, JSON.stringify({
  built_at: new Date().toISOString(),
  fsa_extract_date: extractDate,
  fsa_authority: fsa ? `FHRS${FSA_ID}` : null,
  bbox: BBOX,
  counts: {
    total: pois.length,
    fsa_only: pois.filter(p => p.sources.length === 1 && p.sources[0] === 'fsa').length,
    merged: pois.filter(p => p.sources.includes('fsa') && p.sources.includes('osm')).length,
    osm_only: pois.filter(p => p.sources.length === 1 && p.sources[0] === 'osm').length,
    osm_nodes_pulled: osm.length,
    osm_raw_response: osmRaw,
  },
  with_hours: pois.filter(p => p.opening_hours_osm).length,
  with_phone: pois.filter(p => p.phone).length,
  with_website: pois.filter(p => p.website).length,
}, null, 2));
console.log(`wrote ${pois.length} POIs → ${POIS_OUT}`);
