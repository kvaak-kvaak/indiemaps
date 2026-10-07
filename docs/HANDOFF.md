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
- Position verification (narrowed scope, all measured first): base pulls
  food/shop/tourism ways via out center (type-qualified identity) +
  nightclub amenity + osm_touched/version + osm_fhrs_id persist; new
  verify_positions stage (after base, before ch) merges attested
  (live FHRSID + exact name, distance-blind) and premises (exact +
  area-unique + postcode/street) pairs, adopting surveyed OSM positions
  with logged move distances (Beach Hut 1532 m; 13 merges Southend).
  Single-source FSA/postcode-precision pins get position_approx
  (area-placed badge + halo marker) — shown, never moved, never hidden.
  Hazard roads (coastal interpolation failure): hazard_roads stage flags
  no-survey food POIs within 100 m of coastline on sparse streets
  (middle path: out of default map/list, kept in search + by-id as
  needs-manual-placement); regression group = measured seafront
  specimens. Regression gate in build(): batch pins must carry approx,
  no provisional creations, verified merges must match meta audit,
  hazard records must name road class — fails the area loudly.
 - POSITION INVARIANT (load-bearing): FSA is existence authority only
   (names, ratings, inspections, delistings) — never position authority.
   Position sourcing order: OSM survey > (interpolation: BARRED pending
   explicit ruling — computed positions are confident invention) >
   FSA/postcode batch as absolute last resort, always flagged
   position_approx. Verified by audit: zero batch-precision pins render
   unflagged (recount asserts 890/890 Southend). Servicemap precision is
   trusted municipal data, exempt by decision.
 - Demo promoted (2026-09-21, 3rd): `data/pois.json` + `data/build-meta.json`
   = CI Southend run 35668293079 (1980 POIs, 0 provisional) → 1954 on-map
   default (26 hazard-tier hidden, reachable via search + by-id +
   ?include_hazard=1). Beach Hut at surveyed spot, Essex Seafood live,
   approx halo markers throughout. Map verified arithmetically.
 - Demo promoted (2026-10-01, 4th): `data/pois.json` + `data/build-meta.json`
   = CI Southend run 36845825117 (1981 POIs, spider=true, 311 with hours,
   0 provisional) → 1295 on-map default (27 hazard + 659 stale-orphan
   hidden, all reachable via search + by-id). Dead specimens hide with
   reasons; 40 pure-ch pins live. Map verified arithmetically.
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

## Open reminders

- Re-pull FSQ OS Southend to check parcel services (DPD / DHL / UPS /
  Post Offices): HF `hf://` box pull, category filter swapped from the
  Restaurants family to the shipping/post family, count + eyeball sample
  (~5 min). Needs a live HF token first (previous one revoked; accept +
  fresh token). Prior FSQ box files lived in /tmp only and are gone.

## Part 2 notes (UK-general rules, 2026-10-04)

- `scripts/pack/rules_uk.py` + tests (21 green): country configs GB/FI,
  turnover/same-premises/station/facility/service-host/CH-evidence rules,
  ordered `decide_pair`, descriptor-based `locality_from_area` replacing
  hand town lists for new geographies (building `build.js` DUP_STOP stays
  for the current estate; rewiring the matcher is a separate, measured job).
- Turnover guard corrected by measurement: postcode-only dissimilarity
  false-fires at 39% (Basildon neighbours) — guard requires same
  housenumber; numberless cases stay with fhrs:id + turnover_watch.
- Validated on release packs Basildon (992) + Chelmsford (1285): ~2700
  same-postcode pairs, zero false merges, 25/40 premises-level turnover
  holds. No exact-name same-premises merges fired (chains differ by
  branch text, as designed — conservative, review owns the rest).
- Estate is England-only (47 areas Essex/London + Helsinki); no Scottish
  areas exist, so FHIS needs no adapter until one is added (then: adapter,
  not silent reuse of FHRS parsing). ATP spiders: 42 `_gb` + 3 `_gb_ie`
  + 1 `_fi`; GB inventory extension is data, not code.

## Finland pilot-prep (2026-10-05)

- Servicemap live (1765 food units probed); `areas.json` entry, stage and
  FI country config all present — nothing blocks a Helsinki run today.
- PTV (Palvelutietovaranto) investigated and REJECTED for food: open
  REST/JSON v11, CC0, keyless GETs proven (Helsinki: 80 pages, 7988
  channels, 7037 ServiceLocations) — but the restaurant hits are student/
  staff canteens (institutional catering, out of scope like FSA_EXCLUDE).
  Zero exact-name overlap with 190 Hakaniemi-box Servicemap food units.
  Verdict: Servicemap stays the existence layer; no PTV code, no key
  needed. PTV useful only if non-food categories ever need a national
  layer. v12 redesign coming 2026 — recheck then only if scope changes.

## Step 3 notes (14 FI cities, 2026-10-05)

- 16 new areas (13 cities + Espoo as Tapiola/Matinkylä/Leppävaara
  sub-boxes; Helsinki entry untouched): centre bboxes, `fsa: null`,
  no servicemap outside Helsinki, `ta: false` pending pilot validation.
- RP coverage per city (centre/municipal): Tampere 412/529, Turku
  349/435, Helsinki 1347/1919, Lahti 142/158, others 65–222; Espoo
  centre box alone catches 24 of 1145 municipal (polycentric — hence
  sub-boxes). ta stays off until Tampere/Turku pilots validate it.
- Municipal fallback documented, not implemented: centre boxes keep
  pulls cheap; escalate to municipal boundary only where centre
  coverage proves thin (Rovaniemi/Porvoo pattern).

## Step 4 notes (ATP FI + drop decisions, 2026-10-05)

- ATP `_fi` inventory (Sep-05 run): 8 exact spiders — food-relevant are
  `hesburger` (482, no country suffix), `burger_king_fi` (75),
  `pizza_hut_fi` (22), `taco_bell_fi` (19), `k_market_fi` (1057),
  `r_kioski_fi` (269), `st1` (1143). No Kotipizza/S-group spiders.
  `burger_king_fi` output verified compatible (OSM-style props, 3 in
  Tampere box). Wiring FI spiders into `atp.js` per-area config still open.
- MyHelsinki API: all known endpoints return empty (service retired?) —
  DROPPED with reason. HRI: new hri.fi serves HTML on all API paths,
  `data.hri.fi` empty — DROPPED with reason (re-verify if Espoo/Vantaa
  regional needs arise).

## Step 5 notes (Tampere + Turku pilots, 2026-10-05)

- Both green first try (run 37337592449, spider=true): Tampere 1474
  (912 with hours), Turku 1770 (1037 with hours); merged 0 (no FSA —
  correct); 0 provisional; verify merges empty by design (no FSA rows).
  Spot checks pass (Ravinteli Huber, Blanko — OSM+overture+site).
- Q4 release now carries 3 packs (union growing as designed).
- Remaining: FI spider wiring, 12 more cities in population order,
  Espoo sub-boxes untested.

- 16 new areas (13 cities + Espoo as Tapiola/Matinkylä/Leppävaara
  sub-boxes; Helsinki entry untouched): centre bboxes, `fsa: null`,
  no servicemap outside Helsinki, `ta: false` pending pilot validation.
- RP coverage per city (centre/municipal): Tampere 412/529, Turku
  349/435, Helsinki 1347/1919, Lahti 142/158, others 65–222; Espoo
  centre box alone catches 24 of 1145 municipal (polycentric — hence
  sub-boxes). ta stays off until Tampere/Turku pilots validate it.
- Municipal fallback documented, not implemented: centre boxes keep
  pulls cheap; escalate to municipal boundary only where centre
  coverage proves thin (Rovaniemi/Porvoo pattern).

## Session log (2026-10-05, agenda round)

- ATP scope settled: 62 GB chain spiders (Sep-05 run), 180 feats in
  Southend bbox, 109 matched (+64 hours); unmatched features discarded
  by design (merge-only, no creation — creation rights still barred).
  Rules: brand-anchored, website-URL + Wikidata-QID exact passes first.
- NHS pharmacies live with hours (39 NHS-sourced: 26 matched + 11
  standalone + 2 chain); non-NHS dentists via OSM (Conservation,
  Parkhouse, Parmar ×2…); orphan rule has no category exemption.
- Structural gap found: base merges FSA↔OSM only, never OSM↔OSM —
  Parmar Dental node+way coexist unmerged. OSM↔OSM duplicate rule
  approved for build (same exact name + same premises, node position
  wins, chains excluded).
- Micro-box audit (Hamlet Court): 42/42 live named OSM objects already
  in pack — pull layer complete; misses are hiding-rule outcomes
  (reviewable) or category-filter boundaries, never silent drops.
  Standing diagnostic: one Overpass query + id-set diff per thin street.
- Open: FSQ parcel re-pull (blocked on HF token); branch decision
  (exp/address-resolution: merge/park/delete); Helsinki refresh; Vela
  backfill (post-debug); SFOS spike follow-up; FI spider wiring +
  12 more cities; France deferred.

## Session log (2026-10-05, OSM-OSM rule)

- Base merged FSA↔OSM only, never OSM↔OSM: new element-level pre-pass
  `linkOsmDuplicates` (exact normalized name min-6 + area-unique pair +
  no conflicting postcode/housenumber + <250 m tiebreak + same mapped
  category; node position wins; absorbed ids ride as `osm_absorbed`).
  Measured Southend: 3 merges (Pier Museum, Bakers Box, Havens Hospices),
  zero chain merges (Londis ×16, Spar ×4 intact).
- Parmar Dental verdict corrected: node vs way are 5.3 km apart —
  different branches or a misplaced node, NOT a duplicate. Rule
  correctly refused. Needs ground truth, not code.

## Session log (2026-10-05, fresh demo promotion)

- Demo promoted: CI Southend run 37370366037 (1978 POIs, spider=true,
  311 with hours, 0 provisional) → 1294 on-map default. Release job
  was cancelled on that run (Q4 keeps Oct-4 Southend asset — same
  vintage family, no action needed).
- First dispatch sat queued ~25 min (runner backlog), then the run
  completed normally; no code or infra implications.

## Session log (2026-10-06, ATP chain-pin creation + parking rule)

- User confirmed test streets correct for OSM + Overture; approved
  creating the rest of the ATP pins (all spiders) + parking category
  off by default. Commit 373a7aa, CI 37389445712 green (spider=false,
  release-safe), demo NOT promoted (still Oct-1 1981).
- atp.js creation pass: unmatched features with no matchCandidate hit
  anywhere become atp-{spider}-{nsi|hash} pins (stable ids, rerun
  idempotent — verified locally: 2nd run created 0, matched 130).
  Southend: created 20 (Spar, Londis, Subway, Costa Express, 5 banks
  incl 3 ATM-only records, Halfords x2, Kwik Fit, Screwfix, Argos,
  Pep&Co, Card Factory, Shell x2), skipped 14 same-store variants,
  hosted 0. Total 1978 -> 1998. All 20 carry atp-contributor keep
  evidence; position gate updated deliberately for 'atp-chain'.
- Concessions: same-pc+housenumber + exactly one shopping host ->
  hosted_in + search_only; host card shows "also here", concession
  shows host link (server by-id + app detail, selectPoi fetches
  search-only records by id). Zero/multi hosts -> standalone pin.
- Parking: new category in build.js + server live mirror +
  atpCategory. All-mode hides parking, 🅿️ chip opts in. BUT Southend
  pack has 0 parking records — base/live Overpass queries never fetch
  amenity=parking, and no ATP feature classified as parking. Rule is
  live and waiting; if parking clutter is seen somewhere, it comes
  from outside our packs — ask user where before fetching parking.
- Watch item: 3 ATM-only bank records + Costa Express (likely a
  machine) are thin-but-honest chain pins; Services will grow as
  banks/fuel/DIY land there. Recategorisation is a follow-up, not
  this change.

## Session log (2026-10-06, demo promotion to 1998)

- Promoted CI Southend run 37389445712 (1998 POIs, spider=false) to
  demo: default view 1294 -> 1314 (+20 ATP chain pins), full search
  1929, CH 142, parking 0 in view. Foodbank fsa-1796273 still absent
  (quarantine holds). Backup in /tmp/opencode/backup-pre1998/.
- New pins verified live: search + by-id (atp-subway-subway-6a374d)
  with atp-contributor keep evidence; 🅿️ chip present, stats note
  honest.

## Session log (2026-10-06, index-driven ATP import)

- Commit fc8883f, CI 37436175451 green (spider=false,
  release-safe). Demo NOT promoted (still 1998).
- Fetch rewritten to sibling importer semantics: merge set from
  Sep-5 _results.json (394 spiders = all _gb minus 46 infra +
  17 bare GB names), country-count pruning, empty_export never
  backfilled, 22 failed spiders supplemented from Sep-19 with
  per-spider provenance, 9 failed in BOTH runs recorded
  (big_yellow, cef, coop_food, gsf, heart_of_england, jollyes,
  odeon, soletrader, entertainer) with complete=false.
  Snapshot sha in meta.atp.
- Results Southend: 425 feats (was 180), matched 211 (173 via
  wikidata), created 65, total 1978 -> 2043. All six High-Street
  brands wikidata-matched and un-hidden (Primark, Specsavers,
  Yours, Coral, Waterstones, Shoe Zone). Orphan-hidden 657 -> 589.
- Bugs caught locally: created pins stripped to sources=[] on
  rerun (merge-loop clearing vs matcher gap — Costa Express);
  fixed by keeping created-chain provenance unless re-matched.
  Bare-shown audit 0, gate green.
- WHSmith/Prezzo/Vodafone: NO spider in either run — honestly
  absent, nothing to import. KFC/Domino's/John Lewis recovered
  via supplement. Costa Express + 3 ATM records remain thin-but-
  honest; new Trussell Trust foodbank pin (services) is real
  chain data, flagged for awareness.
- PMTiles tile-extract cross-check skipped: pmtiles/
  tippecanoe-decode absent locally. Ledger shows no unexplained
  gaps (350 ok + 13 empty + 9 failed-both); cross-check remains
  optional. CI refetches ~400 files per build (no actions-cache
  yet — follow-up if build time bites).

## Session log (2026-10-06, repull of the 9 failed spiders)

- Commit 55f937e: supplement ladder Sep-19 + Aug-29 + Aug-22,
  newest healthy export wins per spider. CI 37438576677 green on
  third attempt (first two failed on Overpass 504 weather in base
  stage, unrelated to the change; verified via build.log).
- Recovered 2: jollyes_gb from Aug-29 (1 Southend feat,
  wikidata-matched to OSM Jollyes node), cef_gb from Aug-22
  (healthy nationally, zero Southend feats — correctly empty).
  All 22 Sep-19 provenances unchanged (Sep-19 stays first).
- Remaining 7 fail in ALL 5 runs checked (Aug-15 to Sep-26):
  big_yellow, coop_food, gsf, heart_of_england, odeon,
  soletrader, entertainer. Nothing exists upstream to pull —
  unrecoverable until ATP fixes the spiders. complete=false
  recorded, publish-on-partial holds.
- Run discovery note: no S3 listing, no `latest` symlink; run IDs
  found by bounded Friday-13:32 seconds scan (60 probes/date).
  Known runs: Aug-15-13-32-20, Aug-22-13-32-16, Aug-29-13-32-18,
  Sep-5-13-32-25, Sep-19-13-32-18, Sep-26-13-32-25. No Oct-3 run
  at 13:32 (absent or different time).
- Demo NOT promoted (still 1998).

## Session log (2026-10-06, demo promotion to 2043)

- Promoted CI Southend run 37438576677 (2043 POIs, spider=false)
  to demo: default view 1314 -> 1427 (+45 net records, +68
  un-hidden orphans). Foodbank fsa-1796273 still absent.
  Backup in /tmp/opencode/backup-pre2043/.
- Verified live: Specsavers x2, Waterstones, Jollyes all
  searchable as shopping (base shop=* mapping — consistent,
  not a bug); six High-Street brands on the map with chain
  badges. Trussell Trust foodbank pin ships (real chain data,
  flagged 2026-10-06).

## Session log (2026-10-06, ta-extract rewrite + tiers)

- Commit 2890789 (+4e5fef0 pyyaml): data/ta-extract.parquet
  (17.4MB, 177k rows, UK nations + Finland, 24 cols) cut from the
  1.08M-row c.2021 dump; .gitignore exception with rationale.
  CI 37488090876 + 37489784280 green (spider=false, release-safe).
  Demo NOT promoted.
- Schema: identity/city/coords, price_level, meals, cuisines,
  Y/N diet flags, raw hours JSON, rec_up/down/n (+vintage
  2021-06-01, formula laplace-expdecay-v1), 4 feature booleans,
  features_other (6-value closed vocab), keywords stored-unrendered.
  Parking clustered to one token; 13 junk tokens burned; averages
  discarded; region/province/subscores/languages dropped.
- ta.py rewired: rec fields replace stars, affirmative-only flags,
  gap-only hours unchanged, extract sha in meta.ta. Southend:
  622 rows -> 333 matched, +230 hours, +190 diets, +63 flags.
  Helsinki: 1919 -> 832 matched, +180 hours, +322 diets.
  Zero old star keys remain.
- Display: Laplace % + binned reviews ("89% recommend · ~35
  reviews"), Recommended badge >=65% + n>=5, no year on card,
  decay starts at first fresh review (H>=2yr guardrail in spec).
- Weights restored: source-weights.yaml v1 + area_weights +
  manifest verified flag; per-pin tier (legs capped at 3);
  browse sorts by tier, search untouched. GB verified=true.
- Vela critique in docs/VELA_WEIGHTS_CRITIQUE.md for the other
  agent (brand-term dominance, flat-confidence contradiction,
  no corroboration count, dead iskiosk, visibility inheritance).

## Session log (2026-10-06, release week Mon: quarantine + UK rebuild)

- Quarantine list live: data/quarantine.json (fsa-1796273),
  enforced at creation (companies.py, atp.js) + recount backstop
  apply_quarantine() in build.py with meta counts. Local gate
  test green. Commit c306117, CI Southend green.
- Release-gate audit: scripts/pack/audit_packs.py (read-only,
  exit 1 on bare-shown or unapproved provisional).
- Full 47-area GB spider=true rebuild dispatched (release week
  Stream 1). Audit table due when it lands.

## Session log (2026-10-06, FI chain stages + Helsinki reference)

- Commit 4766842 (+553beed slice fix): chains_fi match-only stage
  (Restel 142 + Raflaamo 677 snapshots committed under
  data/chains_fi/) + prh corroboration stage (exact/anchored
  matching, legal-suffix strip, municipality tiebreak-never-veto,
  150-query cap, committed cache) + chain/prh keep legs +
  orphan exemptions + chain_hours/phone in app.
- PRH matcher took three iterations locally: legal suffixes
  (Oy/Ab), then municipality hard-filter killed recall (28%
  address coverage), then result-slice widened (API ignores
  limit). Final: 14 local / 35 CI corroborated, 10 ceased
  skipped, zero false attaches sampled.
- Helsinki CI needed area-aware blind tiling (b72c7ab): metro
  bbox is 12x Southend; fixed 2x2 kept 504ing the same tile
  4x. Grid now scales (Helsinki 5x3, Southend behavior
  unchanged). Same fix rescues dense London areas if the UK
  run hits them. Only triggers on total-failure path.
- Helsinki reference Q4 (37508331549): 7225 total, 0 audit
  violations, FI verified=true, osm_hours_rate 0.622 (matches
  the Hakaniemi community measurement), chains 109, prh 35,
  ta 1015. Hidden 3176 (44% OSM-only stale — doctrine working).
- 16-area FI sweep dispatched spider=false.

## Session log (2026-10-06, UK47 timeout recovery + _fi merge)

- UK47 run hit the 330-min cap at 26/47 (timeout-minutes: 330).
  19MB partial artifact banked all 26 (audit: 0 violations, all
  verified=true). Remaining 21 (all London) split: batch A (11)
  dispatched 37539667144, batch B (10) after.
- _fi spiders (8) + bare hesburger/st1 in merge set (commit
  5533a28); FI country added to pruning allowlist after it
  wrongly skipped every FI spider as non-UK (caught locally).
- ID integrity fix: nsi_id is brand-level (all Subways share
  one) — ids now always location-bound (atp-<spider>-<nsi>-<hash>).
  Caught via Vantaa Dixi wearing Southend's id; multi-branch
  chains were silently collapsing to one pin.
- rules_uk marked reference-spec (not live, not deleted);
  ATP actions-cache added (keyed on atp.js hash).
- ta enrichment default-on for all areas (extract covers UK+FI);
  the 26 banked packs predate it — ta-only refresh pass due
  after batch B (cheap: no downloads).

## Session log (2026-10-07, Camden tile fix + batch B)

- Camden failed twice on the identical tile (pattern, not weather):
  dense-tile second chance in build.js (split once more before
  failing, bounded; cap-truncation at max depth logged loudly
  instead of silent). Commit 160d0ec, Camden solo re-dispatched.
- Batch B (10 areas) dispatched parallel per instruction (shared
  Overpass load accepted).

## Session log (2026-10-07, partitioned ta extract)

- data/ta-parts/: full 1,067,607-row rewrite in the new schema,
  hive-partitioned by country (24 parts, 85MB total). Counts
  reconcile exactly (UK+FI 177,409 = committed extract;
  residual vocab closed at 6 values; spots verified).
- LICENSE GATE: source public-domain claim unverified — local
  only via *.parquet gitignore. No commit, no asset, until the
  user clears it. Other chat reads via pyarrow (whole dir or
  single country= parts, no query engine needed).
- Next packs can read per-country parts directly; ta.py keeps
  using the committed UK+FI extract until then.

## Session log (2026-10-07, PBF proof + batch B weather)

- PBF proof CLOSED: 1133/1136 OSM records identical to Overpass era,
  0 new, 3 missing all explained (intra-day edit race: PBF cut
  ~02:00 UTC, demo Overpass pull ~09:10; Norton v1 etc. created in
  the window). Unt determinism accepted: different vintages, listed
  delta. Proof gate satisfied.
- PBF chain bugs fixed along the way: export carries no metadata
  (OPL join), short n/w ids, add-locations-to-ways DELETES untagged
  member nodes (dropped, export resolves in-file), tags-filter
  deletes ways (filter in Python), complete_ways explicit, stamp
  after base (build.js rewrites meta), recount carries osm_source.
- Batch B: 5/10 banked, 5 failed on Overpass storm (ran pre-PBF
  code). Retry of the 5 dispatched with PBF default (37623506759).
- Camden attempt 3 + depth-3 cap-split committed; Camden rerun
  waits for batch B load to clear.
