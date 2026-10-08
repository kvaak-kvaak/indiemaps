/* Essex Yellow-Pages Map — honest frontend.
 * Every field shown comes from a real source (FSA / OSM / chain feeds /
 * business websites / visitor contributions). Anything unverified renders
 * as "unknown" — never invented. */
const SOUTHEND = [51.5414, 0.7120];
const state = { pois: [], meta: {}, curatedCount: 0, liveCount: 0, cat: 'all', mode: 'curated', openOnly: false, q: '', selectedId: null, markers: new Map(), pack: '', packs: [], auditFsaOnly: false, showHidden: false, diff: null };
const packQ = () => state.pack ? `pack=${encodeURIComponent(state.pack)}` : '';
const packName = () => (state.packs.find(p => p.id === state.pack) || {}).name || 'Listings';

const map = L.map('map', { zoomControl: false }).setView(SOUTHEND, 13);
L.control.zoom({ position: 'bottomright' }).addTo(map);
// Basemap: OSM standard raster (keyless). Kept as a named constant so the
// provider can switch without a software update (OSMF tile policy asks for
// exactly this). Fallback candidate if ever blocked: humanitarian layer
// at tile.openstreetmap.fr/hot. Attribution must stay visible (policy).
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
L.tileLayer(TILE_URL, {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>', subdomains: 'abc', maxZoom: 19
}).addTo(map);
const clusters = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 46 });
map.addLayer(clusters);
const diffLayers = L.layerGroup();
map.addLayer(diffLayers);

const $ = s => document.querySelector(s);
const resultsEl = $('#results'), statsText = $('#stats-text');
const CAT_ICON = { restaurant: '🍽️', cafe: '☕', pub: '🍺', shopping: '🛍️', hotel: '🛏️', attraction: '🎡', culture: '🎭', health: '⚕️', services: '✂️', parking: '🅿️', transport: '🚌' };

const CAT_TINT = { restaurant: '#fdecea', cafe: '#fef6e0', pub: '#f3e8dc', shopping: '#e8f0fe', hotel: '#ede7f6', attraction: '#e6f4ea', culture: '#feefe3', health: '#e0f7fa', services: '#eceff1', parking: '#e3f2fd', transport: '#e8eaf6' };

// Pin icon granularity (user decision 2026-10-08): packs carry no OSM tag
// detail, so one icon per category is all that's natively expressible.
// Resolution order below — chain spider, then name keywords, then cuisine,
// then the category fallback. Tints stay category-level. Pure frontend:
// works on any pack, no rebuild.
const SPIDER_ICON = {
  mcdonalds: '🍔', burger_king: '🍔', hesburger: '🍔', wendys: '🍔', five_guys: '🍔',
  kfc_gb: '🍗', popeyes_gb: '🍗', taco_bell_gb: '🌮', tortilla_gb: '🌯',
  subway: '🥪', greggs_gb: '🥐', gails_bakery_gb: '🥐', pret_a_manger: '🥐',
  pizza_hut_gb: '🍕', papa_johns_gb: '🍕', pizza_express_gb: '🍕', dominos_pizza_gb: '🍕', fireaway_gb: '🍕',
  nandos_gb_ie: '🍗', wagamama_gb: '🍜', itsu_gb: '🍱', tortilla_gb: '🌯',
  costa_coffee_gg_gb_im_je: '☕', starbucks_eu: '☕', caffe_nero: '☕',
  j_d_wetherspoon: '🍺', greene_king_pubs_gb: '🍺',
};
const NAME_ICON = [
  [/bank|halifax|natwest|hsbc|lloyds|barclays|nationwide|santander|tsb\b/, '🏦'],
  [/pharmacy|chemist|boots|superdrug|well pharmacy/, '💊'],
  [/post office|postoffice/, '📮'],
  [/church|cathedral|chapel|mosque|synagogue|temple\b/, '⛪'],
  [/station\b|railway/, '🚉'],
  [/hotel|guest ?house|b&b\b|inn\b/, '🛏️'],
  [/book|waterstones|library/, '📚'],
  [/charity|oxfam|barnardo|salvation army|british heart|cancer research|sue ryder/, '❤️'],
  [/flower|florist/, '💐'],
  [/pet|vets\b|vet\b/, '🐾'],
  [/dentist|dental|doctor|surgery|clinic|optician|specsavers/, '⚕️'],
  [/gym|fitness|pool\b|swimming/, '🏋️'],
  [/cinema|theatre|theater|museum|gallery/, '🎭'],
  [/hair|barber|beauty|nails|tattoo/, '💇'],
  [/car|garage|mot\b|tyre|kwik fit|halfords/, '🚗'],
  [/launder|dry clean/, '🧺'],
  [/bakery|cake\b|patisserie/, '🧁'],
  [/fish|chippy|chip shop/, '🐟'],
  [/kebab|turkish/, '🥙'],
  [/chinese|noodle|wok/, '🍜'],
  [/indian|curry|tandoori|balti/, '🍛'],
  [/pizza|italian/, '🍕'],
  [/sushi|japanese/, '🍣'],
  [/chicken|peri|nando|kfc\b/, '🍗'],
  [/burger|mcdonald|wendy|five guys/, '🍔'],
  [/coffee|costa|starbucks|nero\b/, '☕'],
  [/pub\b|bar\b|tavern|wetherspoon|greene king/, '🍺'],
  [/school|college|academy/, '🏫'],
];
const CUISINE_ICON = [
  [/pizza|italian/, '🍕'], [/burger|american/, '🍔'], [/sushi|japanese/, '🍣'],
  [/chinese|noodle/, '🍜'], [/indian|curry|bangladeshi|pakistani|nepalese/, '🍛'],
  [/turkish|kebab|lebanese|greek/, '🥙'], [/mexican|taco|burrito/, '🌯'],
  [/thai|vietnamese|malaysian/, '🍜'], [/fish|seafood/, '🐟'],
  [/bakery|cake|patisserie|dessert|ice_cream/, '🧁'], [/coffee|tea/, '☕'],
  [/chicken/, '🍗'], [/vegan|vegetarian/, '🥗'], [/breakfast|brunch/, '🍳'],
];
function pinIcon(p) {
  if (p.atp_spider && SPIDER_ICON[p.atp_spider]) return SPIDER_ICON[p.atp_spider];
  const nm = (p.name || '').toLowerCase();
  for (const [re, icon] of NAME_ICON) if (re.test(nm)) return icon;
  const cu = ((p.site_cuisine || []).join(' ') + ' ' + (p.cuisine || '')).toLowerCase();
  for (const [re, icon] of CUISINE_ICON) if (re.test(cu)) return icon;
  return CAT_ICON[p.category] || '📍';
}

// ---------- opening-hours / open-now (parsed from REAL OSM opening_hours strings only) ----------
const DAY_ORDER = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
const jsDayToOsm = d => ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'][d];
function toMin(t) { const m = t.trim().match(/(\d{1,2}):(\d{2})/); return m ? (+m[1]) * 60 + (+m[2]) : null; }
function expandDays(a, b) {
  const i = DAY_ORDER.indexOf(a), j = DAY_ORDER.indexOf(b);
  if (i < 0 || j < 0) return [];
  const out = []; let k = i;
  for (let n = 0; n < 7; n++) { out.push(DAY_ORDER[k]); if (k === j) break; k = (k + 1) % 7; }
  return out;
}
function parseOsmHours(str) {
  const table = {};
  if (!str) return table;
  if (/24\s*\/\s*7/i.test(str)) { DAY_ORDER.forEach(d => table[d] = [[0, 1440]]); return table; }
  for (const part of str.split(';')) {
    const m = part.trim().match(/^([A-Za-z]{2}(?:-[A-Za-z]{2})?(?:,[A-Za-z]{2}(?:-[A-Za-z]{2})?)*)\s+(.+)$/);
    if (!m) continue;
    const dayExpr = m[1], times = m[2];
    let days = [];
    for (const chunk of dayExpr.split(',')) {
      const c = chunk.trim();
      if (c.includes('-')) { const [a, b] = c.split('-').map(s => s.trim().slice(0, 2)[0].toUpperCase() + s.trim().slice(1, 2).toLowerCase()); days.push(...expandDays(a, b)); }
      else days.push(c.slice(0, 2)[0].toUpperCase() + c.slice(1, 2).toLowerCase());
    }
    const ranges = [];
    for (const t of times.split(',')) {
      const r = t.trim().match(/(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})/);
      if (r) { const o = toMin(r[1]), c = toMin(r[2]); if (o != null && c != null) ranges.push([o, c === 0 ? 1440 : c]); }
      else if (/off|closed/i.test(t)) { ranges.length = 0; break; }
    }
    for (const d of days) { if (DAY_ORDER.includes(d)) table[d] = (table[d] || []).concat(ranges); }
  }
  return table;
}
function openStatus(poi, now = new Date()) {
  const table = parseOsmHours(effHours(poi));
  if (!Object.keys(table).length) return { state: 'unknown', label: 'Hours unknown' };
  const today = jsDayToOsm(now.getDay());
  const mins = now.getHours() * 60 + now.getMinutes();
  const ranges = table[today] || [];
  for (const [o, c] of ranges) {
    if (mins >= o && mins < c) {
      const closes = `${String(Math.floor(c / 60) % 24).padStart(2, '0')}:${String(c % 60).padStart(2, '0')}`;
      return { state: 'open', label: `Open now · closes ${closes}` };
    }
    if (mins < o) {
      const opens = `${String(Math.floor(o / 60)).padStart(2, '0')}:${String(o % 60).padStart(2, '0')}`;
      return { state: 'closed', label: `Closed · opens ${opens} today` };
    }
  }
  return { state: 'closed', label: 'Closed now' };
}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtDate = iso => { try { return new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); } catch { return iso; } };

function sourceBadges(p) {
  const keep = (p.keep_evidence || []).map(e =>
    `<span class="badge src-fsa" title="Kept on the map by: ${esc(e.meaning)}">keeper · ${esc(e.rule)}</span>`).join('');
  const extra = (p.position_approx ? '<span class="badge stacked" title="Area-placed: position comes from an uncorroborated batch geocode — placed by postcode area, not surveyed">area-placed</span>' : '')
    + (p.position_hazard ? '<span class="badge stacked" title="Needs manual placement: on a road class where geocoders fail and no surveyed position exists">needs-manual-placement</span>' : '')
    + (p.unresolved_why === 'stale-orphan' ? `<span class="badge stacked" title="Hidden: map record with no corroboration, untouched since ${esc((p.osm_touched || '').slice(0, 10)) || 'unknown date'}">stale orphan</span>` : '')
    + (p.search_only ? '<span class="badge stacked" title="Search-only record: no standalone map marker">search-only</span>' : '');
  return keep + extra + (p.sources || [p.source]).map(s =>
    s === 'fsa' ? '<span class="badge src-fsa" title="Food Standards Agency open data">FSA</span>'
    : s === 'atp' ? '<span class="badge src-atp" title="Chain-published data via AllThePlaces">chains</span>'
    : s === 'site' ? '<span class="badge src-site" title="Hours stated on the business website">website</span>'
    : s === 'nhs' ? '<span class="badge src-nhs" title="NHS listed pharmacy (England)">NHS</span>'
    : s === 'overture' ? '<span class="badge src-overture" title="Phone/website backfilled from Overture Maps">Overture</span>'
    : s === 'servicemap' ? '<span class="badge src-sm" title="City of Helsinki Service Map (CC BY 4.0)">HKI map</span>'
    : s === 'ta' ? '<span class="badge src-ta" title="Archived research data, c.2021 (stale by design)">archive</span>'
    : s === 'chain' ? '<span class="badge src-atp" title="Chain-published data (Restel/Raflaamo, first-party)">chains</span>'
    : s === 'prh' ? '<span class="badge src-ch" title="Finnish Trade Register: registered business, uninspected">PRH</span>'
    : s === 'ch' ? '<span class="badge src-ch" title="Companies House: incorporated business, uninspected">companies</span>'
    : s === 'osm' ? '<span class="badge src-osm" title="OpenStreetMap contributors">OSM</span>'
    // Unknown sources render under their own name — never borrowed. (A
    // fallthrough OSM label once masqueraded Overture contributions.)
    : `<span class="badge src-osm" title="Unrecognised source key">${esc(s)}</span>`).join('');
}
// Effective hours: OSM mapping first, then chain, site, NHS, municipal,
// stale archive last — every differing source is shown, never merged.
const effHours = p => p.opening_hours_osm || p.atp_hours || p.site_hours || p.nhs_hours || p.sm_hours || p.chain_hours || p.ta_hours || '';
const tblKey = s => JSON.stringify(parseOsmHours(s || ''));
function hourVariants(p) {
  const v = [];
  if (p.opening_hours_osm) v.push(['Mapped on OpenStreetMap', p.opening_hours_osm]);
  if (p.atp_hours) v.push([`Published by ${p.atp_brand || 'the chain'}`, p.atp_hours]);
  if (p.site_hours) v.push(['On the business website', p.site_hours]);
  if (p.nhs_hours) v.push(['NHS listed hours', p.nhs_hours]);
  if (p.sm_hours) v.push(['Municipal listing (Helsinki Service Map)', p.sm_hours]);
  if (p.chain_hours) v.push([`Published by ${p.chain_brand || 'the chain'} (chain site)`, p.chain_hours]);
  if (p.ta_hours) v.push(['Archived research data (c.2021)', p.ta_hours]);
  if (p.site_alt) v.push(['Also stated on the business website', p.site_alt]);
  return v;
}

// ---------- data loading ----------
function bboxStr() { const b = map.getBounds(); return `${b.getSouth().toFixed(4)},${b.getWest().toFixed(4)},${b.getNorth().toFixed(4)},${b.getEast().toFixed(4)}`; }

async function loadPois() {
  if (state.diff) { renderDiff(); return; }
  statsText.textContent = 'Loading…';
  try {
    // Audit + hidden modes need the server-hidden records too — same
    // endpoints, just with the include flag (server supports it).
    const hz = (state.auditFsaOnly || state.showHidden) ? 'include_hazard=1' : '';
    const withHz = url => url + (hz ? (url.includes('?') ? '&' : '?') + hz : '');
    const [metaR, poisR] = await Promise.all([
      fetch('/api/meta' + (packQ() ? '?' + packQ() : '')).then(r => r.json()).catch(() => ({})),
      state.mode === 'curated'
        ? fetch(withHz('/api/pois' + (packQ() ? '?' + packQ() : ''))).then(r => r.json())
        : fetch(withHz('/api/combined?bbox=' + encodeURIComponent(bboxStr()) + (packQ() ? '&' + packQ() : ''))).then(r => r.json()),
    ]);
    state.meta = metaR;
    state.pois = poisR.pois || [];
    state.curatedCount = poisR.curated ?? poisR.count ?? state.pois.length;
    state.liveCount = poisR.live ?? 0;
  } catch { state.pois = []; }
  renderAll();
  const id = new URLSearchParams(location.search).get('poi');
  if (id && state.pois.some(p => p.id === id)) selectPoi(id, { pan: true });
}

let moveT;
map.on('moveend', () => { clearTimeout(moveT); moveT = setTimeout(() => { if (state.mode === 'all') loadPois(); }, 600); });

function filtered() {
  return state.pois.filter(p => {
    // FSA-only audit view: exactly the register-positioned population —
    // FSA in sources, no OSM corroboration — nothing else.
    if (state.auditFsaOnly && (!(p.sources || []).includes('fsa') || (p.sources || []).includes('osm'))) return false;
    // Hidden view: exactly the server-hidden population (hazard tier +
    // stale orphans) — each card shows its reason. Nothing else.
    if (state.showHidden && !(p.position_hazard || p.unresolved_why === 'stale-orphan')) return false;
    if (state.cat !== 'all' && p.category !== state.cat) return false;
    if (state.cat === 'all' && p.category === 'parking') return false; // parking off by default — opt in via the 🅿️ chip
    if (state.q && !(p.name + ' ' + (p.category_label || '') + ' ' + (p.address || '')).toLowerCase().includes(state.q)) return false;
    if (state.openOnly && openStatus(p).state !== 'open') return false;
    return true;
  }).sort((a, b) => {
    const src = x => (x.sources?.includes('fsa') && x.sources?.includes('osm')) ? 2 : x.sources?.includes('fsa') ? 1 : 0;
    return (src(b) - src(a)) || a.name.localeCompare(b.name);
  });
}

// ---------- rendering: markers + list ----------
function renderAll() {
  clusters.clearLayers(); state.markers.clear();
  if (state.diff) { renderDiff(); return; }
  diffLayers.clearLayers();
  const list = filtered();
  for (const p of list) {
    const el = document.createElement('div');
    // Confidence-gated display: batch-placed pins (position_approx) render
    // with an uncertainty halo instead of full-authority markers. Same
    // class will cover the hazard tier (step 3) wherever reachable.
    el.className = `pin cat-${p.category}${p.sources?.length === 1 && p.sources[0] === 'osm' ? ' osm' : ''}${p.position_approx ? ' approx' : ''}${p.id === state.selectedId ? ' selected' : ''}`;
    el.innerHTML = `<span>${pinIcon(p)}</span>`;
    const m = L.marker([p.lat, p.lng], { icon: L.divIcon({ className: '', html: el.outerHTML, iconSize: [30, 30], iconAnchor: [15, 28] }), title: p.name });
    m.on('click', () => selectPoi(p.id, { pan: false }));
    clusters.addLayer(m); state.markers.set(p.id, m);
  }
  const fsaDate = state.meta.fsa_extract_date ? ` · FSA extract ${state.meta.fsa_extract_date}` : '';
  const built = state.meta.built_at ? ` · verified ${fmtDate(state.meta.built_at.slice(0, 10))}` : '';
  statsText.textContent = state.mode === 'curated'
    ? `${state.auditFsaOnly ? 'AUDIT — register-positioned pins only (postcode-grade, not surveyed) · ' : ''}${state.showHidden ? 'SHOWING UNRESOLVED — pins held off the map with reasons · ' : ''}${list.length} listings${state.cat === 'all' ? ' (parking hidden — 🅿️ to show)' : ''} · ${packName()}${fsaDate}${built}`
    : `${list.length} shown (${state.curatedCount} listed + ${state.liveCount} live OSM) · ${packName()}`;
  resultsEl.innerHTML = list.slice(0, 200).map(p => {
    const o = openStatus(p);
    return `<div class="card${p.id === state.selectedId ? ' selected' : ''}" data-id="${p.id}">
      <div class="tile" style="background:${CAT_TINT[p.category] || '#eee'}">${pinIcon(p)}</div>
      <div><h3>${esc(p.name)}</h3>
        <div class="meta">${esc(p.category_label || p.category)}</div>
        <div class="meta">${esc((p.address || '').split(',').slice(0, 2).join(','))}</div>
        <div class="open ${o.state === 'open' ? 'yes' : o.state === 'closed' ? 'no' : 'unk'}">${o.state === 'unknown' ? 'Hours unknown' : esc(o.label)}</div>
        <div class="badges">${sourceBadges(p)}${p.atp_hours ? '<span class="badge src-atp">chain hours</span>' : ''}</div>
      </div></div>`;
  }).join('') || `<p style="padding:16px;color:#666">No matches. Try another category or zoom out.</p>`;
  resultsEl.querySelectorAll('.card').forEach(c => c.addEventListener('click', () => selectPoi(c.dataset.id, { pan: true })));
}

// ---------- detail panel ----------
let heroPhotos = [], heroIdx = 0;
let hitMarker = null;
function clearHitMarker() { if (hitMarker) { map.removeLayer(hitMarker); hitMarker = null; } }
function showHitMarker(p) {
  clearHitMarker();
  const el = document.createElement('div');
  el.className = `pin cat-${p.category} selected`;
  el.innerHTML = `<span>${pinIcon(p)}</span>`;
  hitMarker = L.marker([p.lat, p.lng], { icon: L.divIcon({ className: '', html: el.outerHTML, iconSize: [30, 30], iconAnchor: [15, 28] }), title: p.name, zIndexOffset: 1000 });
  hitMarker.addTo(map);
}
async function selectPoi(id, { pan } = {}) {
  state.selectedId = id;
  let p = state.pois.find(x => x.id === id);
  if (!p) {
    // Search-only / hosted records are not in the browse payload — fetch by id.
    try {
      const r = await fetch(`api/pois/${encodeURIComponent(id)}${state.pack ? `?pack=${encodeURIComponent(state.pack)}` : ''}`);
      if (!r.ok) return;
      p = await r.json();
    } catch { return; }
  }
  if (pan) map.flyTo([p.lat, p.lng], Math.max(map.getZoom(), 15), { duration: 0.7 });
  history.replaceState(null, '', `?poi=${encodeURIComponent(id)}`);
  renderAll();
  // Pin on hit: search-only records have no browse marker — place a
  // transient one on selection so the map shows what the card describes.
  if (!state.markers.has(p.id)) showHitMarker(p); else clearHitMarker();
  $('#detail').classList.remove('hidden');
  $('#detail-body').innerHTML = detailSkeleton(p);
  wireDetail(p);
  loadPhotos(p);
  loadReviews(p);
  loadWiki(p);
}

function detailSkeleton(p) {
  const o = openStatus(p);
  const tel = p.phone ? `tel:${p.phone.replace(/\s/g, '')}` : '';
  const gUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.name + ', ' + (p.address || 'Southend-on-Sea'))}`;
  const taUrl = `https://www.tripadvisor.co.uk/Search?q=${encodeURIComponent(p.name + ' ' + (p.address || ''))}`;
  return `
  <div class="hero" id="hero"><div class="hero-empty"><span>${pinIcon(p)}</span><p>No photo on record.</p></div></div>
  <div class="dpad">
    <h2>${esc(p.name)}</h2>
    <div class="sub">${esc(p.category_label || p.category)}${(p.site_cuisine || [])[0] ? ` · ${esc(p.site_cuisine.join(', '))}` : p.cuisine ? ` · ${esc(p.cuisine)}` : ''}${p.site_price ? ` · ${esc(p.site_price)}` : ''}</div>
    <div class="drow">${sourceBadges(p)}${p.atp_hours ? '<span class="badge src-atp" title="Opening hours as published by the chain (AllThePlaces)">chain hours</span>' : ''}</div>
    ${p.alias ? `<div class="drow" style="font-size:12.5px;color:#555">formerly <b>${esc(p.alias.registered)}</b> (registered name)${p.alias.verified ? ` · verified ${esc(p.alias.verified)}` : ''}</div>` : ''}
    ${p.superseded_by ? `<div class="drow" style="font-size:12.5px;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:6px 9px">⚠️ may have been replaced here by <b>${esc(p.superseded_by.name)}</b> — mapper data not yet updated</div>` : ''}
    ${p.host ? `<div class="drow" style="font-size:12.5px">🏬 concession inside <a href="#" data-poi="${esc(p.host.id)}"><b>${esc(p.host.name)}</b></a> (same address, chain-published)</div>` : ''}
    ${p.hosted?.length ? `<div class="drow" style="font-size:12.5px">🏬 also here: ${p.hosted.map(h => `<a href="#" data-poi="${esc(h.id)}">${esc(h.name)}</a>`).join(' · ')}</div>` : ''}
    <div class="drow open ${o.state === 'open' ? 'yes' : o.state === 'closed' ? 'no' : 'unk'}">● ${esc(o.label)}${o.state === 'unknown' ? ' — not mapped yet' : ''}</div>
    <div class="actions">
      <a class="act primary" style="text-decoration:none" target="_blank" href="https://www.google.com/maps/dir/?api=1&destination=${p.lat},${p.lng}"><span>🧭</span>Directions</a>
      ${p.phone ? `<a class="act" style="text-decoration:none;color:inherit" href="${tel}"><span>📞</span>Call</a>` : `<button class="act" disabled style="opacity:.4" title="No phone number on record"><span>📞</span>No phone</button>`}
      ${p.website ? `<a class="act" style="text-decoration:none;color:inherit" target="_blank" href="${esc(p.website)}"><span>🌐</span>Website</a>` : `<button class="act" disabled style="opacity:.4" title="No website on record"><span>🌐</span>No site</button>`}
      ${p.site_menu ? `<a class="act" style="text-decoration:none;color:inherit" target="_blank" href="${esc(p.site_menu.url)}" title="Menu on the business website${p.site_menu.kind === 'pdf' ? ' (PDF)' : p.site_menu.kind === 'third-party' ? ' (third party)' : ''}"><span>📖</span>Menu</a>` : ''}
    </div>
    <div class="hr"></div>
    <div class="sec"><h4>About</h4><div id="wiki"><p style="font-size:13px;color:#666;margin:0">Checking Wikipedia for this place…</p></div>${ratingHtml(p)}${p.site_description ? `<div class="wiki" style="margin-top:10px;background:#fffdf4;border-color:#f0e6c8"><b>In their own words</b> <span style="color:#888;font-size:11px">from the business website</span><br/>${esc(p.site_description)}</div>` : ''}</div>
    <div class="hr"></div>
    <div class="sec"><h4>Opening hours</h4>${hoursHtml(p)}</div>
    <div class="hr"></div>
    <div class="sec"><h4>Contact & details</h4>
      ${p.address ? `<div class="kv"><span class="k">📍</span><span>${esc(p.address)}</span></div>` : `<div class="kv"><span class="k">📍</span><span style="color:#999">No address on record</span></div>`}
      ${p.phone ? `<div class="kv"><span class="k">📞</span><a href="${tel}">${esc(p.phone)}</a> <span style="color:#888;font-size:11px">(${p.phone_source === 'overture' ? 'via Overture' : p.phone_source === 'atp' ? 'via AllThePlaces' : p.phone_source === 'chain' ? 'via Finnish chain data' : p.phone_source === 'servicemap' ? 'via Service Map' : 'as mapped on OSM'})</span></div>` : ''}
      ${p.email ? `<div class="kv"><span class="k">✉️</span><a href="mailto:${esc(p.email)}">${esc(p.email)}</a></div>` : ''}
      ${p.website ? `<div class="kv"><span class="k">🌐</span><a target="_blank" href="${esc(p.website)}">${esc(prettyUrl(p.website))}</a> <span style="color:#888;font-size:11px">(${p.website_source === 'overture' ? 'via Overture' : p.website_source === 'atp' ? 'via AllThePlaces' : p.website_source === 'servicemap' ? 'via Service Map' : 'as mapped on OSM'})</span></div>` : ''}
      ${(p.facebook || p.instagram || p.twitter) ? `<div class="kv"><span class="k">📣</span><span>${p.facebook ? `<a target="_blank" href="${esc(p.facebook)}">Facebook</a> · ` : ''}${p.instagram ? `<a target="_blank" href="${esc(p.instagram)}">Instagram</a> · ` : ''}${p.twitter ? `<a target="_blank" href="${esc(p.twitter)}">X/Twitter</a>` : ''}</span></div>` : ''}
      ${p.opening_hours_osm ? `<div class="kv"><span class="k">🕒</span><span style="font-family:monospace;font-size:12px">${esc(p.opening_hours_osm)} <span style="color:#888">(as mapped on OSM)</span></span></div>` : ''}
    </div>
    ${p.amenities?.length ? `<div class="hr"></div><div class="sec"><h4>Listed features <span style="font-weight:400;text-transform:none">(from OpenStreetMap tags)</span></h4><div class="chipset">${p.amenities.map(a => `<span>${esc(a)}</span>`).join('')}</div></div>` : ''}
    <div class="hr"></div>
    <div class="sec"><h4>Reviews</h4>
      <p style="font-size:12.5px;color:#555;background:#f6f8ff;border:1px solid #dfe7ff;border-radius:10px;padding:8px 10px">No imported reviews — we don't copy Google/TripAdvisor content. Read them at the source, or leave a visitor note below.</p>
      <div style="display:flex;gap:8px;margin:8px 0">
        <a class="act" style="flex:1;text-decoration:none;color:inherit;text-align:center" target="_blank" href="${gUrl}"><span>⭐</span>Google reviews</a>
        <a class="act" style="flex:1;text-decoration:none;color:inherit;text-align:center" target="_blank" href="${taUrl}"><span>🦉</span>TripAdvisor</a>
      </div>
      <div id="reviews"><p style="color:#666;font-size:13px">Loading visitor notes…</p></div>
      <form id="rev-form"><b style="font-size:13px">Leave a visitor note</b>
        <input id="rev-name" placeholder="Your name" maxlength="60" required />
        <select id="rev-rating"><option value="5">★★★★★ (5)</option><option value="4">★★★★ (4)</option><option value="3">★★★ (3)</option><option value="2">★★ (2)</option><option value="1">★ (1)</option></select>
        <textarea id="rev-text" rows="3" placeholder="e.g. opening hours correct? still trading?" maxlength="2000" required></textarea>
        <button type="submit">Post note</button>
      </form>
    </div>
    <div class="hr"></div>
    <div class="sec"><h4>Spotted something wrong?</h4>
      <form id="suggest-form">
        <div style="display:flex;gap:6px">
          <select id="sug-field" style="flex:1"><option value="opening_hours">Opening hours</option><option value="phone">Phone</option><option value="website">Website</option><option value="address">Address</option><option value="closed">Permanently closed</option><option value="other">Other</option></select>
        </div>
        <input id="sug-value" placeholder="Correct information" maxlength="500" required style="width:100%;margin:6px 0;padding:9px;border:1px solid var(--line);border-radius:8px;font-size:13px" />
        <button type="submit" style="background:#fff;border:1px solid var(--line);border-radius:8px;padding:9px;width:100%;cursor:pointer;font-weight:700">Suggest a correction</button>
      </form>
    </div>
    <div class="hr"></div>
    <div class="sec"><h4>Where this info comes from</h4>
      <div style="font-size:12.5px;color:#444;line-height:1.7">
      ${(p.sources || []).includes('fsa') ? `· <b>Name & address</b> — Food Standards Agency open data (extract ${esc(state.meta.fsa_extract_date || '?')})<br/>` : ''}
      ${(p.sources || []).includes('osm') ? `· <b>Position, hours & contact</b> — OpenStreetMap contributors${p.osm_id ? ` (node ${p.osm_id})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('overture') ? `· <b>Phone & website backfill</b> — Overture Maps${p.overture_id ? ` (GERS ${esc(p.overture_id.slice(0, 8))}…)` : ''}<br/>` : ''}
      ${(p.sources || []).includes('atp') ? (p.atp_method === 'created-chain' ? `· <b>New pin</b> — store record as published by ${esc(p.atp_brand || 'the chain')}, via AllThePlaces (CC0); position from the chain, not surveyed<br/>` : `· <b>Opening hours</b> — as published by ${esc(p.atp_brand || 'the chain')}, via AllThePlaces (CC0)<br/>`) : ''}
      ${(p.sources || []).includes('nhs') ? `· <b>Listed pharmacy</b> — NHS England${p.nhs_ods ? ` (ODS ${esc(p.nhs_ods)})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('servicemap') ? `· <b>Municipal listing</b> — City of Helsinki Service Map (CC BY 4.0)${p.sm_id ? ` (unit ${esc(String(p.sm_id))})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('ta') ? `· <b>Cuisines, dietary notes${p.ta_hours ? ', hours' : ''}${p.ta_rec_n != null ? ', recommend score' : ''}</b> — archived research data (stale by design)<br/>` : ''}
      ${(p.sources || []).includes('chain') ? `· <b>Chain record</b> — as published by ${esc(p.chain_brand || 'the chain')} (${esc(p.chain_source || 'chain site')})<br/>` : ''}
      ${(p.sources || []).includes('prh') ? `· <b>Registered business</b> — Finnish Trade Register${p.prh_number ? ` (${esc(p.prh_number)})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('ch') ? `· <b>Registered business</b> — Companies House${p.ch_incorporated ? `, incorporated ${esc(p.ch_incorporated)}` : ''} (registered office, may differ from trading address; uninspected)<br/>` : ''}
      ${(p.fsa_alias || []).length ? `· <b>Also registered as</b> — ${p.fsa_alias.map(a => `${esc(a.name)} (FHRS ${a.fsa_id})`).join('; ')}<br/>` : ''}
      ${(p.supersedes || []).length ? `· <b>Replaces at these premises</b> — ${p.supersedes.map(s => esc(s.name)).join('; ')}<br/>` : ''}
      ${(p.sources || []).includes('site') ? `· <b>Opening hours${p.site_image ? ', photo' : ''}${p.site_menu ? ', menu' : ''}${p.site_description ? ', description' : ''}</b> — from the business website${p.site_url ? ` (<a target="_blank" href="${esc(p.site_url)}">source</a>, ${esc(p.site_method || 'parsed')})` : ''}<br/>` : ''}
      · <b>Position accuracy</b> — ${p.geo_precision === 'postcode' ? 'postcode area (approximate)' : 'mapped point'}
      </div>
      ${debugHtml(p)}
    </div>
    <div style="height:20px"></div>
  </div>`;
}

function debugHtml(p) {
  // Per-source match diagnostics for datamash debugging: which rows won,
  // by what score/distance, from how many candidates. Renders only keys
  // the build actually stored — never invented.
  const rows = [];
  const src = p.sources || [];
  if (src.includes('fsa')) rows.push(['FSA', `FHRS ${p.fsa_id ?? '?'} · extract ${esc(state.meta.fsa_extract_date || '?')}`]);
  if (src.includes('osm')) {
    const m = p.osm_match || {};
    rows.push(['OSM', `${esc(p.osm_type || '?')} ${p.osm_id ?? '?'}${p.osm_id ? ` (<a target="_blank" href="https://www.openstreetmap.org/${p.osm_type || 'node'}/${p.osm_id}">view</a>)` : ''}`
      + (m.score != null ? ` · score ${m.score}, ${m.dist_m ?? '?'} m, ${m.candidates ?? '?'} candidates` : ' · base record, no merge decision')]);
  }
  if (src.includes('overture')) {
    const m = p.overture_match || {};
    rows.push(['Overture', `GERS ${esc(p.overture_id || '?')}${m.name ? ` · matched “${esc(m.name)}” at ${m.dist_m ?? '?'} m` : ''}`]);
  }
  if (src.includes('atp')) {
    const m = p.atp_match || {};
    rows.push(['AllThePlaces', `${esc(p.atp_spider || '?')} · method ${esc(p.atp_method || m.method || '?')}${p.atp_brand ? ` · brand ${esc(p.atp_brand)}` : ''}${p.atp_wikidata ? ` · <a target="_blank" href="https://www.wikidata.org/wiki/${esc(p.atp_wikidata)}">${esc(p.atp_wikidata)}</a>` : ''}`
      + (m.score != null ? ` · score ${m.score}, ${m.candidates ?? '?'} candidates` : '')]);
  }
  if (src.includes('nhs')) {
    const m = p.nhs_match || {};
    rows.push(['NHS', `ODS ${esc(p.nhs_ods || '?')}${m.dist_m != null ? ` · ${m.dist_m} m` : ''}`]);
  }
  if (src.includes('servicemap')) {
    const m = p.sm_match || {};
    rows.push(['ServiceMap', `unit ${esc(String(p.sm_id ?? '?'))}${m.dist_m != null ? ` · ${m.dist_m} m` : ''}`]);
  }
  if (src.includes('site')) rows.push(['Website', `${esc(p.site_method || 'parsed')}${p.site_url ? ` · <a target="_blank" href="${esc(p.site_url)}">source page</a>` : ''}${p.site_raw ? ` · ${p.site_raw.length} snippets` : ''}${p.site_alt ? ' · disputed on-site' : ''}`]);
  if (p.wikipedia || p.wikidata) rows.push(['Wikipedia', `mapper-asserted${p.wikipedia ? ` ${esc(p.wikipedia)}` : ''}${p.wikidata ? ` · <a target="_blank" href="https://www.wikidata.org/wiki/${esc(p.wikidata)}">${esc(p.wikidata)}</a>` : ''}`]);
  if (!rows.length) return '';
  return `<details style="margin-top:8px"><summary style="font-size:12.5px;cursor:pointer;color:#555">Source debug</summary>`
    + `<div style="font-size:12px;color:#444;line-height:1.7;margin-top:4px;font-family:monospace">`
    + rows.map(([s, d]) => `· <b>${s}</b> — ${d}<br/>`).join('') + `</div></details>`;
}

function ratingHtml(p) {
  // Recommend display: OBSERVED positive share ((E+V)/n) — directly
  // explainable against the vote piles, never an adjusted figure.
  // Ranking and the badge use the five-level Bayesian score instead
  // (ta_rec_bayes: empirical extract-global prior, C=10, stated in
  // meta.ta). Adjusted scores are never presented as observed
  // percentages. Averages stay in the denominator of both. No
  // smoothing: under 5 votes the actual share shows with a plain
  // caveat. Decay lives HERE, not in the data: legacy counts enter
  // undecayed (w=1) and decay per level only once fresh 5-step
  // reviews arrive (firstFresh arms the clock; auto-blend deferred
  // until tested — historical and fresh render separately). Vintage
  // lives in meta.ta (extract level); the card shows no year.
  // Thresholds stay adjustable; OSM consumers strip ta_*.
  const REC_HALF_LIFE_YEARS = 2; // floor: configurable upward only (spec guardrail)
  const recShare = q => {
    const st = q.ta_rec_stars || [0, 0, 0, 0, 0];
    const n = st[0] + st[1] + st[2] + st[3] + st[4];
    if (!(n > 0)) return null;
    return (st[0] + st[1]) / n;
  };
  const recReviews = n => {
    if (n == null) return '';
    if (n < 5) return String(n);
    if (n < 20) return '~' + (Math.round(n / 5) * 5);
    if (n < 100) return '~' + (Math.round(n / 10) * 10);
    return '~' + (Math.round(n / 50) * 50);
  };
  const diet = [
    p.ta_vegetarian ? '<span class="badge diet-veg">Vegetarian</span>' : '',
    p.ta_vegan ? '<span class="badge diet-vegan">🌱 Vegan</span>' : '',
    p.ta_gluten_free ? '<span class="badge diet-gf">Gluten-Free</span>' : '',
  ].filter(Boolean).join(' ');
  const rn = p.ta_rec_n;
  const share = rn != null ? recShare(p) : null;
  const bayes = p.ta_rec_bayes;
  const rate = share == null
    ? ''
    : `<span style="font-size:14px">${Math.round(share * 100)}% positive <span style="color:#888;font-size:11px">(${recReviews(rn)} reviews${rn < 5 ? ' · fewer than 5 reviews' : ''})</span>${bayes != null && bayes >= 4.0 && rn >= 5 ? ' <span class="badge src-atp">👍 Recommended</span>' : ''}</span>`;
  if (!rate && !diet) return '';
  return `<div style="margin-top:10px;font-size:13px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">${rate}${diet}</div>`;
}

function hoursHtml(p) {
  const variants = hourVariants(p);
  if (!variants.length) return `<p style="font-size:13px;color:#666">Opening hours aren't on record for this place yet. If you know them, <b>suggest a correction</b> below.</p>`;
  const [primaryWho, raw] = variants[0];
  const srcNote = primaryWho === 'Mapped on OpenStreetMap' ? 'As mapped on OpenStreetMap — may be out of date.'
    : primaryWho.startsWith('Published by') ? `As published by ${esc(p.atp_brand || 'the chain')} (via AllThePlaces) — may be out of date.`
    : primaryWho === 'NHS listed hours' ? 'As listed by NHS England — may be out of date.'
    : primaryWho.startsWith('Municipal listing') ? 'As listed by the City of Helsinki Service Map — may be out of date.'
    : primaryWho.startsWith('Archived research') ? 'As listed c.2021 in archived research data — likely out of date.'
    : primaryWho.startsWith('Also stated') ? 'As also stated on the business website — may be out of date.'
    : `As stated on the business website — may be out of date.`;
  // table-compare so formatting-only differences don't flag as conflicts
  const conflicts = variants.slice(1).filter(([, h]) => tblKey(h) !== tblKey(raw) && Object.keys(parseOsmHours(h)).length);
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const full = { Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday', Sun: 'Sunday' };
  const jsToday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date().getDay()];
  const table = parseOsmHours(raw);
  if (!Object.keys(table).length) return `<p style="font-size:13px;font-family:monospace;background:#f6f6f6;padding:8px;border-radius:8px">${esc(raw)}</p>`;
  return `<table class="hours-tbl">${days.map(d => {
    const v = table[d.slice(0, 2)]; // parse keys are 2-letter (Mo); rows are 3-letter (Mon)
    const txt = !v ? '—' : v.length === 0 ? 'Closed' : v.map(([o, c]) => `${String(Math.floor(o / 60)).padStart(2, '0')}:${String(o % 60).padStart(2, '0')}–${String(Math.floor(c / 60) % 24).padStart(2, '0')}:${String(c % 60).padStart(2, '0')}`).join(', ');
    const isToday = d === jsToday;
    return `<tr class="${isToday ? 'today' : ''}"><td>${full[d]}${isToday ? ' · today' : ''}</td><td style="text-align:right">${esc(txt)}</td></tr>`;
  }).join('')}</table>${conflicts.map(([who, h]) => `<p style="font-size:12.5px;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:8px 10px">⚠️ ${esc(who)} says: <span style="font-family:monospace">${esc(h)}</span></p>`).join('')}${!conflicts.length ? `<p style="font-size:11.5px;color:#888">${srcNote}</p>` : ''}`;
}
const prettyUrl = u => u.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');

function wireDetail(p) {
  $('#rev-form').addEventListener('submit', async e => {
    e.preventDefault();
    const body = { author: $('#rev-name').value.trim(), rating: +$('#rev-rating').value, text: $('#rev-text').value.trim() };
    if (!body.author || !body.text) return toast('Name + note needed');
    const r = await fetch('/api/reviews/' + encodeURIComponent(p.id), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (r.ok) { toast('Note posted ✓'); loadReviews(p); }
    else toast('Could not post note');
  });
  $('#suggest-form').addEventListener('submit', async e => {
    e.preventDefault();
    const body = { poiId: p.id, poiName: p.name, field: $('#sug-field').value, value: $('#sug-value').value.trim() };
    if (!body.value) return;
    const r = await fetch('/api/suggest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (r.ok) { toast('Thanks — suggestion recorded ✓'); $('#sug-value').value = ''; }
    else toast('Could not save suggestion');
  });
}

function renderHero(p) {
  const hero = $('#hero');
  if (!hero) return;
  if (!heroPhotos.length) return; // keep honest placeholder
  const credit = ph => ph.credit || `📷 ${esc((ph.title || '').slice(0, 50))}`;
  hero.innerHTML = `
    <img id="hero-img" src="${heroPhotos[0].thumb}" alt="${esc(heroPhotos[0].title || p.name)}" onerror="this.closest('#hero').innerHTML='<div class=&quot;hero-empty&quot;><span>${pinIcon(p)}</span><p>Photo unavailable.</p></div>'" />
    ${heroPhotos.length > 1 ? `<button class="hero-nav prev">‹</button><button class="hero-nav next">›</button><div class="hero-dots">${heroPhotos.map((_, i) => `<button data-i="${i}" class="${i === 0 ? 'on' : ''}"></button>`).join('')}</div>` : ''}
    <div class="hero-credit">${credit(heroPhotos[0])}</div>`;
  const setHero = i => {
    heroIdx = (i + heroPhotos.length) % heroPhotos.length;
    $('#hero-img').src = heroPhotos[heroIdx].thumb;
    hero.querySelector('.hero-credit').innerHTML = credit(heroPhotos[heroIdx]);
    hero.querySelectorAll('.hero-dots button').forEach((b, j) => b.classList.toggle('on', j === heroIdx));
  };
  hero.querySelector('.hero-nav.prev')?.addEventListener('click', () => setHero(heroIdx - 1));
  hero.querySelector('.hero-nav.next')?.addEventListener('click', () => setHero(heroIdx + 1));
  hero.querySelectorAll('.hero-dots button').forEach(b => b.addEventListener('click', () => setHero(+b.dataset.i)));
}

async function loadPhotos(p) {
  // Hero order: first-party image (validated at build), else chain brand
  // logo (Wikidata P154 via /api/brand-logo), else honest placeholder.
  heroPhotos = p.site_image ? [{ thumb: p.site_image.url, url: p.site_image.url,
    title: p.name, credit: `📷 via the <a target="_blank" href="${esc(p.site_url || p.website || '#')}">business website</a>` }] : [];
  heroIdx = 0;
  if (state.selectedId === p.id) renderHero(p);
  const qid = p.atp_wikidata || p.brand_wikidata;
  if (!heroPhotos.length && qid) {
    try {
      const r = await fetch(`/api/brand-logo?qid=${encodeURIComponent(qid)}`);
      const j = await r.json();
      if (j.thumb) heroPhotos = [{ thumb: j.thumb, url: j.file || j.thumb,
        title: p.atp_brand || p.name,
        credit: `ⓘ brand logo · <a target="_blank" href="${esc(j.file || j.thumb)}">Wikimedia Commons</a>` }];
      if (state.selectedId === p.id) renderHero(p);
    } catch { /* placeholder stays */ }
  }
}

async function loadReviews(p) {
  try {
    const r = await fetch('/api/reviews/' + encodeURIComponent(p.id));
    const revs = await r.json();
    const box = $('#reviews');
    if (!box || state.selectedId !== p.id) return;
    box.innerHTML = revs.length ? `<p style="font-size:12px;color:#888">Visitor notes left on this prototype:</p>` + revs.slice().reverse().map(v => `<div class="rev"><span class="who">${esc(v.author)}</span><span class="when">${esc(v.date || '')} · <span class="stars">${'★'.repeat(v.rating)}${'☆'.repeat(5 - v.rating)}</span></span><p>${esc(v.text)}</p></div>`).join('')
      : `<p style="color:#666;font-size:13px">No visitor notes yet.</p>`;
  } catch { /* keep skeleton */ }
}

function wikiFallback(p) {
  return `<p style="font-size:13px;color:#666;margin:0">${esc(p.category_label || p.category)}${p.address ? ` · ${esc(p.address.split(',').slice(0, 2).join(','))}` : ''}.</p>`;
}

async function loadWiki(p) {
  // OSM-asserted articles only: the record must carry a mapper-assigned
  // wikipedia ('lang:Title') or wikidata ('Q…') tag. Bare names are never
  // resolved (a café called "Tides" must not show the tidal article).
  const box = $('#wiki');
  if (!p.wikipedia && !p.wikidata) { if (box) box.innerHTML = wikiFallback(p); return; }
  try {
    const q = p.wikipedia ? `wikipedia=${encodeURIComponent(p.wikipedia)}` : `wikidata=${encodeURIComponent(p.wikidata)}`;
    const r = await fetch('/api/enrich?' + q);
    const j = await r.json();
    if (!box || state.selectedId !== p.id) return;
    box.innerHTML = j.extract
      ? `<p style="font-size:13.5px;line-height:1.55;margin:0">${esc(j.extract.slice(0, 420))}${j.extract.length > 420 ? '…' : ''} ${j.url ? `<a target="_blank" href="${j.url}">Wikipedia</a>` : ''}</p>`
      : wikiFallback(p);
  } catch { if (box) box.innerHTML = wikiFallback(p); }
}

// ---------- search ----------
let sugT;
$('#search').addEventListener('input', e => {
  const v = e.target.value.trim();
  $('#clear-search').style.display = v ? 'block' : 'none';
  state.q = v.toLowerCase();
  clearTimeout(sugT);
  sugT = setTimeout(() => suggest(v), 250);
  renderAll();
});
$('#clear-search').addEventListener('click', () => { $('#search').value = ''; state.q = ''; $('#suggest').style.display = 'none'; $('#clear-search').style.display = 'none'; renderAll(); });

async function suggest(v) {
  const box = $('#suggest');
  if (!v) { box.style.display = 'none'; return; }
  const needle = v.toLowerCase();
  const hits = state.pois.filter(p => (p.name + ' ' + (p.address || '')).toLowerCase().includes(needle)).slice(0, 5);
  let places = [];
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(v) + (packQ() ? '&' + packQ() : ''));
    const j = await r.json();
    places = j.places || [];
  } catch {}
  box.innerHTML = hits.map(p => `<div class="sug" data-poi="${p.id}"><span>${pinIcon(p)}</span><span><div class="t">${esc(p.name)}</div><div class="s">${esc(p.address || p.category_label || '')}</div></span></div>`).join('')
    + places.slice(0, 3).map(pl => `<div class="sug" data-lat="${pl.lat}" data-lng="${pl.lng}"><span>📌</span><span><div class="t">${esc(pl.display_name.split(',').slice(0, 2).join(','))}</div><div class="s">${esc(pl.display_name.slice(0, 90))}</div></span></div>`).join('')
    || `<div class="sug"><span>🔍</span><span><div class="t">No matches</div></span></div>`;
  box.style.display = 'block';
  box.querySelectorAll('.sug').forEach(s => s.addEventListener('click', () => {
    box.style.display = 'none';
    if (s.dataset.poi) selectPoi(s.dataset.poi, { pan: true });
    else if (s.dataset.lat) map.flyTo([+s.dataset.lat, +s.dataset.lng], 15, { duration: 0.8 });
  }));
}
document.addEventListener('click', e => { if (!e.target.closest('#search-wrap')) $('#suggest').style.display = 'none'; });
// Hosted-concession links inside the detail panel (host card ↔ concession).
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-poi]');
  if (!a || !a.closest('#detail')) return;
  e.preventDefault();
  selectPoi(a.dataset.poi, { pan: true });
});

// ---------- filters ----------
document.querySelectorAll('#chips .chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('#chips .chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active'); state.cat = c.dataset.cat; renderAll();
}));
$('#mode-curated').addEventListener('click', () => { state.mode = 'curated'; $('#mode-curated').classList.add('active'); $('#mode-all').classList.remove('active'); loadPois(); });
$('#mode-all').addEventListener('click', () => { state.mode = 'all'; $('#mode-all').classList.add('active'); $('#mode-curated').classList.remove('active'); toast('Live OSM added — unlisted extras may lack addresses/hours'); loadPois(); });
$('#open-now-only').addEventListener('change', e => { state.openOnly = e.target.checked; renderAll(); });
$('#audit-fsa-only').addEventListener('change', e => { state.auditFsaOnly = e.target.checked; loadPois(); });
$('#show-hidden').addEventListener('change', e => { state.showHidden = e.target.checked; loadPois(); });
$('#recenter').addEventListener('click', () => map.flyTo(SOUTHEND, 13, { duration: 0.8 }));
$('#detail-close').addEventListener('click', () => { $('#detail').classList.add('hidden'); state.selectedId = null; clearHitMarker(); history.replaceState(null, '', location.pathname); renderAll(); });
$('#cfg-link').addEventListener('click', async () => {
  const [c, m] = await Promise.all([fetch('/api/config').then(r => r.json()), fetch('/api/meta').then(r => r.json()).catch(() => ({}))]);
  toast(`FSA extract ${m.fsa_extract_date || '?'} · ${m.counts?.total || '?'} listings · ` + Object.entries(c.providers).map(([k, v]) => `${k}: ${v.status}`).join(' · ').slice(0, 90));
});

function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600); }

// ---------- pack diff (old vs new pins) ----------
// Client-side A/B: joins two packs on stable ids (fsa-{FHRSID},
// osm-node-{id}, ch-{number} — identical across vintages). Moved = same
// id, coords differ beyond DIFF_MOVE_M; added/removed = single-sided ids.
// Same-area pairs only (bbox-overlap guard). App-only: no API, pipeline
// or CI changes; exiting restores normal rendering byte-for-byte.
const DIFF_OLD = 'eu/gb/england/essex/southend-old-pipeline';
const DIFF_MOVE_M = 25;
const havM = (a, b, c, d) => {
  const r = 6371000, p = Math.PI / 180;
  const h = Math.sin((c - a) * p / 2) ** 2 + Math.cos(a * p) * Math.cos(c * p) * Math.sin((d - b) * p / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h));
};
function bboxOf(rows) {
  let s = 90, w = 180, n = -90, e = -180;
  for (const p of rows) {
    if (p.lat == null || p.lng == null) continue;
    if (p.lat < s) s = p.lat; if (p.lat > n) n = p.lat;
    if (p.lng < w) w = p.lng; if (p.lng > e) e = p.lng;
  }
  return { s, w, n, e };
}
function bboxOverlap(a, b) {
  const ix = Math.max(0, Math.min(a.e, b.e) - Math.max(a.w, b.w));
  const iy = Math.max(0, Math.min(a.n, b.n) - Math.max(a.s, b.s));
  const ua = (a.e - a.w) * (a.n - a.s), ub = (b.e - b.w) * (b.n - b.s);
  return ua > 0 && ub > 0 ? (ix * iy) / (ua + ub - ix * iy) : 0;
}
async function enterDiffMode() {
  statsText.textContent = 'Loading diff…';
  try {
    const [oldR, newR] = await Promise.all([
      fetch('/api/pois?pack=' + encodeURIComponent(DIFF_OLD) + '&include_hazard=1').then(r => r.json()),
      fetch('/api/pois?include_hazard=1').then(r => r.json()),
    ]);
    const oldRows = oldR.pois || [], newRows = newR.pois || [];
    if (bboxOverlap(bboxOf(oldRows), bboxOf(newRows)) < 0.5) {
      toast('Diff refused: pack areas do not overlap');
      $('#pack-sel').value = state.pack;
      return;
    }
    const byId = new Map(newRows.map(p => [p.id, p]));
    const oldById = new Map(oldRows.map(p => [p.id, p]));
    const moved = [], added = [], removed = [];
    for (const p of newRows) {
      const o = oldById.get(p.id);
      if (!o) { added.push(p); continue; }
      if (o.lat == null || p.lat == null) continue;
      const d = Math.round(havM(o.lat, o.lng, p.lat, p.lng));
      if (d > DIFF_MOVE_M) moved.push({ cur: p, old: o, d });
    }
    for (const o of oldRows) if (!byId.has(o.id)) removed.push(o);
    moved.sort((a, b) => b.d - a.d);
    state.pois = newRows;
    state.diff = { moved, added, removed, nOld: oldRows.length, nNew: newRows.length };
    state.selectedId = null;
    $('#detail').classList.add('hidden');
    renderAll();
  } catch { toast('Diff failed to load'); $('#pack-sel').value = state.pack; }
}
function exitDiffMode() {
  state.diff = null;
  diffLayers.clearLayers();
}
function renderDiff() {
  diffLayers.clearLayers();
  const { moved, added, removed, nOld, nNew } = state.diff;
  for (const m of moved) {
    L.polyline([[m.old.lat, m.old.lng], [m.cur.lat, m.cur.lng]], { color: '#d93025', weight: 2, dashArray: '5 4' }).addTo(diffLayers);
    const el = document.createElement('div');
    el.className = `pin cat-${m.cur.category}`;
    el.innerHTML = `<span>${pinIcon(m.cur)}</span>`;
    const mk = L.marker([m.cur.lat, m.cur.lng], { icon: L.divIcon({ className: '', html: el.outerHTML, iconSize: [30, 30], iconAnchor: [15, 28] }), title: `${m.cur.name} (moved ${m.d}m)` });
    mk.bindPopup(`<b>${esc(m.cur.name)}</b><br>moved ${m.d}m<br>was: ${m.old.lat.toFixed(5)}, ${m.old.lng.toFixed(5)}<br>now: ${m.cur.lat.toFixed(5)}, ${m.cur.lng.toFixed(5)}<br>${esc(m.cur.verified_from ? 'via ' + m.cur.verified_from : (m.cur.verified_position ? m.cur.verified_position : 'unattributed move'))}`);
    mk.on('click', () => selectPoi(m.cur.id, { pan: false }));
    diffLayers.addLayer(mk);
  }
  for (const p of added) {
    const mk = L.marker([p.lat, p.lng], { title: `${p.name} (added)` });
    mk.on('click', () => selectPoi(p.id, { pan: false }));
    diffLayers.addLayer(mk);
  }
  for (const o of removed) {
    if (o.lat == null) continue;
    const mk = L.circleMarker([o.lat, o.lng], { radius: 6, color: '#9e9e9e', fillOpacity: 0.4, title: `${o.name} (removed)` });
    mk.bindPopup(`<b>${esc(o.name)}</b><br>removed in demo<br>was: ${o.lat.toFixed(5)}, ${o.lng.toFixed(5)}`);
    diffLayers.addLayer(mk);
  }
  statsText.textContent = `Diff old → demo: ${moved.length} moved · ${added.length} added · ${removed.length} removed (of ${nOld} → ${nNew})`;
  resultsEl.innerHTML = moved.slice(0, 200).map(m =>
    `<div class="card" data-id="${m.cur.id}">
      <div class="tile" style="background:#fdecea">↔</div>
      <div><h3>${esc(m.cur.name)}</h3>
        <div class="meta">moved ${m.d}m</div>
        <div class="meta">${esc((m.cur.address || '').split(',').slice(0, 2).join(','))}</div>
      </div></div>`).join('')
    || `<p style="padding:16px;color:#666">No moved pins.</p>`;
  resultsEl.querySelectorAll('.card').forEach(c => c.addEventListener('click', () => selectPoi(c.dataset.id, { pan: true })));
}

// ---------- pack selector (debugging across built packs) ----------
async function initPacks() {
  try {
    const r = await fetch('/api/packs');
    state.packs = (await r.json()).packs || [];
  } catch { state.packs = []; }
  const sel = $('#pack-sel');
  // A/B labels: built date + distinguishing stages so competing vintages
  // tell themselves apart (all fields already in the payload — display
  // only). New-pipeline markers shown when present, absent when not.
  const mark = p => ['verify_positions', 'hazard_roads'].filter(s => (p.stages_ok || []).includes(s))
    .map(s => s === 'verify_positions' ? '+verify' : '+hazard').join(' ');
  const when = p => { try { return fmtDate((p.built_at || '').slice(0, 10)); } catch { return '?'; } };
  sel.innerHTML = state.packs.map(p =>
    `<option value="${esc(p.id)}">${esc(p.name)}${p.total != null ? ` (${p.total})` : ''} · ${when(p)}${mark(p) ? ' ' + mark(p) : ''}</option>`).join('')
    + `<option value="diff:old-demo">Diff: old → demo (moved pins)</option>`
    || `<option value="">Southend-on-Sea</option>`;
  sel.addEventListener('change', () => {
    if (sel.value === 'diff:old-demo') { enterDiffMode(); return; }
    const wasDiff = !!state.diff;
    state.pack = sel.value;
    state.selectedId = null;
    $('#detail').classList.add('hidden');
    if (wasDiff) { exitDiffMode(); state.pack = sel.value; }
    const b = (state.packs.find(p => p.id === state.pack) || {}).bbox;
    if (b && [b.s, b.w, b.n, b.e].every(Number.isFinite)) map.flyToBounds([[b.s, b.w], [b.n, b.e]], { duration: 0.7 });
    loadPois();
  });
  loadPois();
}

initPacks();
