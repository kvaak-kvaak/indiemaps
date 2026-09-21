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
  stages: base → servicemap → overture → nhs → atp → sites → merge →
  ta → fsa_resweep → nhs_late → ch → overture_late; CH runs LAST so its
  FSA-absent gate sees final FSA verdicts; overture_late backfills contact
  for ch/resweep-added records, skipped when they added nothing)
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
- Phase 1b spider GREEN: 47/47 via Actions run 34909795573 (full
  --sites-all); release `pois-2026-Q3` = 48 assets, 87,690 POIs.
  Southend reference (post-fix rebuild 34984689428): 1,723 POIs / 258
  with hours — Overpass raw-count cap guard recovered ~290 POIs the
  Sep-14 pull silently truncated (Wendy's + Nagawa were the canaries).
- Open threads: Westminster rebuilt post-bbox-fix (verify in next full
  run); `record_id`→`fsq_place_id` hypothesis unverified (needs Portal
  lookup); Gogi (2 Queens Rd) correctly absent — premises churned
  Nagawa→Gogi, FSA delisted 1426811, new occupant unregistered.
- Lambeth lesson (full-47 run): one malformed homepage href
  (`http://[foo]` → `ValueError: Invalid IPv6 URL` in `links()`) killed
  the area at 450/1501. Fixed by skip-and-count in `links()` plus a
  per-POI parse guard (error records, resume-retried); single-area
  re-dispatch green (5,140 POIs, poison link skipped ×1). Release union
  proven live: 48/48 packs.
- Overpass storm saga (Sep 15–17): 504s across three runs; new raw-count
  tiling multiplied query fan-out, and a latent `catch (e)` shadowing the
  east coordinate turned the blind re-tile into NaN tiles (40 areas).
  Fixed (renamed binding, out-2000 single pulls, per-attempt endpoint
  logging). Lesson for UK-wide: matrix sharding is mandatory (single job
  measured 305+ min at 48 areas), Geofabrik-extract base as fallback
  research. Local debugging turfs refreshed post-fix (Hackney 5,370).
- Release rule fixed: publish-on-partial (success/failure, never on
  cancel) with failed areas in the notes — every run banks progress,
  staleness stays labeled per pack. FSA mis-geocoded tail handled:
  coords >10 km from postcode centroid fall back to postcodes.io
  (validated: 10/10 controls under, all howlers above); counts plumbing
  carries osm_*/fsa_repinned through the recount, sm_hours counted.
  Westminster fresh pack served locally (13,020 POIs).
- Phantom-badge postmortem: the badge map's OSM fallthrough rendered
  Overture contributions as a second "OSM" chip (Victory Mansion read
  FSA, OSM, OSM). Fixed with an overture branch + unknowns render under
  their own key; contact rows now labeled via-Overture vs OSM-mapped.
  No. 61 London Road confirmed shared site (Pallavas + Kwik Fit) —
  evidence for the rename rule's category-agreement guard.
- Duplicates doctrine live (verified Southend+Hackney rebuilds): FSA
  duplicate linking (same postcode+housenumber+brand, newest ratingDate
  wins, alias retained; different-number pairs queue for humans),
  verified-alias registry with TA-KO #1 (display TA-KO, alias kept) and
  Fireaway absorb #2 (single 376-378 record), stale-occupant flags
  (Slug→Skylahs proven). Known open: town-word base merges (Swagger
  class, phase 2), Fireaway ATP hours (spider pass), absorbed records
  orphan already-claimed OSM nodes until next base rebuild.
- Base brand-anchoring live (phase 2): generic-word exclusion (with
  plural stemming), food-scoped category veto, exact-6 gate, OSM fhrs:id
  exact pass. Verified: 24-case battery green; 1.3+ floor 408/412 kept
  with all 4 deltas upgraded to id-exact (Big News uncrossed, OKKO/Wing
  resolved); JK/New Look/Bakery merges fixed; Swagger node correctly
  freed to its own record. fhrs:id carried 68% of Southend merges;
  veto refused 2,521 cross-category pairs. Locality stopwords are
  curated per geography (Leigh/Lea were dead capitalized entries —
  set now self-normalizes; extend with every new geography).
- Spider hours fixes (TA-KO verdict): bare-small-hours guard kills
  offer-like misfires ('TUESDAY 2-4') before they can outscore clean
  JSON-LD; compatibility-union merges granularity differences (split
  shifts) to one table with no false conflict flag; genuine
  contradictions and bar-vs-kitchen piles keep both + warning. Bare
  noon reads as noon ('12-5' → 12:00-17:00). Known open, separate
  ticket: overnight close formatting ('Fr 18:00-02:00' renders close
  as 14:00 — pre-existing, display-layer).
- Prefer-Meta ranking live (product-call update): +0.15 freshness prior,
  FSQ fallback intact. Measured: Meta won 88%/86% unassisted; bonus
  flips ~1/area, zero lost (verified by per-POI diff). Per-record
  `overture_datasets` stored (future staleness audits free). Known
  ordering gap: NHS-added rows miss Overture contact (overture runs
  before nhs) — 7 pharmacies gained it on a re-run.
- Helsinki pilot GREEN (gate exception): run 35029285720, 3,701 POIs /
  2,068 with hours via new Servicemap municipal stage (1,663 units →
  223 matched + 1,440 added, FI hours normalized). Oiva has no bulk
  path — manual verification reference only. Open FI gaps: FI-specific
  ATP spiders (UK list yielded 28 matches), Swedish day names in spider.
- Demo serves three debugging turfs via pack selector (`/api/packs`):
  Southend demo data, Hackney (Stoke Newington), Helsinki.
- Vela evaluation (Sep 2026, read-only — no code reused): same Overture S3
  source and ATP run as us, no dataset filtering (confidence ≥0.4 gate
  instead), ATP merged via world-PMTiles z15 extract with hash-join dedupe
  (brand or first-two-words, ~150 m), OSM-coordinate-first with 30 m snap
  floor, per-region PMTiles streamed by range requests (the instant feel).
  Pins are open data; place sheets are Google fetch-on-tap. Decision: no
  pipeline adoption (attribution/postcode loss, freshness coupling,
  ~3% pack-time savings); PMTiles derivative reserved for the client
  track. Queued, unscheduled: ATP-extract ingest, tenant flagging,
  Overture confidence column, BrightQuery identification. Open: OSM
  license boundary (#5), which Vela consumption would inherit, not settle.
- Companies House tripwire live (ch stage, Southend pilot): monthly bulk
  CSV (469 MB/run, gitignored cache + shared food extract + committed
  postcode geocache) feeds new-Ltd detection — creation ONLY if ≤12 mo
  old, FSA-absent, pack-absent, unshared address (formation-agent
  suppression), and geocodable; otherwise corroboration (number, dates,
   previous names) or discard. License unverified — release gate, not
   build gate. Counting note: meta matched counts CH row-events,
   pois_matched counts distinct POIs (Southend: 108 row-events → 94
   POIs, 14 overwrites where 2 rows hit one POI; 45 created; 139 POIs
   carry ch_number).
- Stale-dump enrichment live (ta stage, local-only by decision): 1M-row
  research parquet matched against pack POIs (Southend+Helsinki gated via
  areas `"ta"` flag) — match-only, never creates; alias-aware keys;
  cuisines compared (ta_cuisines+verdict), gap hours only (ta_hours),
  Y-only dietary flags, numeric ratings + counts (emoji/chips derived at
  render). Vintage c.2021 in meta; all review-derived keys under ta_* for
  OSM-compatible stripping. Measured Southend: 622 rows → 307 matched,
  +171 hours, +176 diets; Helsinki: 1919 rows → 832 matched. CI skips
  green without the file (never committed).
- TA RUNBOOK (manual, quarterly): download the CI artifact packs for
  Southend + Helsinki → `python3 scripts/pack/ta.py
  --bbox=<from areas.json> --pois <pack>/pois.json --meta <pack>/meta.json`
  per area → review the printed audit (matched/hours/diets/verdicts/bands)
  → Southend output replaces `data/pois.json` + `data/build-meta.json`
  (committed demo data), Helsinki output replaces
  `packs/eu/fi/uusimaa/helsinki/` (gitignored serving) → restart server,
  smoke `/api/pois` counts → commit + push.

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
