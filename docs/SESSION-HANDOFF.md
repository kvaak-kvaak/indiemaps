# IndieMaps — full session handoff (all caveats included)

Carry this file (plus `docs/HANDOFF.md`, `docs/PLANNING_BRIEF.md`,
`docs/PHASE_PLAN.md`, `docs/packs.md`) into any fresh session instead of
re-deriving what follows. Everything below was measured, not assumed.

## 0. How to work with the assistant
- Plan mode = analysis only, zero file/system changes (absolute, overrides
  everything). Build mode = execution. Say `go` to execute a presented plan.
- Conventions (never bend): real data only (gaps render "unknown",
  conflicts render side-by-side, never invent/merge silently); **fix the
  rule, not the row** (every matcher change needs a measured audit);
  matching is brand-anchored (own name/brand must share specific
  vocabulary; branch text and town names never qualify; website-URL
  equality and Wikidata QID are first-class keys); verify empirically
  before claiming (run it, diff outputs, spot-check); no standing
  infrastructure, no secrets, no keyed APIs without explicit approval.
- `gh` authed as kvaak-kvaak. Dispatch pattern:
  `gh workflow run build-packs.yml -f spider=true [-f areas="id1 id2"]`.
  Full 48 ≈ 5 h; the 330-min job cap is real (one run died at ~305 min).
- **IP caution (user-flagged):** bot detectors may flag the home IP after
  heavy crawling. Rules: no bulk crawling from this machine — verification
  runs offline or against APIs (GitHub, OSM, postcodes.io, Servicemap);
  spider/site fetches happen on CI runners only; keep pulls small (single
  artifact files, not full downloads, where possible).
- Shell footguns learned: `pkill -f "[s]erver\.js"` must run in its OWN
  tool call — the pattern suicides any shell whose command line contains
  literal `node server.js`. Start the server in a separate call:
  `setsid nohup node server.js > /tmp/opencode/server.log 2>&1 < /dev/null &`.

## 1. Repo state (`/home/az/Documents/indiemaps`, `main`, pushed clean)
- Live demo server `:3000` (Southend demo data + Hackney/Helsinki/
  Westminster via `?pack=` selector and `/api/packs` catalog).
- Release `pois-2026-Q3`: 48/48 packs, ~107k POIs, but **mixed vintages**
  (only rebuilt areas are current; most predate matcher hardening).
- `data/aliases.json`: verified-alias registry (TA-KO rename, Fireaway
  absorb). Entries need id + registered_name + evidence + verifier;
  application skips loudly on drift.
- `restaurants.parquet` (139 MB, repo root, gitignored): enriches
  Southend + Helsinki **locally only** via `scripts/pack/ta.py`
  (CI has no access and skips green). See the TA RUNBOOK in HANDOFF.md.
- Leave alone: `package-lock.json` (pre-existing modification, not ours),
  `/tmp/opencode` scratch (OS-managed).

## 2. Pipeline as it runs (7 stages + ta)
`base` (FSA + OSM, `--fsa none` outside England) → `servicemap`
(Helsinki only, `servicemap_muni`) → `overture` (contact backfill +
alias application + prefer-Meta +0.15) → `nhs` (England, no-ops
elsewhere) → `atp` (62 spiders) → `sites` (`--sites-all`, resume
checkpoints every 25) → `merge` → `ta` (Southend+Helsinki only, `"ta"`
flag, skips green without parquet). `build.py` recounts + flags stale
occupants last. Manifest union lets partial dispatches publish safely
(proven live); release job publishes on success *or* failure, never on
cancel, with failed areas in the notes.
- Ordering gaps (known, open): NHS-added rows miss Overture contact
  (overture runs before nhs); absorbed records orphan already-claimed
  OSM nodes until next base rebuild.

## 3. Matching rules as they stand (the earned knowledge)
- Base FSA↔OSM: generic-word exclusion (DUP_STOP, self-normalizing,
  extend per geography), plural stemming, food-scoped category veto,
  exact-6 spaceless gate, OSM `fhrs:id` exact pass (carried 68% of
  Southend merges), postcode +0.3, distance terms. 24-case battery green.
- Overture join: brand-anchored + Meta +0.15 + per-record
  `overture_datasets`; Microsoft culled (product call). Measured: Meta
  won 88%/86% of matches unassisted.
- FSA duplicates: same postcode+housenumber+brand auto-link (newest
  ratingDate wins, alias retained); different-number pairs queue for
  humans → verified-alias registry.
- Stale occupants flagged (never deleted); renames display operating
  name + registered alias.
- Spider verdict: bare-small-hours guard, compatibility union, bare-noon
  inference; genuine contradictions still keep both + warning.
- Known-open bugs (do not re-derive): overnight-close *formatting*
  (`18:00–02:00` renders `18:00–14:00`, display layer); Romance/Swedish
  day names absent; FI ATP spiders missing; absorbed-orphan nodes;
  nhs/overture ordering.
- Never do: row overrides (registry with evidence is the only
  exception), name-similarity relaxation (Costa/Nisa hole stays shut),
  auto-merging different-housenumber pairs (392-vs-418 case), deleting
  stale rows.

## 4. Specimens (ground truth, do not re-prove)
TA-KO (alias OK, Meta row + dead domain + live domain); Wendy's (merged
all sources); Nagawa (osm-only, FSA delisted 1426811); Pallavas (FSA
island, shared site w/ Kwik Fit — category-guard evidence); Fireaway
(single 376–378 record, alias [1870321], ATP hours pending spider pass);
Slug→Skylahs (flagged pair); Big News (uncrossed via fhrs:id);
OKKO/Wing (id-resolved); JK/New Look/Bakery (fixed merges); Swagger
node (correctly free); Gogi (absent-correct: unregistered, zero
Overture rows region-wide); Bavarian-style traps: "392 Kingsland Road"
≠ "418…", Kokin/Kotkin typo (split, honest), Moo Moo vs Honest (split).

## 5. Hard-won tool/process lessons
- `out 400` + post-filter cap check silently truncated pulls (Wendy's +
  Nagawa lost) → raw-count guard + out-2000 single pulls.
- `catch (e)` shadowed the east coordinate → NaN tiles killed 40 areas;
  blind re-tile only works with clean bindings.
- Overpass 504 storms are multi-hour weather: per-area isolation +
  remainder dispatches + per-attempt endpoint logging are the response,
  not bigger retries. `out 2000` keeps query fan-out low.
- Merge counts rows not POIs (fixed with Sets); `links()` poison hrefs
  kill runs (skip-and-count + per-POI guard); appending records during
  match iteration collapses chains (snapshot iteration in nhs +
  servicemap).
- Pack JSON is append-only truth for the app: display derivations
  (emoji, chips, effective hours) live in the frontend, never stored.
  OSM-compatible consumers strip `ta_*`.
- FSA data quirks catalogued: mis-geocoded tail (>10 km → postcode
  centroid rule), duplicate FHRSIDs (linking), fossil names (aliases),
  wrong addresses (ATP arbitration pending spiders).
- Overture dataset reality: Meta ~7/8 of matches, FSQ ~1/8,
  single-contributor rows; prefer-Meta, never drop (yet).
- Release process: union + always-publish proven across storm/failure
  cycles; single-area dispatches are safe; full runs take ~5 h and flirt
  with the cap — matrix sharding mandatory before UK-wide.
- Evaluation discipline that paid off repeatedly: predict-then-verify
  with named specimens; offline replay of shipped functions
  (eval-extraction harness — note: `const` doesn't escape `eval` scope,
  export via `globalThis`); git-HEAD differential testing to separate
  regressions from pre-existing behavior; battery-first rule changes
  (the battery caught stem-compare, dead-capital bugs, and a guard
  regression before CI ever ran).

## 6. Queued, explicitly not started
Spider expansion (fireaway_gb verified in pinned run + KFC/Domino's
audit); full rename-rule spec (policy + specimens stand); tenant
flagging + quiet rendering; ATP ingest via world-PMTiles; Overture
confidence column; BrightQuery identification; OSM license boundary
(#5); 1c non-food; Geofabrik-extract base research; pack-schema doc for
app builder; keyed residual; `record_id→fsq_place_id`; Romance days at
country onboarding; quarterly ta re-enrichment (manual runbook in
HANDOFF); Vela borrow queue (all read-only findings banked, no code
reused).

## 7. Next actions available (all planned, none executed)
1. Next full 48-area rebuild (unifies mixed-vintage release; first live
   shakedown of always-publish + ta-skip + hardened matcher at scale).
2. POI + place-card quality system (pack gates, specimen suite from user
   nominations, card-logic tests, persistent scorecard) — feeds app dev,
   which waits on this project's data.
3. User's manual turf debugging (map is current); specimen nominations
   feed the QA suite when built.
