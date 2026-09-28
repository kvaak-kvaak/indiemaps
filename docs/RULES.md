# IndieMaps pipeline rules (plain English)

## How this pipeline decides things

**1. Only real data, ever.** Every pin comes from an actual source: the food register, the map community, the companies register, a chain's own website. If no source says it, it doesn't go on the map. We never guess, average, or invent — not names, not hours, not positions.

**2. Fix the rule, not the row.** When a pin is wrong, we don't hand-edit it. We find the rule that produced it and fix that, so the same mistake can't happen to a hundred other pins we haven't looked at yet.

**3. The food register says *what exists*, never *where it is*.** The register is trusted for names, inspections, ratings, and closures. Its coordinates are office-assigned batch points, often shared across whole postcodes. So positions come in this order: surveyed map positions first, register coordinates last and always labelled. Anything in between (like computing a position) is forbidden — a computed pin is a guess dressed up as data.

**4. A pin renders only on a surveyed position.** Either the map community surveyed it, or its exact address (number plus street, or named unit) matches a surveyed address point. Everything else stays in the data and searchable, but off the map. No estimation anywhere in this chain.

**5. Merging needs proof of identity, and distance isn't proof.** Two records merge only on hard identity: a mapper-linked register ID, or the exact same unusual name plus matching postcode or street. Being close together proves nothing — on a dense high street everything is close together. Same premises with different names means a *turnover* (old tenant out, new tenant in), which is flagged, never merged.

**6. Mapped venues with no corroboration follow a freshness rule.** A map-community record nobody else confirms stays visible while its map touch is fresh (under 6 months); older or undateable ones hide until something corroborates them. Unassessable freshness is not freshness.

**7. Nothing is created on one source's word.** A new pin from map, company, or archive data only enters the map backed by an independent check. Anything that can't clear that bar stays out. The build itself fails loudly if an unapproved creation ever slips through.

**8. Nothing is ever deleted.** Dead venues, false creations, demolished pins — all stay in the data, flagged and hidden from the map where appropriate, reachable by direct lookup. Deletion destroys evidence of how the mistake happened.

**9. Stale data enriches, never leads.** Old or archived sources may add colour (a cuisine, an old rating, clearly labelled as archived) but never names, positions, or opening status. Current sources always win by default.

**10. The map shows its uncertainty.** Fully verified pins render plainly. Single-check pins (one string match, proximity merges) render with a visible qualification. Unplaced pins stay off the map but findable by search. What you see is what the pipeline actually knows — no confident rendering of doubtful data.

**11. Helsinki runs on trusted municipal data.** Where no food register exists, the city service map's positions are trusted as surveyed; the same verdict shares are recorded so both regimes stay comparable.

**12. Check before claiming.** No result is reported until it has been measured on real data: counts reconciled, specimens eyeballed, regression shares holding. "It should work" is not a result.

**13. Tread lightly.** No paid or keyed data sources without explicit approval. No heavy downloading from home networks — big pulls run on CI. No permanent servers or infrastructure; everything rebuilds from scripts.
