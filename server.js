import express from 'express';
import cors from 'cors';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

const POIS = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'pois.json'), 'utf8'));
let REVIEWS = {};
try {
  REVIEWS = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'reviews.json'), 'utf8'));
} catch { REVIEWS = {}; }

// ---- helpers ----
function inBbox(poi, bbox) {
  if (!bbox) return true;
  const [s, w, n, e] = bbox.split(',').map(Number);
  if ([s, w, n, e].some(isNaN)) return true;
  return poi.lat >= s && poi.lat <= n && poi.lng >= w && poi.lng <= e;
}

function richness(poi) {
  // Detail completeness: share of real fields actually present (never invented)
  const fields = ['phone', 'email', 'website', 'opening_hours_osm', 'atp_hours', 'site_hours', 'nhs_hours', 'amenities', 'cuisine', 'facebook', 'instagram'];
  let score = 0;
  for (const f of fields) {
    const v = poi[f];
    if (Array.isArray(v) ? v.length > 0 : (v !== undefined && v !== null && v !== '' && !(typeof v === 'object'))) score++;
  }
  return Math.round((score / fields.length) * 100);
}

function mapOsmCategory(tags = {}) {
  const a = tags.amenity, s = tags.shop, t = tags.tourism, l = tags.leisure;
  if (['restaurant', 'fast_food', 'food_court'].includes(a)) return { category: 'restaurant', label: 'Restaurant' };
  if (['cafe', 'ice_cream'].includes(a)) return { category: 'cafe', label: 'Café' };
  if (['pub', 'bar', 'biergarten'].includes(a)) return { category: 'pub', label: 'Pub / Bar' };
  if (['pharmacy', 'doctors', 'dentist', 'clinic', 'hospital', 'optician'].includes(a)) return { category: 'health', label: 'Health' };
  if (['theatre', 'cinema', 'arts_centre', 'library', 'place_of_worship'].includes(a) || t === 'museum' || t === 'gallery') return { category: 'culture', label: 'Culture' };
  if (t === 'hotel' || t === 'guest_house' || t === 'hostel') return { category: 'hotel', label: 'Hotel' };
  if (t === 'attraction' || t === 'viewpoint' || l === 'park' || l === 'nature_reserve' || l === 'miniature_golf') return { category: 'attraction', label: 'Attraction' };
  if (s) return { category: 'shopping', label: 'Shop' };
  if (a || t) return { category: 'services', label: 'Services' };
  return { category: 'services', label: 'Services' };
}

function osmToPoi(el) {
  const tags = el.tags || {};
  if (!tags.name) return null;
  const lat = el.lat ?? el.center?.lat;
  const lon = el.lon ?? el.center?.lon;
  if (lat == null || lon == null) return null;
  const { category, label } = mapOsmCategory(tags);
  const addrParts = [tags['addr:housenumber'], tags['addr:street'], tags['addr:suburb'], tags['addr:city'] || tags['addr:town'] || tags['addr:village'], tags['addr:postcode']].filter(Boolean);
  const poi = {
    id: `osm-${el.type}-${el.id}`,
    osm_type: el.type,
    osm_id: el.id,
    name: tags.name,
    category,
    category_label: tags.cuisine ? `${label} · ${tags.cuisine.split(';')[0]}` : label,
    lat,
    lng: lon,
    address: addrParts.join(', ') || '',
    phone: tags.phone || tags['contact:phone'] || '',
    email: tags.email || tags['contact:email'] || '',
    website: tags.website || tags['contact:website'] || '',
    facebook: tags['contact:facebook'] || '',
    instagram: tags['contact:instagram'] || '',
    twitter: tags['contact:twitter'] || '',
    price_level: null,
    rating: null,
    review_count: 0,
    description: tags.description || '',
    opening_hours_osm: tags.opening_hours || '',
    opening_hours: {},
    amenities: [tags.cuisine && `Cuisine: ${tags.cuisine}`, tags.takeaway === 'yes' && 'Takeaway', tags.outdoor_seating === 'yes' && 'Outdoor seating', tags.wheelchair === 'yes' && 'Wheelchair accessible', tags['diet:vegan'] === 'yes' && 'Vegan options', tags['diet:vegetarian'] === 'yes' && 'Vegetarian options'].filter(Boolean),
    photos: [],
    verified: false,
    source: 'osm',
    cuisine: tags.cuisine || '',
  };
  poi.richness = richness(poi);
  return poi;
}

// simple in-memory cache for Overpass
let liveCache = { key: '', at: 0, data: [] };

async function fetchLiveOsm(bboxStr) {
  // Default to Southend / Essex view if no bbox
  const bbox = bboxStr || '51.50,0.60,51.57,0.82';
  const [s, w, n, e] = bbox.split(',').map(Number);
  const cacheKey = `${s.toFixed(3)},${w.toFixed(3)},${n.toFixed(3)},${e.toFixed(3)}`;
  if (liveCache.key === cacheKey && Date.now() - liveCache.at < 5 * 60 * 1000) return liveCache.data;

  // Nodes only + tag-filtered: keeps Overpass fast (<2s) and skips anonymous
  // buildings/ways. Ways rarely carry POI names; named POIs are overwhelmingly nodes.
  const query = `[out:json][timeout:20];(node["amenity"~"^(restaurant|cafe|pub|bar|fast_food|ice_cream|pharmacy|doctors|dentist|cinema|theatre|arts_centre|library|place_of_worship|clinic|optician)$"](${s},${w},${n},${e});node["shop"](${s},${w},${n},${e});node["tourism"~"^(hotel|guest_house|hostel|attraction|museum|gallery|viewpoint)$"](${s},${w},${n},${e});node["leisure"~"^(park|nature_reserve|miniature_golf|sports_centre)$"](${s},${w},${n},${e}););out 250;`;

  const endpoints = ['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'];
  for (const ep of endpoints) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 22000);
      // NOTE: GET, not POST — Overpass returns 406 for undici POST bodies
      const res = await fetch(ep + '?data=' + encodeURIComponent(query), { headers: { 'User-Agent': 'yellowpages-map-prototype/0.1 (contact: prototype@localhost)' }, signal: ctrl.signal });
      clearTimeout(t);
      if (!res.ok) continue;
      const json = await res.json();
      const pois = (json.elements || []).map(osmToPoi).filter(Boolean).filter(p => p.name && p.name.length > 1).slice(0, 300);
      liveCache = { key: cacheKey, at: Date.now(), data: pois };
      return pois;
    } catch { /* try next endpoint */ }
  }
  return liveCache.data || [];
}

// ---- API ----

// Curated Yellow-Pages POIs (rich detail guaranteed)
app.get('/api/pois', (req, res) => {
  const { bbox, category, q } = req.query;
  let out = POIS.map(p => ({ ...p, richness: richness(p), community_reviews: (REVIEWS[p.id] || []).length }));
  if (bbox) out = out.filter(p => inBbox(p, bbox));
  if (category && category !== 'all') out = out.filter(p => p.category === category);
  if (q) {
    const needle = q.toLowerCase();
    out = out.filter(p => (p.name + ' ' + p.category_label + ' ' + p.address + ' ' + p.description).toLowerCase().includes(needle));
  }
  res.json({ count: out.length, pois: out });
});

// Live OSM POIs (raw coverage layer — deliberately thinner, shows the problem we solve)
app.get('/api/live-pois', async (req, res) => {
  try {
    const pois = await fetchLiveOsm(req.query.bbox);
    res.json({ count: pois.length, pois, notice: 'Live OpenStreetMap: real names/positions, but hours & contact are often unmapped.' });
  } catch (err) {
    res.status(502).json({ count: 0, pois: [], error: 'Overpass unavailable' });
  }
});

// Unified: curated + live, de-duplicated roughly by name+proximity
app.get('/api/combined', async (req, res) => {
  const { bbox, category, q } = req.query;
  let curated = POIS.map(p => ({ ...p, richness: richness(p) }));
  if (bbox) curated = curated.filter(p => inBbox(p, bbox));
  if (category && category !== 'all') curated = curated.filter(p => p.category === category);
  let live = [];
  try { live = await fetchLiveOsm(req.query.bbox); } catch { live = []; }
  if (category && category !== 'all') live = live.filter(p => p.category === category);
  // de-dupe: drop OSM POIs within ~60m with same-ish name as curated
  const norm = s => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  live = live.filter(lp => !curated.some(cp => {
    const dLat = (lp.lat - cp.lat) * 111320, dLng = (lp.lng - cp.lng) * 62500;
    const dist = Math.hypot(dLat, dLng);
    return dist < 60 && (norm(lp.name).includes(norm(cp.name).slice(0, 6)) || norm(cp.name).includes(norm(lp.name).slice(0, 6)));
  }));
  let all = [...curated, ...live];
  if (q) {
    const needle = q.toLowerCase();
    all = all.filter(p => (p.name + ' ' + (p.category_label || '') + ' ' + (p.address || '')).toLowerCase().includes(needle));
  }
  res.json({ curated: curated.length, live: live.length, count: all.length, pois: all });
});

app.get('/api/pois/:id', (req, res) => {
  const poi = POIS.find(p => p.id === req.params.id);
  if (!poi) return res.status(404).json({ error: 'Not found. IDs rebuild from real sources — search /api/pois instead.' });
  res.json({ ...poi, richness: richness(poi), reviews: REVIEWS[poi.id] || [] });
});

app.get('/api/reviews/:id', (req, res) => {
  res.json(REVIEWS[req.params.id] || []);
});

app.post('/api/reviews/:id', (req, res) => {
  const { author, rating, text } = req.body || {};
  if (!author || !text || !(rating >= 1 && rating <= 5)) return res.status(400).json({ error: 'author, rating 1-5 and text required' });
  const review = { author: String(author).slice(0, 60), rating: Number(rating), text: String(text).slice(0, 2000), date: new Date().toISOString().slice(0, 10) };
  REVIEWS[req.params.id] = [...(REVIEWS[req.params.id] || []), review];
  // best-effort persist (prototype store)
  try { fs.writeFileSync(path.join(__dirname, 'data', 'reviews.json'), JSON.stringify(REVIEWS, null, 2)); } catch {}
  res.status(201).json(review);
});

// Search: curated first, then Nominatim (no key) for addresses
app.get('/api/search', async (req, res) => {
  const q = (req.query.q || '').trim();
  if (!q) return res.json({ pois: [], places: [] });
  const needle = q.toLowerCase();
  const pois = POIS.filter(p => (p.name + ' ' + p.category_label + ' ' + p.address).toLowerCase().includes(needle)).map(p => ({ ...p, richness: richness(p) })).slice(0, 10);
  let places = [];
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&viewbox=0.55,51.60,0.85,51.50&bounded=1&q=${encodeURIComponent(q)}`;
    const r = await fetch(url, { headers: { 'User-Agent': 'yellowpages-map-prototype/0.1 (contact: prototype@localhost)' } });
    if (r.ok) places = (await r.json()).map(p => ({ display_name: p.display_name, lat: Number(p.lat), lng: Number(p.lon), type: p.type, class: p.class }));
  } catch {}
  res.json({ pois, places });
});

// Real photos from Wikimedia Commons (no key): geosearch near the POI, ranked by
// name match. Most small businesses have none — the frontend says so honestly.
const photoCache = new Map();
app.get('/api/photo', async (req, res) => {
  const { name = '', lat, lng } = req.query;
  if (lat == null || lng == null) return res.json({ photos: [] });
  const key = `${Number(lat).toFixed(4)},${Number(lng).toFixed(4)}|${name.slice(0, 40).toLowerCase()}`;
  if (photoCache.has(key)) return res.json(photoCache.get(key));
  const out = { photos: [] };
  try {
    const url = 'https://commons.wikimedia.org/w/api.php?action=query&format=json&origin=*'
      + `&generator=geosearch&ggscoord=${lat}|${lng}&ggsradius=250&ggslimit=20&ggsnamespace=6`
      + '&prop=imageinfo&iiprop=url|size|mediatype|extmetadata&iiurlwidth=900';
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 12000);
    const r = await fetch(url, { headers: { 'User-Agent': 'yellowpages-map-prototype/0.1' }, signal: ctrl.signal });
    clearTimeout(t);
    if (!r.ok) return res.json(out);
    const j = await r.json();
    const pages = Object.values(j.query?.pages || {});
    const nameTokens = new Set(name.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(' ').filter(w => w.length > 3));
    const scored = [];
    for (const p of pages) {
      const info = p.imageinfo?.[0];
      if (!info || info.mediatype !== 'BITMAP' || (info.width || 0) < 400) continue;
      if (/\.svg|\.pdf$/i.test(p.title)) continue;
      const titleTokens = new Set(p.title.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(' ').filter(w => w.length > 3));
      let inter = 0;
      for (const w of nameTokens) if (titleTokens.has(w)) inter++;
      scored.push({ score: inter, info, title: p.title });
    }
    scored.sort((a, b) => b.score - a.score);
    const stripHtml = s => (s || '').replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').slice(0, 90);
    out.photos = scored.slice(0, 4).map(({ info, title }) => ({
      thumb: info.thumburl || info.url,
      url: info.descriptionurl || info.url,
      title: title.replace(/^File:/, '').replace(/\.[a-z]+$/i, '').slice(0, 80),
      author: stripHtml(info.extmetadata?.Artist?.value),
    }));
  } catch { /* no photos — honest empty */ }
  if (photoCache.size > 2000) photoCache.clear();
  photoCache.set(key, out);
  res.json(out);
});

// Community correction suggestions — stored as-is, clearly user-contributed
app.post('/api/suggest', (req, res) => {
  const { poiId, poiName, field, value } = req.body || {};
  if (!poiId || !field || !value) return res.status(400).json({ error: 'poiId, field and value required' });
  const entry = {
    date: new Date().toISOString().slice(0, 10),
    poiId: String(poiId).slice(0, 80),
    poiName: String(poiName || '').slice(0, 120),
    field: String(field).slice(0, 40),
    value: String(value).slice(0, 500),
  };
  try {
    const file = path.join(__dirname, 'data', 'suggestions.json');
    const arr = JSON.parse(fs.readFileSync(file, 'utf8'));
    arr.push(entry);
    fs.writeFileSync(file, JSON.stringify(arr, null, 2));
  } catch {}
  res.status(201).json({ ok: true });
});
// Wikipedia enrichment proxy (no key) — real editorial summaries for notable places
app.get('/api/enrich', async (req, res) => {
  const name = (req.query.name || '').trim();
  if (!name) return res.json({});
  try {
    const r = await fetch(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(name)}`, { headers: { 'User-Agent': 'yellowpages-map-prototype/0.1' } });
    if (!r.ok) return res.json({});
    const j = await r.json();
    if (j.type === 'disambiguation' || !j.extract) return res.json({});
    res.json({ title: j.title, extract: j.extract, thumbnail: j.thumbnail?.source || '', url: j.content_urls?.desktop?.page || '' });
  } catch { res.json({}); }
});

// Build provenance: when the dataset was built and from which FSA extract
app.get('/api/meta', (req, res) => {
  try {
    res.json(JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'build-meta.json'), 'utf8')));
  } catch { res.json({}); }
});

// Where premium providers would plug in (Google Places, Foursquare, Yelp, TripAdvisor)
app.get('/api/config', (req, res) => {
  res.json({
    providers: {
      fsa: { status: 'active', note: 'Food Standards Agency FHRS open data (Southend-on-Sea) — real business names + addresses. No key.' },
      osm_overpass: { status: 'active', note: 'Live OpenStreetMap — real names/positions/hours/contact where mapped. No key.' },
      postcodes_io: { status: 'active', note: 'Real postcode coordinates for records missing a geocode. No key.' },
      nominatim: { status: 'active', note: 'Address search, no key' },
      wikipedia: { status: 'active', note: 'Real editorial summaries for notable places, no key' },
      wikimedia_commons: { status: 'active', note: 'Real nearby photos via /api/photo, no key' },
      google_places: { status: process.env.GOOGLE_PLACES_KEY ? 'active' : 'not-configured', note: 'Set GOOGLE_PLACES_KEY to add real reviews, photos, hours' },
      foursquare: { status: process.env.FOURSQUARE_KEY ? 'active' : 'not-configured', note: 'Set FOURSQUARE_KEY for rich venue details' },
      yelp: { status: process.env.YELP_KEY ? 'active' : 'not-configured', note: 'Set YELP_KEY for reviews/photos' }
    }
  });
});

app.use(express.static(path.join(__dirname, 'public')));
app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));

app.listen(PORT, () => console.log(`Yellow-Pages map on http://localhost:${PORT} — Essex/Southend prototype`));
