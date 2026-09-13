# Overture + OSM join experiment

`join.py` pulls Overture Places (Foursquare-sourced) and OSM POIs for a bbox,
joins them on name + proximity + postcode, and reports the coverage gap both ways.

```bash
pip install duckdb
python3 join.py --bbox=-0.10,51.52,0.00,51.59 --out=output/hackney
# with AllThePlaces chain hours (dir of spider *.geojson, see scripts/atp.js spiders):
python3 join.py --bbox=-0.10,51.52,0.00,51.59 --atp-dir=/tmp/atp --out=output/hackney
```

`output/hackney.stats.json` is the Hackney run (11 Sep 2026,
Overture release 2026-07-22.0, ATP run 2026-09-05, base = all non-Microsoft
sources). Raw pulls are reproducible and git-ignored. Matching is
brand-anchored on both joins: concessions (Costa-in-Nisa) and branch-led area
matches can never attach hours to the wrong business.

`output/hackney.stats.json` + `hackney.matches.json` are the Hackney run (11 Sep 2026,
Overture release 2026-07-22.0). Raw pulls are reproducible and git-ignored.

## Why Overture and not FSQ OS Places direct?

Foursquare's own open release has a richer schema per record (`date_created` /
`date_refreshed` / `date_closed`, social handles, `placemaker_url`) — but as of
Sep 2026 it is **gated**: HF mirror needs login + terms acceptance, the legacy
public S3 bucket is empty, and Portal access needs an account + token. Overture's
S3 copy stays anonymously readable and adds `confidence`, `operating_status` and
a taxonomy mapping. Neither carries opening hours (that's Premium-API-only);
the OSM join in this script is currently the only keyless hours source.
Unverified hypothesis: Overture `sources[].record_id` on Foursquare rows looks
exactly like an `fsq_place_id` (24-hex) — one Portal lookup would confirm it,
which would give a clean join key into the Premium API without fuzzy matching.
