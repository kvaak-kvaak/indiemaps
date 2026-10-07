# Recommend spec (shared, both builders) — `positive-share-display-v1`

## Display (observed only, never adjusted)

- Inputs per place: `rec_stars = [E, V, A, P, T]`, `rec_n` = sum.
- Card: `{round(100·(E+V)/n)}% positive · {binned n} reviews`.
  Bins: exact below 5, nearest 5 to 20, nearest 10 to 100, nearest 50 above.
- `n < 5`: actual share + literal caveat `fewer than 5 reviews`, no badge.
- `n = 0`: show nothing. No year on the card. Vintage (`2021-06-01`)
  and formula version live in pack meta only.

## Ranking + badge (adjusted, never displayed as %)

- Five-level Bayesian (Miller-style): `bayes = (C·m + 5E+4V+3A+2P+1T) / (C+n)`,
  `m` = extract-global mean stars (currently 4.125, UK+FI), `C = 10`.
  Prior and C are stamped in `meta.ta` (`rec_prior_mean`, `rec_prior_c`) —
  explicit assumptions, no hidden ones. Per-pin `rec_bayes` (3dp) computed
  at build.
- Browse order: evidence tier first, `rec_bayes` breaking ties; missing
  ratings sort by tier alone (neutral, never penalized). Search untouched.
- Badge `👍 Recommended` iff `rec_bayes ≥ 4.0 AND n ≥ 5`.

## Decay (deferred by decision)

- Legacy counts enter undecayed (`w = 1`).
- When fresh 5-step reviews arrive (`mg_stars[5]`, `mg_first_at` set):
  legacy levels multiply by `w = 2^(−t/H)`, `t` years since first fresh
  review, `H ≥ 2` floor; fresh counts enter straight. Same formula.
- AUTO-BLEND IS OFF until tested: historical and fresh render
  separately first. Rationale: one fresh review plus a decade of silence
  must not zero out legacy standing.
- Guarantees (structural): one vote moves ≤ ~`2/(4n)`; `w(t)` smooth,
  no cliff dates.

## Don't

No pseudo-counts, no priors smuggled into display, no wall-clock
pre-decay, no adjusted figure presented as an observed percentage.
Averages in the denominator always, displayed alone never.

## Acceptance fixtures (both renders must agree)

1. E. Mono `[58,13,12,7,1]`, n=91 → card `78% positive · ~90 reviews` + badge; `rec_bayes` ≈ 4.30.
2. Lone excellent `[1,0,0,0,0]` → `100% positive · 1 review · fewer than 5 reviews`, no badge.
3. All-average `[0,0,10,0,0]` → `50% positive · ~10 reviews`, no badge.
4. Ranking: thin-unanimous venues (e.g. 6/0/0/0/0) order BELOW deep-endorsed ones (e.g. 977/392/…) under Bayes despite higher raw share.
5. Decay: 100 legacy excellents + fresh `[0,0,0,1,0]` at t=0 → ≈99%; t=1yr → ≈98.9% (blend path only, currently off).
