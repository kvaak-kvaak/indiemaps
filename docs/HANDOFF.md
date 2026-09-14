# HANDOFF — IndieMaps POI pipeline

You are looking at a keyless-first POI enrichment pipeline + demo map app.
Read these three docs first, in order:

1. `docs/PLANNING_BRIEF.md` — why this exists, source matrix, measured results
2. `docs/PHASE_PLAN.md` — phases, gates, category weighting
3. `docs/packs.md` — area tiling + GitHub release distribution design

## Layout

- `server.js` + `public/` — demo web map (Southend). `npm install && npm start`.
- `scripts/build.js` — base pack builder (FSA + OSM + postcodes.io)
- `scripts/pack/build.py` — pack orchestrator (`--area ID | --all | --manifest`,
  stages: base → overture → nhs → atp → sites → merge)
- `scripts/pack/overture.py` — Overture contact backfill (needs `pip install duckdb`)
- `scripts/pack/nhs.py` — NHS pharmacy layer (`--update-cache` quarterly)
- `scripts/atp.js` — AllThePlaces chain-hours merge (61 UK spiders)
- `scripts/site-hours/site-hours.py` — first-party site spider ("indiespider")
- `scripts/merge-site.js` — site-hours merge
- `scripts/overture-join/join.py` — analysis tool (Overture↔OSM↔ATP experiments)
- `scripts/pack/areas.json` — 47 Phase-1 areas (Essex + Greater London)
- `.github/workflows/build-packs.yml` — quarterly matrix build + release
- `data/` — live demo data + committed caches (`nhs/cache.json`,
  `dead-domains/`); `packs/` is gitignored build output

## Current state (2026-09-13)

- Phase 1a GREEN: 47/47 packs complete via Actions (base+overture+nhs+atp).
  Southend reference: 1,437 POIs / 231 with hours in release `pois-2026-Q3`
  (local demo `data/pois.json` shows 237 — different build vintage).
- Open threads: Phase 1b spider dispatch; Westminster rebuilt post-bbox-fix;
  `record_id`→`fsq_place_id` hypothesis unverified (needs Portal lookup).

## Working conventions (non-negotiable, learned the hard way)

- Real data only. Gaps render as "unknown"; conflicts render side-by-side
  with provenance. Never invent, never silently merge.
- Fix the rule, not the row. Every matcher change needs a measured audit
  (see git log: concession saga, possessive-token collision, argparse bug).
- Matching is brand-anchored (own name/brand must share specific vocabulary;
  branch text and town names never qualify; website-URL equality is a
  first-class key; Wikidata QID exact pass before fuzzy).
- Verify empirically before claiming (run it, diff outputs, spot-check).
  Overpass 504s are weather — retries + tiling exist for a reason.
- No standing infrastructure, no secrets, no keyed APIs without explicit go.
