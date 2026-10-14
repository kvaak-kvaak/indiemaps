# Finnish restaurant source snapshots

National source collection, 14 October 2026. No phone pack changes.

- Friends & Brgrs: 38 markers from the official restaurants page's HTML `data-markers` attribute. Full list parsed without executing scripts. The generic probe incorrectly flagged the page because its geolocation translations contained “Location access denied”; this was not an access challenge. No challenge bypass was used.
- Picnic: 40 records from the public `ssf-wp-xml.php?wpml_lang=fi` route referenced by its locator JavaScript. The alternative static JSON route returned an error, so the site-provided XML route was used. Original HTML hours remain raw, not interpreted as permanent hours.
- Espresso House: 78 records whose country is Finland, from `https://myespressohouse.com/beproud/api/CoffeeShop/v2` with the site's `Accept-Language: fi` header. The response is international despite that header; country filtering is mandatory. Raw full response remains in the review cache. No API key.
- NoHo: 111 directory entries from `no_restaurant_feed` with lang=fi and per_page=100: pages 100 + 11 + terminal empty. These include chain-level listings and entertainment/event venues. Directory location cities are not coordinates. Filter metadata confirms restauranttypes 7=food, 8=entertainment, 1478=events.

`normalized.json` contains 156 records with valid Finnish coordinates. `public/finland-restaurant-chains.json` compares the 63 inside the current Helsinki envelope against all pack records, including hidden ones. 59 have same-brand candidates within 300m; 4 have none. This is candidate screening, not proof that raw OSM/Overture lacks a branch and not automatic merging.

Full source fields are retained in source JSONs. Metadata contains source URL and saved-file SHA. Cached HTML, scripts, XML, feeds and request ledgers reside under review/finland-restaurant-chains. Rebuild with `python -m tools.extract_finland_restaurants` from release-repository-alt. Fetches were paced and cached. Annual refresh can reuse this pattern, but changed source schemas must be validated.

Next chain-level locator targets discovered in NoHo: Hanko Aasia, Jungle Juice Bar, Sushibar + Wine, Hook, Stefan’s Steakhouse, Classic American Diner, Pizzeria Luca and Fat Lizard. NoHo membership is discovery evidence only; each chain's own national branch inventory is needed before asserting coverage.
