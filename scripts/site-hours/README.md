# First-party hours spider (prototype)

Thought experiment made concrete: take Overture-derived POIs (name + website),
fetch the business's own site, extract opening hours, and record existence
signals. Covers the independents that chains (ATP) and mappers (OSM) miss.

```bash
python3 site-hours.py --in pois.json --out site_hours.json --workers 8 --limit 50
```

Input items need `name`, `website`, optional `postcode`.

## Method (in priority order)

1. **schema.org JSON-LD** (`openingHours` / `openingHoursSpecification` on
   Restaurant/Cafe/Bar/etc. nodes) — kept with branch labels for multi-site pages.
2. **Hours-page discovery** — same-host links matching hour/open/contact/visit,
   1 hop, max 3 pages.
3. **Conservative regex** on visible text — day + explicit time range only, with
   am/pm inference (`8 - 9pm` → 20–21, `9 - 5pm` → 9–17) and overnight wraps kept.

## Measured on 150 Hackney food/drink sites (Sep 2026)

- Fetched: 97/150 (65%). Of the failures: ~31 dead domains (existence-negative
  signal), ~13 bot-blocked (403, unknown — not dead), rest timeouts/redirects.
- Hours found: **35/150 (23%)** — 10 via JSON-LD, 25 via regex, no overlap.
- Name confirmed on page: 80/150. JS-only sites: ~6 (need headless rendering).
- Regex precision on audit: ~19/22 clean; known issues are bar-vs-kitchen
  double hours and same-day conflicts — both kept with raw snippets, never merged.

## Trust model (learned the hard way)

- **JSON-LD is a claim, not a source.** Template-built sites emit plausible
  structured data once and never update it (found: daily 09:00–17:00 in JSON-LD
  vs split shifts + closed Tuesdays in the footer). Every site gets an
  internal cross-check: JSON-LD table vs visible-text table.
- **Specificity wins disputes**, ties go to JSON-LD. Self-contradicting visible
  text (overlapping-but-different hours per day = bar-vs-kitchen piles) loses
  to clean structured data; both are always kept.
- **Dine-in vs delivery** separated by section headers; delivery never becomes
  venue hours.
- **Wrong-business guard** (positive-contradiction standard): skip only when
  the page positively identifies as a different business (title/JSON-LD names
  share nothing with the POI) *and* body text doesn't name it. Absence alone
  (JS shells, logo-only brands) is not contradiction. Caught: a foodbank POI
  carrying a bike shop's URL, and brand-finder pages served at pub URLs.

## Production notes

- urllib skips HTTP 308 redirects; handled manually here.
- Add robots.txt compliance + crawl-delay + contact UA before scaling.
- Social-only web presence (Facebook/Instagram as `website`) is unscrapable —
  treat as its own coverage class, not a failure.
- Facts (hours) aren't copyrightable expression, but respect sites' ToS and
  don't republish page copy — store hours strings + source URL only.
