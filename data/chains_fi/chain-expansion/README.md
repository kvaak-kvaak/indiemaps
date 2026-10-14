# Additional Finnish official chain inventories

Collected 14 October 2026; local source map only, phone packs unchanged.

- Pancho Villa: 58 branches. Coordinates from `window.FWP_JSON.preload_data.settings.map.locations`, joined by post ID to the full HTML restaurant cards. The extraction asserts every marker has a matching title. Address, URL and original page are retained.
- Fazer Café: 28 listings, 24 valid Finnish coordinate pairs. The public page supplies content ID 11630; `/api/cda/content/11630` provides category 606. Its map client calls `/api/location/map-locations?categoryIds=606&language=fi`. Four zero-coordinate records remain off-map; addresses and hours are retained for matching.
- Burger Company: 15 restaurant JSON-LD objects with addresses and opening hours, no source coordinates. These remain address-only evidence, not invented pins.
- Soupster: 41 named directory cards with official venue links. Entries may refer to chains or venues; no coordinates inferred from prose or city names. Confirmation/discovery only.
- Masku and IKH returned HTTP 403; Finnkino's host was stopped after an access restriction. These routes were left alone.
- Kotipizza page and scripts are accessible; the shop client has been identified but the final public feed route is still pending. No account/order API calls made.

`tools/extract_finland_chain_expansion.py` rebuilds saved inventories from cached source pages. `tools/match_finland_address_only.py` compares address-only entries against the existing Helsinki pack. Exact address plus brand/name can support a match; name-only cases remain review candidates. Lack of a Helsinki candidate is not evidence of nationwide absence.

NoHo is a separate confirmation layer (`public/noho-confirmation.json`), never a creation/coordinate source. Raw OSM/Overture matches are candidates until identity evidence supports them. Official addresses found for Strindberg by the Sea and Cotò are stored with source URLs in `review/finland-restaurant-chains/noho-address-evidence.json`; rerunning the audit preserves them. Other-city listings are explicitly outside our current raw spatial extract.

Official-source map additions are shown independently from the existing pack. National cross-source deduplication and production promotion remain separate steps. Preserve raw snapshots and source hashes; annual refresh must recheck source schemas and closures.
