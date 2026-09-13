# Planning brief: independent POI enrichment pipeline ("Yellow Pages" track)

Date: 2026-09-12. Status: researched + prototyped + measured. No production deployment yet.

## 1. What this is

A keyless-first pipeline that turns thin map POIs into Yellow-Pages listings
(name, address, phone, website, hours, photos, menu, description, reviews
access) for a mapping app (Android / SailfishOS). Proven on two pilots:
Southend-on-Sea (live web demo) and Hackney, London (data experiments).
Everything below was measured, not assumed.

## 2. Core architecture: layered enrichment, never invented

Each layer adds only what it authoritatively has. Anything unverified renders
as "unknown". When sources disagree, all variants render side-by-side with
provenance — never silently merged. Layers, in build order:

1. **Base POIs + contact — Overture Maps (S3, anonymous, keyless).**
   Current releases are Foursquare-sourced (Meta contributed only 2023–24
   era data). Keep FSQ, drop Microsoft (political/product call, already made).
   Verified fill in Hackney: phone 94%, website 98%, postcode 93%.
   Overture S3 is currently the ONLY anonymously-readable bulk path to this
   data (HuggingFace mirror is login-gated, legacy public S3 bucket is empty,
   Snowflake/Databricks/Portal all need accounts).
2. **Food existence + authoritative names/addresses — FSA FHRS (England only).**
   Per-local-authority open files, no key. FSA verifies existence (regulatory
   registrations) but carries no websites/phones/hours. Note: FSA lags new
   openings by months, so it must verify, never gate — non-FSA POIs stay.
3. **Mapped hours + contact — OpenStreetMap (Overpass, keyless).**
   `opening_hours` + `contact:*` where mapped. Sparse (~2–6%) but the ONLY
   keyless hours source for independents.
4. **Chain hours — AllThePlaces weekly dumps (CC0, keyless).**
   4,100+ spiders, OSM-format hours, ~85% fill on chains in-bbox. Consume
   dumps; do not run spiders. Caveats: current-week-only strings (holiday
   distortion — diff several crawls), unparsable days silently omitted.
5. **Independent hours + photos + menu + description — "indiespider"
   (custom-built, stdlib Python, new code, not ATP-based).** Crawls business
   websites found via Overture: schema.org JSON-LD → hours-page discovery →
   regex fallback; plus og:image, menu links, meta/OG description, cuisine,
   priceRange. Measured: 23% of Hackney food sites, 53% of Southend sample
   yielded hours. Side benefit: dead domains = existence-negative signal
   (closure detection input).
6. **Reviews — deliberately NOT scraped.** Google/TripAdvisor terms forbid it.
   Decision taken: outbound deep links to read reviews at source + local
   visitor notes. This is final unless a keyed API is procured.

## 3. Findings that change planning assumptions

- **Opening hours exist keyless in bulk in exactly three places**: OSM tags,
  ATP chain dumps, first-party websites. There is no general national
  database in the UK, France, or Finland (registers like SIRENE/Companies
  House never carry hours). Only sectoral ones: NHS pharmacy opening hours
  (England, quarterly CSV, Open Government Licence) and La Poste's opening
  calendar (France, ODbL). Factor these in per-country.
- **Neither FSQ OS Places nor Overture carries hours, by schema design**
  (verified against official schemas; hours are FSQ Premium-API-only).
  Stadia's FSQ feed truncates to geocoder schema (name/address/coords only).
- **Sources constantly disagree** (measured 7/48 identical OSM-vs-ATP strings;
  JSON-LD vs visible footer on the same site). Product position taken:
  render conflicts, don't resolve them. Bonus: conflict/specificity signals
  are cheap offline quality signals for search ranking.
- **Structured data can be the stale layer.** Found: template-generated
  JSON-LD (`09:00–17:00` daily placeholder) contradicting a maintained footer
  (split shifts, closed Tuesdays). Rule adopted: cross-check JSON-LD against
  visible text; specificity wins; keep both.
- **Matching must be brand-anchored.** Naive name+distance attaches wrong
  hours (documented cases: Costa Express machine hours → host Nisa; Lloyds
  Bank hours → "KFC Southend"; McDonald's → "Wendy's" via possessive-token
  collision). Rule: the candidate's own name/brand must share specific
  vocabulary; branch text and town names can never qualify; exact-name needs
  postcode/proximity corroboration; website-URL equality is a first-class
  match key (Check-The-Places method).
- **Overture ingests ATP but drops the hours column** — the join is
  load-bearing, not redundant. Overture `sources[].record_id` on Foursquare
  rows is format-consistent with `fsq_place_id` (unverified — one Portal
  lookup would confirm a clean join key into the Premium API).
- **Cross-validation exists**: Chain Reaction (UK-wide OSM-vs-chain match
  rates 70–99% per brand) confirms chains are well-mapped and the real gap
  is independents — consistent with our Hackney food match rate (~11%).

## 4. Measured results

- Southend pack (1,421 POIs): hours 32 → 105 (2.3% → 7.4%); 647 websites +
  773 phones backfilled via Overture; every attribution audited, zero suspect.
- Hackney bbox (28,535 Overture base): 350 unique POIs with hours (322 OSM
  pairs + 336 ATP pairs) + 60 chain POIs ATP found that exist in neither OSM
  nor the base (genuinely new coverage).
- Indiespider throughput after optimization: ~2.5 sites/sec single process
  (~8× from baseline via keep-alive pool, split timeouts, dead-domain cache,
  24 workers, checkpoint resume). ~1M sites ≈ 4–5 days one box, linear
  sharding by pack.

## 5. Distribution design (agreed)

Admin-area packs (UK LADs incl. 33 London boroughs; FI/SE municipalities; FR
departments), hierarchical IDs (`eu/gb/england/…`), builds pull by padded
bbox and dedupe on stable IDs (overlap-tolerant), shipped via GitHub releases
(`manifest.json` + one JSON per pack). 47 Phase-1 areas (Essex + Greater
London) registered with verified bboxes. Rollout: Essex/London → England →
UK → FI/SE/FR → Europe.

## 6. Open decisions for the planning agent

1. **Image bytes**: URLs + HEAD-validation stored; no CDN caching yet.
   Hotlinking is unacceptable for production — decide storage (pack assets?).
2. **JS rendering**: ~5% of sites need headless browsing; currently flagged,
   not crawled. Second-pass pool or drop?
3. **Crawl compliance**: robots.txt + per-host delays still to implement
   before scaling past samples (bot-WAFs already observed blocking repeats).
4. **Keyed residual**: Google/FSQ Premium for the remainder nobody covers —
   bounded, last-resort spend. Overture record_id→fsq_place_id confirmation
   would make this join exact.
5. **Licensing boundary**: keep OSM-derived columns separable (ODbL
   share-alike) from Apache-2.0/CC0 columns.
6. **OSM write-back**: atp2osm precedent (France-only) shows community
   process per country; current pipeline is read-only, which sidesteps it.
7. **Hygiene scores**: stripped as out-of-scope (England-only anyway).
