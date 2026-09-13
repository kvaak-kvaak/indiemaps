# POI packs: area tiling + GitHub release distribution

## What OSM gives us (verified Sep 2026)

Geofabrik offers **no neat small areas**: Great Britain is one 2.0 GB file,
Finland one 730 MB file ("No sub regions are defined"), France one 4.7 GB file
with legacy pre-2016 region splits. County-level UK extracts don't exist there.
So we define our own tiling — which is fine, because our packs are *derived*
POI data (KBs–MBs), not raw OSM extracts.

## Scheme: admin-area packs, bbox-built, overlap-tolerant

- **Pack = one administrative area.** UK: Local Authority Districts (includes all
  33 London boroughs, Southend-on-Sea, Essex districts). Finland: municipalities
  (kunta). Sweden: municipalities (kommun). France: departments.
- **Why admin, not a grid:** the best sources are admin-shaped (FSA publishes per
  local authority; NHS per board; La Poste national). Yellow-Pages UX is
  place-named ("Hackney", not "geohash u10j"). Grids split high streets in half.
- **IDs mirror geography:** `eu/gb/england/greater-london/hackney`,
  `eu/gb/england/essex/southend-on-sea`, `eu/fi/uusimaa/helsinki`,
  `eu/fr/ile-de-france/paris`, `eu/se/stockholm/stockholm`.
- **Builds pull by padded bbox, never clip by polygon.** Every source we use is
  bbox-native (Overpass, Overture/DuckDB, ATP coordinate filter, site spider).
  Boundary polygons (ONS Open Geography Portal, MML, IGN Admin Express, SCB —
  or OSM `admin_level` 6/8 relations for v1) are only used to *derive* each
  pack's bbox.
- **Overlap is expected and harmless:** neighbouring packs overlap at edges;
  records dedupe on stable IDs (Overture UUID, FSA ID, OSM type+id, ATP
  `nsi_id`/`ref`, site URL). Never invent IDs.
- **Size guard:** one pack should stay (!) under ~25 MB JSON (Southend:
  1,421 POIs ≈ 0.9 MB; Hackney-scale ≈ 15–20 MB). Split any pack that outgrows
  it along obvious sub-boundaries; never split a high street.

## Distribution: GitHub releases

One release per build cycle (e.g. `pois-2026-10`):

```
pois-2026-10/
  manifest.json          # every pack: id, name, bbox, file, bytes, counts,
                         # built_at, source run IDs (fsa extract, overture
                         # release, atp run, osm timestamp)
  eu-gb-england-essex-southend-on-sea.json
  eu-gb-england-greater-london-hackney.json
  ...
```

The app fetches `manifest.json`, downloads packs intersecting the viewport,
dedupes by stable ID. **Bundles** (`essex`, `greater-london`, `england`, `uk`,
…) are manifest groupings only — no reprocessing. At release time the per-pack
`pois.json` files are flattened to `<slug>.json` assets (slugs are unique).

## Rollout order

1. Essex (12 districts + Southend + Thurrock) + Greater London (33 boroughs)
2. England — loop all LADs; FSA food coverage comes free per-LA
3. UK (Scotland/Wales/NI — no FSA hygiene needed; FSA names/addresses still apply)
4. Finland (municipalities; HRI service points as the municipal layer),
   Sweden, France (departments; La Poste calendar for postal hours)
5. Europe — fill remaining Geofabrik countries with OSM+ATP+site-hours only

## Per-layer source matrix (applies inside every pack)

| Layer | Source | Keyless | Gives |
|---|---|---|---|
| Base POIs + contact | Overture (FSQ+Meta, minus MS) | yes (S3+DuckDB) | names, coords, phone/web/postcode |
| Food names/addresses (UK) | FSA FHRS per local authority | yes | authoritative names + addresses |
| Chain hours | AllThePlaces weekly dump | yes (CC0) | first-party hours, ~85% fill |
| Mapped hours/contact | OSM Overpass per bbox | yes | `opening_hours`, `contact:*` |
| Independent hours | First-party site spider | yes | ~23–53% of sites with hours + existence signal |
| Reviews | none imported | n/a | outbound links + visitor notes |
| Hours at scale (optional) | Google / FSQ Premium | keyed | residual only |
