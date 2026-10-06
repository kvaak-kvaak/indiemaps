# Vela prominence assessment (for the Vela builder)

Scope: `tools/vela/build-southend.sh` scored/ranked CTEs only. No changes
made here — assess-and-hand-back per 2026-10-06 decision.

## What's wrong

1. **Brand term dominates (+1.6 flat vs max confidence swing 0.8).**
   Any branded pin — ATM, parcel locker, Costa Express machine — outranks
   an unbranded restaurant on brand alone. If High-StreetCoupons-vs-cafés
   ordering feels off, this is almost certainly it. The term is also
   unmeasured: no spot-check justifies 1.6 over any other value.
2. **Flat confidence contradicts your own gating work.** Foursquare
   stamps ~0.77 on everything, Microsoft 0.85/0.97, ATP 0.85, OSM 0.8 —
   and the tree carries `overture_datasets` explicitly "for
   provider-aware quality gating… never a flat threshold", yet
   prominence consumes `(confidence−0.5)×1.6` flat. Vendor bias becomes
   rank order.
3. **No corroboration count.** A 4-source pin ties a 1-source pin,
   ceteris paribus. Evidence legs exist in the data (`origin`,
   `dsets`); the score ignores them.
4. **Dead branch:** `iskiosk()` keys on category `'atms'`, which
   `osmcat()` never emits — kiosk demotion never fires.
5. **Bias propagates to visibility.** `frank`/`rank`/`crank` derive from
   prominence, so all of the above becomes map-visibility order.

## Proposal (yours to implement)

- Cap or normalize the brand term (e.g. +0.4, or brand counts only
  alongside a second leg). Re-measure on a labelled High-Street sample.
- Replace flat confidence with provider-aware gating on `dsets`
  (Foursquare-stamped rows need corroboration, not a score donation).
- Add `+0.5` per independent corroborating source, capped.
- Delete or fix the `iskiosk` branch.
- Keep tenant −2.0 only with a labelled justification, else drop.
- Forward-compat: recommend scores use Laplace `(up+1)/(up+down+2)`
  with decay starting at first fresh review (`rec_formula
  laplace-expdecay-v1`, H≥2yr) — see our `ta-extract` spec in HANDOFF;
  same formula both sides keeps scores comparable.
