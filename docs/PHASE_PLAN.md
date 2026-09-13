# Revised phase plan: lean pipeline for a one-person operation

Response to the planning brief (2026-09-12). Constraint accepted as primary:
one maintainer, UX background, near-zero ongoing ops. No standing box, ever.
Code changes held until this plan is agreed.

## 1. GitHub Actions sharding (concrete design)

The 6-hour cap never binds, because the shard unit is wrong in the brief's
framing: we don't shard one multi-day crawl — **one area is one job**, and
areas are minutes-large, not hours-large. Measured: Southend full build
(base+overture+atp+sites+merge) runs in ~20 seconds; the heaviest
London-borough site stage is single-digit minutes. 47 areas × ~10 minutes
worst case ≈ 8 Actions-hours per quarterly cycle — inside the free tier
(2,000 min/month private repos, unlimited public), with zero secrets
required since every source is keyless.

- **Workflow 1 `build-packs.yml`** (matrix over `areas.json`, quarterly cron
  `0 0 1 */3 *` + `workflow_dispatch`): per area, run
  `build.py --area ID [--sites for 1b+]`. Existing `--resume` (checkpoint
  every 25, skips done URLs) covers retries and the few oversized areas, not
  routine sharding. Artifacts: `pois.json` + `meta.json` per area.
- **Workflow 2 `release.yml`** (needs workflow 1): run `build.py --manifest`,
  flatten `<slug>.json` assets, cut GitHub release `pois-YYYY-Qn`.
- **The one moving part**: `dead-domains.json` committed back to the repo by
  the build job (GITHUB_TOKEN push) so quarterly runs inherit corpse knowledge.
  Everything else is hermetic per run. If even that feels like machinery,
  fallback is cold-start each quarter (cost: re-probing known-dead domains
  for ~5s each — measurable, bounded, acceptable).
- **"Last verified"**: per-pack `built_at` already exists in every
  `meta.json`; render it in the app as "last verified: <date>" per pack.
  Per-record source dates (FSA extract, ATP run, Overture release) stay in
  metadata for audit. No new instrumentation needed.

## 2. Phase plan (revised)

- **Phase 0 — infra once.** The two workflows above, dead-cache auto-commit,
  "last verified" rendering. No data work.
- **Phase 1a — food & drink, 47 areas, no spider.** Stages: base (FSA↔OSM) +
  Overture backfill + ATP. Success = existence-graded listings.
  Honesty amendment to the brief: FSA verifies existence *at last inspection*,
  with graded confidence by inspection recency — not a binary "exists" stamp.
  It lags openings by months and delists closures with lag. Closure signal
  comes from triangulation (FSA absence + Overture `operating_status` low
  confidence + dead domain), never from one source. Render "listed by FSA,
  inspected <date>", not "verified open".
- **Phase 1b — indiespider, food/drink only, same 47 areas.** Validates the
  sharded Actions path end-to-end on the smallest meaningful slice before
  anything bigger is scheduled. Includes the already-required robots.txt +
  per-host rate limiting (ships here, before any non-sample run).
- **Phase 1c — independent non-food shops, same areas, with the gap named
  in the output.** No FSA equivalent exists (verified: Companies House won't
  cover sole traders — agreed, not a substitute). These listings render with
  an explicit lower-confidence provenance ("Overture + crawl only, no
  independent existence check") rather than silently inheriting food-grade
  trust. That label is part of the acceptance criteria, not polish.
- **Gates (unchanged, restated):** nothing past phase-1 geography, no new
  categories beyond §3 ordering, no keyed/paid APIs, until 1a–1c run clean
  end-to-end on Actions.

Cuts accepted outright: JS rendering dropped (the ~5% stays flagged,
never crawled); image bytes dropped entirely this phase (no crawl, store,
or hotlink — removes the storage design and grey legal question together).
Menu links, descriptions, cuisine, priceRange spidering continues — none of
it was cut and all of it rides the same fetches.

## 3. Category weighting (past phase 1)

Scored H/M/L. (a) staleness risk of base data, (b) free existence verifier,
(c) day-to-day hours need, (d) crawlability.

| Category | (a) stale? | (b) verifier? | (c) need? | (d) crawl? | Order |
|---|---|---|---|---|---|
| Pharmacies | L | **YES — NHS list ships hours too** | H (urgent "open now") | M | **First** — nearly free, hours included |
| Pubs/bars | M–H | none | H | H (own sites common) | Next |
| Cafés | H | none | M–H | H | With pubs |
| Dentists/opticians/health | L | NHS ODS (existence only) | M | M | Cheap via ODS; after food |
| Takeaways | H | FSA (registered, partial) | M | L–M (Deliveroo-walled, thin sites) | After cafés; temper yield expectations |
| Hair/beauty | M | none | L–M | **L (Instagram-only common)** | Later; social-only is unscrapable |
| Post offices (UK) | L | unknown (Post Office Ltd list unverified) | M | n/a | Research item, not scheduled |
| Banks, supermarkets | L | n/a (ATP covers) | L | n/a | **Skip spider entirely** |
| Places of worship, estate agents | L | none | L | L | Deprioritized |

Notes: takeaway yield will underperform Southend/Hackney food rates (platform
walls); hair/beauty is the worst crawlability-per-effort on the table and
should not be scheduled on feel. No category beyond this table gets proposed
until 1a–1c are green.
