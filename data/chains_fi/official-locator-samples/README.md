# Official Finnish chain locator snapshots

Audit inputs only. These records have not been imported into the map or published.

| Chain | Response records | Coordinate/status eligible |
|---|---:|---:|
| Tokmanni | 208 | 208 |
| Motonet | 42 | 42 |
| Biltema | 28 | 27 |
| Puuilo | 65 | 62 |

These are whole response arrays, not independently verified national totals. Eligibility checks Finnish coordinate bounds and source enabled/active/show-on-website flags where present. Identity matching and branch validation remain necessary. Biltema's zero-coordinate `Verkkokauppa` is an online shop; Puuilo marks Vantaa Virkamies, Kurikka and Turku disabled. Do not infer why disabled or import these as active stores.

## Reproduce request discovery

- **Tokmanni:** `/stores` embeds `window.mapConfig.getAllStores`. Its referenced Magento module `Cgi_TokmanniStoreSearch/js/model/map.min.js` calls that URL with jQuery AJAX. GET `https://www.tokmanni.fi/stores/frontend/AllStores/` with `X-Requested-With: XMLHttpRequest` returns 208 records; ordinary GET returned an empty array. Preserve the positional fields and HTML address/hours in the raw file.
- **Motonet:** `/tavaratalot` loads the Next.js stores chunk, which calls `location/store/all`; the shared app client supplies base `https://www.motonet.fi/api`. GET `https://www.motonet.fi/api/location/store/all` returns `stores[]`, including coordinates, addresses, opening hours and status fields. No API key used. Raw contact fields are retained locally; only venue-relevant fields should be promoted.
- **Biltema:** cached official locator response contains `window.storeData = window.storeData || [...]`. Extract the array without executing JavaScript.
- **Puuilo:** `/myymalat` embeds an `agents` array. Keep `enabled`; coordinate presence alone is insufficient.

Per-file metadata contains source URLs, output hashes and eligibility limits. Discovery responses and source scripts remain in `review/finland-chain-coverage/probes/`. The initial and locator passes record request outcomes for all 50 shortlisted chains. Requests were sequential and cached, with pauses, no retry storms, no proxy rotation and no challenge bypass. Stopped/error sources need a separate permitted route or manual investigation, not repeated probing.

## Extended extraction and gap comparison

`tools/extract_finland_chain_gaps.py` additionally extracts cached Rusta/Kappahl `window.CURRENT_PAGE.stores`, K-Rauta `window.__PRELOADED_STATE__.storeSelector.storesList`, Budget Sport `window.BSstores`, and XXL's JSON `stores` array. No additional website requests are needed for these.

Nine inventories yield 585 records passing basic coordinate/status checks. The 50 inside the existing Helsinki envelope produce 15 single-counterpart cases, 34 multiple-counterpart cases, and one gap candidate. These are brand/proximity comparisons against all pack records, including hidden ones, not an authoritative nationwide raw-source absence audit. Status `isOpen` may mean open now and is deliberately not treated as permanent closure.

The candidate GeoJSON contains the official Motonet Vantaa location at Ansatie 1. It has no same-brand pack counterpart within 500m. This is staged for the next build, not injected into the live map. `normalized.json` retains source URLs and raw row indexes; full source fields remain in the original arrays. Do not overwrite existing coordinates or merge multiple branches based on this audit alone.

Other accessible sites without readily extractable branch arrays are left pending. Stopped sites are not retried.
