# IndieMaps POI packs — 2026 Q4 release notes (DRAFT)

> Numbers marked TBD fill in from the audit table on cut day. Nothing
> ships with a red cell except listed exceptions below.

## Coverage (TBD)

- 64 areas: 47 England (Essex + all 32 London boroughs + City), 17 Finland
  (Helsinki metro incl. 3 Espoo sub-boxes, Tampere, Turku, + 12 cities).
- Pipeline vintage per pack in `meta.json` (per-spider ATP runs, Overture
  release, CH snapshot month, extract shas). Audit: `audit_packs.py`.

## What's new since Q3

- Index-driven chain import (394 spiders, targeted supplements with
  provenance) — High-Street chains back on the map with chain badges.
- Finnish chains (Restel/Raflaamo, match-only) + Trade Register (PRH)
  corroboration for FI areas.
- Research-extract enrichment live in every build (cuisines, gap-only
  hours, diet flags, recommend scores, feature flags).
- Recommend display (Laplace + decay-on-fresh-inflow) replaces stars.
- Evidence tiers sort browse; parking off by default; quarantine
  blocklist; area-aware Overpass tiling for metro bboxes.

## Known gaps (shipped openly, not hidden)

- 7 ATP spiders broken upstream in all runs checked (list in meta):
  Big Yellow, Co-op Food, GSF, Heart of England Co-op, Odeon,
  Soletrader, Entertainer. Auto-recover when ATP fixes them.
- No spider exists (either run) for WHSmith, Prezzo, Vodafone —
  nothing to import from.
- FI cities outside the capital region have no municipal source:
  thinner than Helsinki, honestly so.
- August-vintage supplements (Jollyes, CEF): stamped per-spider.
- Thin-but-honest pins: ATM-only bank records, vending-machine
  chains (Costa Express), locker banks. Chain-published, labelled.

## Method in one paragraph

Real data only; corroboration over proximity; creation rights closed
and gated; hiding with reasons instead of deleting; every pin carries
its evidence legs; research data fills gaps only and decays gracefully.
Full doctrine in `docs/RULES.md`. Per-pin provenance in each record;
per-pack provenance in each `meta.json`.
