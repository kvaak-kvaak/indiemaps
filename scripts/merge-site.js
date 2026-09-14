/* Merge first-party site-hours (scripts/site-hours/) into built listings.
 *
 * Usage: node scripts/merge-site.js --in data/site/southend.json
 *
 * Matching is website-exact (normalized deep URLs — the spider ran on the
 * POIs' own URLs, so equality is near-certain) with a name-sanity check.
 * Stores site_hours (JSON-LD preferred, else joined regex fragments),
 * site_method, site_url; appends 'site' to sources. OSM/ATP hours keep
 * display priority in the frontend; conflicts render side-by-side.
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
  if (!m) continue;
  args[m[1]] = m[3] ?? (rawArgs[i + 1] && !rawArgs[i + 1].startsWith('--') ? rawArgs[++i] : true);
}
if (!args.in) { console.error('usage: node scripts/merge-site.js --in data/site/<area>.json'); process.exit(1); }

function normUrl(u) {
  let s = String(u || '').trim().toLowerCase().replace(/^(https?:)?\/\//, '').replace(/^www\./, '').replace(/\/$/, '');
  if (!s || /\s/.test(s)) return null;
  const [host, ...rest] = s.split('/');
  const p = rest.join('/').split(/[?#]/)[0];
  return host + (p ? '/' + p : '');
}
const normName = s => (s || '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
const toks = s => new Set(normName(s).split(' ').filter(w => w.length >= 3 && !['and', 'the', 'of', 's'].includes(w)));

const site = JSON.parse(fs.readFileSync(args.in, 'utf8'));
const POIS_PATH = args.pois || path.join(DATA, 'pois.json');
const META_PATH = args.meta || path.join(DATA, 'build-meta.json');
const pois = JSON.parse(fs.readFileSync(POIS_PATH, 'utf8'));
// clear previous merge first (stale entries must not survive a re-run)
for (const p of pois) {
  delete p.site_hours; delete p.site_method; delete p.site_url; delete p.site_raw; delete p.site_alt;
  delete p.site_image; delete p.site_menu; delete p.site_description; delete p.site_cuisine; delete p.site_price;
  p.sources = (p.sources || []).filter(s => s !== 'site');
}
const byUrl = new Map();
for (const p of pois) {
  const u = normUrl(p.website);
  if (u) byUrl.set(u, p);
}
// Distinct-POI counts: site rows are per-POI input, but several POIs can
// share one website (same premises, rebrand, directory page), and byUrl
// collapses them onto a single POI. Counting rows inflates matched /
// hours_added (measured on Southend: 377 rows -> 370 POIs, 104 -> 102).
const matchedIds = new Set(), hoursAddedIds = new Set(), conflictIds = new Set();
for (const r of site) {
  const p = byUrl.get(normUrl(r.site));
  if (!p) continue;
  // name sanity: site must share vocabulary with the POI (guards URL reuse).
  // spaceless variants count ('Red Chilliezs' vs FSA 'RedChilliezs').
  const cn = s => normName(s).replace(/ /g, '');
  const shared = [...toks(r.name)].some(w => toks(p.name).has(w)) ||
    (Math.min(cn(r.name).length, cn(p.name).length) >= 8 &&
     (cn(r.name).includes(cn(p.name)) || cn(p.name).includes(cn(r.name))));
  if (!shared) { console.log(`skip (name mismatch): ${r.name} vs ${p.name}`); continue; }
  // upstream-URL guard, positive-contradiction standard: skip only when the
  // site positively identifies as a DIFFERENT business (page title/JSON-LD
  // names share nothing with the POI) AND body text doesn't name it either.
  // Absence alone (JS shells, logo-only brands) is not contradiction.
  if (r.identity && !r.identity_match && r.name_on_page === false) { console.log(`skip (wrong business): ${r.name} @ ${r.site} (page: ${r.identity.slice(0, 60)})`); continue; }
  if (matchedIds.has(p.id)) console.log(`note (shared website): ${r.name} merges into already-counted ${p.name}`);
  matchedIds.add(p.id);
  const jl = (r.jsonld || []).map(([lbl, h]) => h).filter(Boolean);
  const rx = (r.regex || []).map(h => h.osm);
  // spider verdict preferred (cross-checked JSON-LD vs visible + specificity);
  // fall back to raw fields for older spider outputs
  const primary = r.hours_primary || (jl.length ? { osm: [...new Set(jl)].join('; '), method: 'json-ld' }
    : rx.length ? { osm: [...new Set(rx)].join('; '), method: 'regex' } : null);
  const alt = r.hours_alt || null;
  const hours = primary?.osm || '';
  if (!hours) continue;
  p.site_hours = hours;
  p.site_method = primary.method + (r.internal_conflict ? ' (disputed on-site)' : '');
  p.site_url = r.site;
  if (alt && alt.osm) p.site_alt = alt.osm;
  p.site_raw = [...(r.jsonld || []).map(([lbl, h]) => `${lbl}: ${h}`.slice(0, 120)), ...(r.regex || []).map(h => h.raw)].slice(0, 8);
  // first-party enrichment (all optional, all attributed)
  const ex = r.extras || {};
  const imgs = (ex.images || []).filter(i => i.url);
  const pick = imgs.find(i => i.ok === true) || imgs.find(i => i.ok !== false);
  if (pick) p.site_image = { url: pick.url, kind: pick.kind };
  if ((ex.menu || []).length) p.site_menu = ex.menu[0];
  if (ex.description) p.site_description = ex.description;
  if ((ex.cuisine || []).length) p.site_cuisine = ex.cuisine.slice(0, 4);
  if (ex.price_range) p.site_price = ex.price_range;
  if (!p.sources.includes('site')) p.sources.push('site');
  if (!p.opening_hours_osm && !p.atp_hours) hoursAddedIds.add(p.id);
  const eff = p.opening_hours_osm || p.atp_hours || '';
  if (eff && eff.replace(/\s/g, '') !== hours.replace(/\s/g, '')) conflictIds.add(p.id);
}
fs.writeFileSync(POIS_PATH, JSON.stringify(pois, null, 2));
const meta = JSON.parse(fs.readFileSync(META_PATH, 'utf8'));
const matched = matchedIds.size, hoursAdded = hoursAddedIds.size, conflicts = conflictIds.size;
meta.site = { input: args.in, sites: site.length, matched, hours_added: hoursAdded, conflicts };
fs.writeFileSync(META_PATH, JSON.stringify(meta, null, 2));
console.log(`site-hours: ${site.length} sites → ${matched} matched, +${hoursAdded} new hours, ${conflicts} conflicts with existing`);
