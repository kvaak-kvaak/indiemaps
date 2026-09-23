/* Essex Yellow-Pages Map — honest frontend.
 * Every field shown comes from a real source (FSA / OSM / chain feeds /
 * business websites / visitor contributions). Anything unverified renders
 * as "unknown" — never invented. */
const SOUTHEND = [51.5414, 0.7120];
const state = { pois: [], meta: {}, curatedCount: 0, liveCount: 0, cat: 'all', mode: 'curated', openOnly: false, q: '', selectedId: null, markers: new Map(), pack: '', packs: [], auditFsaOnly: false };
const packQ = () => state.pack ? `pack=${encodeURIComponent(state.pack)}` : '';
const packName = () => (state.packs.find(p => p.id === state.pack) || {}).name || 'Listings';

const map = L.map('map', { zoomControl: false }).setView(SOUTHEND, 13);
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>', subdomains: 'abcd', maxZoom: 20
}).addTo(map);
const clusters = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 46 });
map.addLayer(clusters);

const $ = s => document.querySelector(s);
const resultsEl = $('#results'), statsText = $('#stats-text');

const CAT_ICON = { restaurant: '🍽️', cafe: '☕', pub: '🍺', shopping: '🛍️', hotel: '🛏️', attraction: '🎡', culture: '🎭', health: '⚕️', services: '✂️' };
const CAT_TINT = { restaurant: '#fdecea', cafe: '#fef6e0', pub: '#f3e8dc', shopping: '#e8f0fe', hotel: '#ede7f6', attraction: '#e6f4ea', culture: '#feefe3', health: '#e0f7fa', services: '#eceff1' };

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
  const extra = (p.position_approx ? '<span class="badge stacked" title="Area-placed: position comes from an uncorroborated batch geocode — placed by postcode area, not surveyed">area-placed</span>' : '')
    + (p.position_hazard ? '<span class="badge stacked" title="Needs manual placement: on a road class where geocoders fail and no surveyed position exists">needs-manual-placement</span>' : '');
  return extra + (p.sources || [p.source]).map(s =>
    s === 'fsa' ? '<span class="badge src-fsa" title="Food Standards Agency open data">FSA</span>'
    : s === 'atp' ? '<span class="badge src-atp" title="Chain-published data via AllThePlaces">chains</span>'
    : s === 'site' ? '<span class="badge src-site" title="Hours stated on the business website">website</span>'
    : s === 'nhs' ? '<span class="badge src-nhs" title="NHS listed pharmacy (England)">NHS</span>'
    : s === 'overture' ? '<span class="badge src-overture" title="Phone/website backfilled from Overture Maps">Overture</span>'
    : s === 'servicemap' ? '<span class="badge src-sm" title="City of Helsinki Service Map (CC BY 4.0)">HKI map</span>'
    : s === 'ta' ? '<span class="badge src-ta" title="Archived research data, c.2021 (stale by design)">archive</span>'
    : s === 'ch' ? '<span class="badge src-ch" title="Companies House: incorporated business, uninspected">companies</span>'
    : s === 'osm' ? '<span class="badge src-osm" title="OpenStreetMap contributors">OSM</span>'
    // Unknown sources render under their own name — never borrowed. (A
    // fallthrough OSM label once masqueraded Overture contributions.)
    : `<span class="badge src-osm" title="Unrecognised source key">${esc(s)}</span>`).join('');
}
// Effective hours: OSM mapping first, then chain, site, NHS, municipal,
// stale archive last — every differing source is shown, never merged.
const effHours = p => p.opening_hours_osm || p.atp_hours || p.site_hours || p.nhs_hours || p.sm_hours || p.ta_hours || '';
const tblKey = s => JSON.stringify(parseOsmHours(s || ''));
function hourVariants(p) {
  const v = [];
  if (p.opening_hours_osm) v.push(['Mapped on OpenStreetMap', p.opening_hours_osm]);
  if (p.atp_hours) v.push([`Published by ${p.atp_brand || 'the chain'}`, p.atp_hours]);
  if (p.site_hours) v.push(['On the business website', p.site_hours]);
  if (p.nhs_hours) v.push(['NHS listed hours', p.nhs_hours]);
  if (p.sm_hours) v.push(['Municipal listing (Helsinki Service Map)', p.sm_hours]);
  if (p.ta_hours) v.push(['Archived research data (c.2021)', p.ta_hours]);
  if (p.site_alt) v.push(['Also stated on the business website', p.site_alt]);
  return v;
}

// ---------- data loading ----------
function bboxStr() { const b = map.getBounds(); return `${b.getSouth().toFixed(4)},${b.getWest().toFixed(4)},${b.getNorth().toFixed(4)},${b.getEast().toFixed(4)}`; }

async function loadPois() {
  statsText.textContent = 'Loading…';
  try {
    // Audit mode needs the hazard-hidden kiosks too — same endpoints,
    // just with the include flag (server change already supports it).
    const hz = state.auditFsaOnly ? 'include_hazard=1' : '';
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
    if (state.cat !== 'all' && p.category !== state.cat) return false;
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
  const list = filtered();
  for (const p of list) {
    const el = document.createElement('div');
    // Confidence-gated display: batch-placed pins (position_approx) render
    // with an uncertainty halo instead of full-authority markers. Same
    // class will cover the hazard tier (step 3) wherever reachable.
    el.className = `pin cat-${p.category}${p.sources?.length === 1 && p.sources[0] === 'osm' ? ' osm' : ''}${p.position_approx ? ' approx' : ''}${p.id === state.selectedId ? ' selected' : ''}`;
    el.innerHTML = `<span>${CAT_ICON[p.category] || '📍'}</span>`;
    const m = L.marker([p.lat, p.lng], { icon: L.divIcon({ className: '', html: el.outerHTML, iconSize: [30, 30], iconAnchor: [15, 28] }), title: p.name });
    m.on('click', () => selectPoi(p.id, { pan: false }));
    clusters.addLayer(m); state.markers.set(p.id, m);
  }
  const fsaDate = state.meta.fsa_extract_date ? ` · FSA extract ${state.meta.fsa_extract_date}` : '';
  const built = state.meta.built_at ? ` · verified ${fmtDate(state.meta.built_at.slice(0, 10))}` : '';
  statsText.textContent = state.mode === 'curated'
    ? `${state.auditFsaOnly ? 'AUDIT — register-positioned pins only (postcode-grade, not surveyed) · ' : ''}${list.length} listings · ${packName()}${fsaDate}${built}`
    : `${list.length} shown (${state.curatedCount} listed + ${state.liveCount} live OSM) · ${packName()}`;
  resultsEl.innerHTML = list.slice(0, 200).map(p => {
    const o = openStatus(p);
    return `<div class="card${p.id === state.selectedId ? ' selected' : ''}" data-id="${p.id}">
      <div class="tile" style="background:${CAT_TINT[p.category] || '#eee'}">${CAT_ICON[p.category] || '📍'}</div>
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
async function selectPoi(id, { pan } = {}) {
  state.selectedId = id;
  const p = state.pois.find(x => x.id === id);
  if (!p) return;
  if (pan) map.flyTo([p.lat, p.lng], Math.max(map.getZoom(), 15), { duration: 0.7 });
  history.replaceState(null, '', `?poi=${encodeURIComponent(id)}`);
  renderAll();
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
  <div class="hero" id="hero"><div class="hero-empty"><span>${CAT_ICON[p.category] || '📍'}</span><p>No photo on record.</p></div></div>
  <div class="dpad">
    <h2>${esc(p.name)}</h2>
    <div class="sub">${esc(p.category_label || p.category)}${(p.site_cuisine || [])[0] ? ` · ${esc(p.site_cuisine.join(', '))}` : p.cuisine ? ` · ${esc(p.cuisine)}` : ''}${p.site_price ? ` · ${esc(p.site_price)}` : ''}</div>
    <div class="drow">${sourceBadges(p)}${p.atp_hours ? '<span class="badge src-atp" title="Opening hours as published by the chain (AllThePlaces)">chain hours</span>' : ''}</div>
    ${p.alias ? `<div class="drow" style="font-size:12.5px;color:#555">formerly <b>${esc(p.alias.registered)}</b> (registered name)${p.alias.verified ? ` · verified ${esc(p.alias.verified)}` : ''}</div>` : ''}
    ${p.superseded_by ? `<div class="drow" style="font-size:12.5px;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:6px 9px">⚠️ may have been replaced here by <b>${esc(p.superseded_by.name)}</b> — mapper data not yet updated</div>` : ''}
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
      ${p.phone ? `<div class="kv"><span class="k">📞</span><a href="${tel}">${esc(p.phone)}</a> <span style="color:#888;font-size:11px">(${p.phone_source === 'overture' ? 'via Overture' : p.phone_source === 'servicemap' ? 'via Service Map' : 'as mapped on OSM'})</span></div>` : ''}
      ${p.email ? `<div class="kv"><span class="k">✉️</span><a href="mailto:${esc(p.email)}">${esc(p.email)}</a></div>` : ''}
      ${p.website ? `<div class="kv"><span class="k">🌐</span><a target="_blank" href="${esc(p.website)}">${esc(prettyUrl(p.website))}</a> <span style="color:#888;font-size:11px">(${p.website_source === 'overture' ? 'via Overture' : p.website_source === 'servicemap' ? 'via Service Map' : 'as mapped on OSM'})</span></div>` : ''}
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
      ${(p.sources || []).includes('atp') ? `· <b>Opening hours</b> — as published by ${esc(p.atp_brand || 'the chain')}, via AllThePlaces (CC0)<br/>` : ''}
      ${(p.sources || []).includes('nhs') ? `· <b>Listed pharmacy</b> — NHS England${p.nhs_ods ? ` (ODS ${esc(p.nhs_ods)})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('servicemap') ? `· <b>Municipal listing</b> — City of Helsinki Service Map (CC BY 4.0)${p.sm_id ? ` (unit ${esc(String(p.sm_id))})` : ''}<br/>` : ''}
      ${(p.sources || []).includes('ta') ? `· <b>Cuisines, dietary notes${p.ta_hours ? ', hours' : ''} & rating</b> — archived research data, c.2021 (stale by design)<br/>` : ''}
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
  // Archived research ratings + dietary marks, derived at render from
  // numeric pack data (never stored as emoji — thresholds stay adjustable
  // and OSM-compatible consumers strip ta_* cleanly). Vintage always shown:
  // a medal on stale data must read as stale.
  const diet = [
    p.ta_vegetarian ? '<span class="badge diet-veg">Vegetarian</span>' : '',
    p.ta_vegan ? '<span class="badge diet-vegan">🌱 Vegan</span>' : '',
    p.ta_gluten_free ? '<span class="badge diet-gf">Gluten-Free</span>' : '',
  ].filter(Boolean).join(' ');
  if (p.ta_rating == null && !diet) return '';
  const emoji = p.ta_rating === 5 ? '🥇' : p.ta_rating === 4.5 ? '✨' : p.ta_rating === 4 ? '👍' : '';
  const rate = p.ta_rating != null
    ? `<span style="font-size:14px">${emoji ? emoji + ' ' : ''}★ ${p.ta_rating}${p.ta_reviews ? ` <span style="color:#888;font-size:11px">(${p.ta_reviews} reviews, c.2021)</span>` : ''}</span>`
    : '';
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
    <img id="hero-img" src="${heroPhotos[0].thumb}" alt="${esc(heroPhotos[0].title || p.name)}" onerror="this.closest('#hero').innerHTML='<div class=&quot;hero-empty&quot;><span>${CAT_ICON[p.category] || '📍'}</span><p>Photo unavailable.</p></div>'" />
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
  box.innerHTML = hits.map(p => `<div class="sug" data-poi="${p.id}"><span>${CAT_ICON[p.category] || '📍'}</span><span><div class="t">${esc(p.name)}</div><div class="s">${esc(p.address || p.category_label || '')}</div></span></div>`).join('')
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

// ---------- filters ----------
document.querySelectorAll('#chips .chip').forEach(c => c.addEventListener('click', () => {
  document.querySelectorAll('#chips .chip').forEach(x => x.classList.remove('active'));
  c.classList.add('active'); state.cat = c.dataset.cat; renderAll();
}));
$('#mode-curated').addEventListener('click', () => { state.mode = 'curated'; $('#mode-curated').classList.add('active'); $('#mode-all').classList.remove('active'); loadPois(); });
$('#mode-all').addEventListener('click', () => { state.mode = 'all'; $('#mode-all').classList.add('active'); $('#mode-curated').classList.remove('active'); toast('Live OSM added — unlisted extras may lack addresses/hours'); loadPois(); });
$('#open-now-only').addEventListener('change', e => { state.openOnly = e.target.checked; renderAll(); });
$('#audit-fsa-only').addEventListener('change', e => { state.auditFsaOnly = e.target.checked; loadPois(); });
$('#recenter').addEventListener('click', () => map.flyTo(SOUTHEND, 13, { duration: 0.8 }));
$('#detail-close').addEventListener('click', () => { $('#detail').classList.add('hidden'); state.selectedId = null; history.replaceState(null, '', location.pathname); renderAll(); });
$('#cfg-link').addEventListener('click', async () => {
  const [c, m] = await Promise.all([fetch('/api/config').then(r => r.json()), fetch('/api/meta').then(r => r.json()).catch(() => ({}))]);
  toast(`FSA extract ${m.fsa_extract_date || '?'} · ${m.counts?.total || '?'} listings · ` + Object.entries(c.providers).map(([k, v]) => `${k}: ${v.status}`).join(' · ').slice(0, 90));
});

function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600); }

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
    || `<option value="">Southend-on-Sea</option>`;
  sel.addEventListener('change', () => {
    state.pack = sel.value;
    state.selectedId = null;
    $('#detail').classList.add('hidden');
    const b = (state.packs.find(p => p.id === state.pack) || {}).bbox;
    if (b && [b.s, b.w, b.n, b.e].every(Number.isFinite)) map.flyToBounds([[b.s, b.w], [b.n, b.e]], { duration: 0.7 });
    loadPois();
  });
  loadPois();
}

initPacks();
