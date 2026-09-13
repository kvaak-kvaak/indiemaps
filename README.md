# Essex Yellow-Pages Map — prototype

Google-Maps-like webapp for **Southend-on-Sea (Leigh → Shoebury)** built on
**real data only**. Anything unverified renders as "unknown" — never invented.

## Run it (no API keys needed)

```bash
npm install
npm start            # → http://localhost:3000
PORT=3100 npm start  # if port 3000 is taken
npm run build:data   # rebuild listings from live sources (FSA + OSM + postcodes.io)
```

## Where every field comes from

| Field | Source | Notes |
|---|---|---|
| Business name, address, postcode | **FSA FHRS open data** (Southend-on-Sea, updated daily) + OSM `addr:*` | 1,421 listings at last build |
| Position | OSM survey point, else FSA geocode, else postcode centroid (labeled approximate) | `geo_precision` recorded per POI |
| Opening hours | **AllThePlaces chain data** (CC0, first-party) + OSM `opening_hours` + **first-party site spider** | OSM takes display priority; chain/site fallbacks labeled; conflicts shown side-by-side |
| Phone / website / email / socials | OSM `contact:*` tags only | Shown only when mapped |
| Descriptions | Wikipedia summary, notable places only | Otherwise omitted |
| Photos | **Wikimedia Commons** geosearch at view time | Honest placeholder when none exist |
| Reviews | **Not imported** (would violate Google/TripAdvisor terms) | Outbound links to Google + TripAdvisor search; visitor notes stored locally, labeled as such |
| Corrections | Visitor suggestions → `data/suggestions.json` | Honest community layer |

Rebuild any time: `npm run build:data` → `scripts/build.js` downloads the latest
FSA extract, queries Overpass, matches records by name + proximity, writes
`data/pois.json` + `data/build-meta.json` (provenance). Then `node scripts/atp.js`
merges chain opening hours from AllThePlaces (61 UK spiders, cached raw in
`data/atp/raw/`), brand-anchored so concessions (Costa-in-Nisa etc.) can't
misattribute hours.

## API

| Endpoint | Source |
|---|---|
| `GET /api/pois` | built listings (filter `bbox`, `category`, `q`) |
| `GET /api/live-pois?bbox=` | live OSM snapshot |
| `GET /api/combined?bbox=` | listings + live, de-duplicated |
| `GET /api/search?q=` | listings + Nominatim |
| `GET /api/enrich?name=` | Wikipedia summary |
| `GET /api/photo?name=&lat=&lng=` | Wikimedia Commons nearby photos |
| `GET/POST /api/reviews/:id` | local visitor notes (start empty) |
| `POST /api/suggest` | correction suggestions |
| `GET /api/meta`, `GET /api/config` | build provenance, provider status |

**To add Google Places** (real reviews/photos/hours): set `GOOGLE_PLACES_KEY` and
merge Place Details in `server.js`. The UI already renders whatever the API returns.

## Known honest gaps

- Hours still missing for independents with no chain/OSM/site record (~93% of
  Southend listings: 103 of 1,421 have hours — 32 OSM, 61 ATP chains, 10 site).
  When sources disagree, all variants are shown.
- Pubs/buildings mapped as OSM *ways* (not nodes) can be FSA-only until the ways
  query is affordable — they're still listed with real name/address.
- FSA covers food businesses; dentists, salons, non-food shops rely on OSM alone.
