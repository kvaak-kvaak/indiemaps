# IndieMaps pipeline rules (plain English)

How the map decides what to show, what to hide, and what to never invent.
One rule per row of the audit table; nothing here is advisory.

## Existence first, position honesty always

- A place gets on the map because a source says it exists — never because
  an algorithm guessed.
- The Food Hygiene register proves a food business registered, not that
  it's open, and its coordinates are postcode-grade roughly a third of the
  time. Single-source register pins are labelled area-placed, never moved,
  never hidden.
- Anything the pipeline moves (verified merges, chain snaps) is logged
  with the old and new position and the reason. Unlogged moves fail the
  build. Maintainer-verified renames (mapper typos, rebrands) merge
  through explicit aliases with evidence — never by fuzzy matching.
- A seafront address on an inland batch position is a contradiction:
  flagged for review (the coastal gate keys on position, so it can't
  see these), never moved, never deleted.

## Corroboration, not proximity

- Two records merge only on shared identity: brand Wikidata ID, deep
  store URL, exact name on shared premises, or mapper-asserted IDs.
  Distance is a tiebreak, never the reason.
- Same-postcode neighbours up to ~1 km apart are the measured norm for
  batch geocodes. Turnover pairs (one closes, another opens at the
  address) are flagged, never merged.
- Chain-published positions (AllThePlaces, Restel, Raflaamo) corroborate
  like a second witness: same-chain + close + name agreement un-hides,
  and unmatched chain features may become pins because the chain itself
  surveyed them.

## Creation rights (closed list)

Only three paths may add a pin, each gated: new Companies House
incorporations (young + FSA-absent + unshared address + locatable),
chain-spider features with no same-store variant anywhere in the pack,
and nothing else. The build fails on any other provisional record —
a new creation path must update that gate deliberately, never slip past.
`data/quarantine.json` is the blocklist: listed ids are refused at
creation and dropped at recount, loudly.

## Hiding, not deleting

- Nothing is ever deleted. Pins leave the browse view only with a
  recorded reason: hazard tier (roads where geocoders fail), stale
  orphan (un corroborated and untouched for 6+ months), or search-only
  (concessions living on their host's card, parcels with host links).
- Everything hidden stays reachable by search and by id, with its reason
  shown. "Show unresolved" reveals the whole hidden population.
- Parking pins exist but stay off by default — too many, too rarely
  the destination. The 🅿️ chip opts in.

## Evidence on every pin

- Every shown pin carries at least one keep-evidence leg (register,
  chain, company, fresh map touch, pharmacy listing, municipal record)
  with a human-readable meaning. Shown-without-evidence fails the build.
- Tiers are evidence counts (1–3), capped and coarse: sorting help,
  never a quality verdict. Weights settle field disagreements only
  (which hours to show), per `data/source-weights.yaml`.

## Freshness and staleness

- Map touches older than six months are retention, not proof of
  opening. The register is existence-only. Review scores decay only
  once fresh reviews arrive — legacy standing is never pre-punished.
- Research-dump data (vintage 2021) enriches gaps only: cuisines are
  compared not merged, hours apply only where nothing current exists,
  review stars became recommend inputs, and averages were discarded
  as ambiguous middle.

## Money rules

- No keyed APIs without approval, no bulk crawling from home, no
  standing infrastructure. Spider and site fetches run on CI;
  per-spider provenance and snapshot hashes ride in the meta so any
  number can be traced to its pull.
