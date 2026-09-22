# The Bayesian trend model

Bundestag only. Source: [`trend.stan`](../src/wahlwetter/model/stan/trend.stan).
Run with `uv run wahlwetter model`.

## What it estimates

A latent daily vote share for each party, on a log-ratio scale relative to a
reference category (`Sonstige`), pushed through a softmax so the shares sum to
one by construction. No renormalisation happens anywhere downstream.

The latent series follows a random walk:

```
z[t, k] = z[t-1, k] + sigma_rw[k] * eps[t, k],   eps ~ Normal(0, 1)
```

Each poll is placed at its **fieldwork midpoint**. As of 2026-09 every dawum
survey carries fieldwork dates, so the publication-date fallback is implemented
and tested but never triggers on real data.

## Observation model

For poll *i* and reported group *g*:

```
mu       = sum of the latent shares in group g
var      = design_effect * mu(1-mu)/n_i  +  tau^2  +  rounding_var_i
observed ~ Normal(mu, sqrt(var))
```

Three variance components, each doing a distinct job:

- **`design_effect * mu(1-mu)/n`** — sampling error, scaled by an *estimated*
  design effect rather than assumed to equal simple random sampling. This is
  what makes the effective sample size effective.
- **`tau`** — non-sampling error, common to all polls.
- **`rounding_var`** — dawum publishes values on a 1.0 or 0.5 point grid (see
  [`data_source.md`](data_source.md)). A value rounded to grid *d* carries
  uniform error with variance *d²/12*. The grid is detected per poll.

### Parties a poll does not report

A party absent from a poll was folded into that institute's `Sonstige`; it is
not zero. Rather than imputing it, the model **groups**: those parties join the
reference group and the model compares the *sum* of their latent shares against
the single reported value. Nothing is invented, and polls that break out more
parties simply contribute more constraints.

A party broken out by fewer than 50% of polls in the window gets no latent
series of its own and stays pooled into the reference — otherwise its path
would be almost entirely prior-driven. In the current window this excludes
Freie Wähler (2.6% of polls).

## House effects

Per institute, per party, applied to the log-ratio **before** the softmax, so
they act multiplicatively on shares. A one-point house effect on a party at 30%
is not the same quantity as at 3%, and treating it as such is a common error.

Constrained to sum to zero over institutes for each party. Without that, the
house effects and the latent level are identified only up to a common shift:
every institute could be "two points high" with the level two points low.

## Survey-method effects are OFF by default

The brief asks for method effects (online vs. phone/mixed). They are
implemented, behind `--include-method-effects`, and **disabled by default**.

In the window since the 2025 election, **9 of 11 institutes use exactly one
survey method**:

| Institute | Telephone | In person | Online | Phone & online | Methods used |
| --- | --- | --- | --- | --- | --- |
| INSA | 0 | 0 | 81 | 80 | 2 |
| Forsa | 79 | 0 | 0 | 0 | 1 |
| Forschungsgruppe Wahlen | 0 | 0 | 0 | 28 | 1 |
| YouGov | 0 | 1 | 18 | 0 | 2 |
| Verian (Emnid) | 19 | 0 | 0 | 0 | 1 |
| Ipsos | 0 | 0 | 19 | 0 | 1 |
| Infratest dimap | 0 | 0 | 0 | 19 | 1 |
| Allensbach | 0 | 18 | 0 | 0 | 1 |
| GMS | 0 | 0 | 0 | 10 | 1 |
| pollytix | 0 | 0 | 9 | 0 | 1 |
| Institut Wahlkreisprognose | 0 | 0 | 1 | 0 | 1 |

A global method effect is therefore identified almost entirely through INSA's
81/80 split, and would then be applied to Forsa, Allensbach and everyone else.
The sum-to-zero constraints make the model *formally* identified, which is worse
than being visibly unidentified: the posterior would look confident while
resting on one institute's internal practice.

The honest options are (a) leave it off, (b) turn it on with a tight prior and
state that it is INSA-driven, or (c) model it as a within-institute interaction
only, for institutes that actually vary their method. **This needs a decision;
the default is (a).**

## Priors

| Parameter | Prior | Reasoning |
| --- | --- | --- |
| `z_init` | Normal(0, 2) | log-ratio against a reference near 4%; a party at 30% sits around 2 |
| `sigma_rw` | half-Normal(0, 0.02), capped at 0.5 | ~2% relative change per day |
| `sigma_house` | half-Normal(0, 0.15), capped at 2 | house effects of a few points |
| `tau` | half-Normal(0, 0.02), capped at 0.2 | up to ~2 points of non-sampling error |
| `design_effect` | Lognormal(0, 0.5), in [0.05, 10] | centred on simple random sampling |

The upper bounds are far outside any plausible value. They exist only because
without them warmup could push the log-ratios to infinity, where `softmax()`
returns NaN and the proposal is rejected with an alarming message.

## Fit diagnostics

Convergence is **gated, not reported**: `wahlwetter model` refuses to write
output from a fit that fails, unless explicitly overridden.

| Check | Threshold |
| --- | --- |
| max R-hat | ≤ 1.01 |
| min bulk ESS | ≥ 400 |
| min tail ESS | ≥ 400 |
| divergent transitions | 0 |
| transitions at max treedepth | ≤ 5% |

Current fit (2025-02-23 to 2026-09-18, 382 polls, 8 parties, 573 days,
4 chains × 1000 warmup + 1000 sampling, `adapt_delta = 0.95`):

- max R-hat **1.0062**, min bulk ESS **847**, min tail ESS **1389**
- **0** divergent transitions, **0** at max treedepth
- runtime about 10 minutes on 4 cores

## Posterior predictive check

Observations falling outside their predictive interval:

| | expected | observed |
| --- | --- | --- |
| outside 95% interval | 5.0% | **3.5%** |
| inside 50% interval | 50.0% | **55.6%** |

Slightly conservative, which is the safer direction. An earlier version with
the design effect fixed at 1 was badly over-dispersed (1.7% outside the 95%
interval, 67.6% inside the 50%) — its intervals were far too wide.

## The design effect, and why it needs care

The estimated design effect is **0.136** (80% interval 0.098–0.176). Published
poll values vary only about a seventh as much as simple random sampling at the
reported sample size would imply — an effective sample size roughly seven times
the reported one.

That is not a claim that German polls are seven times more accurate than their
sample size suggests. What is measured is the variability of **published**
numbers, and institutes publish weighted *projections*, not raw sample
proportions — dawum says so explicitly. Smoothing, weighting and anchoring to
previous results all reduce variance. So do two quite different things:

1. genuine methodological efficiency (stratification, quota sampling), or
2. **herding** — institutes converging on each other and on prior results.

**This model cannot distinguish them**, and the two have opposite implications.
If the low design effect is herding, the true uncertainty is larger than the
intervals here suggest, and the site would be overconfident. The brief already
lists herding tests as later work; this result makes them a priority rather
than a nicety.

A second, related caution: `sigma_rw` and the observation variance are partially
confounded, as in any state-space model. A random walk free to move can absorb
scatter that is really observation noise, pushing the estimated design effect
down. The two should be varied together in a sensitivity check.

## House effects from thin data

Institutes differ enormously in how often they poll. In the current window:

| Institute | Polls |
| --- | --- |
| INSA | 161 |
| Forsa | 79 |
| Forschungsgruppe Wahlen | 28 |
| Allensbach, Infratest dimap, Ipsos, Verian, YouGov | 18–19 each |
| GMS | 10 |
| pollytix | 9 |
| Institut Wahlkreisprognose | **1** |

The first run produced a **+5.65 point** house effect on AfD for Institut
Wahlkreisprognose, with an 80% interval excluding zero — fitted to a single
poll. It reads as a confident finding and is nothing of the sort. The low
estimated design effect makes this worse, because the model treats that one
poll as highly informative.

Output therefore carries `n_polls` and a `reportable` flag per institute, false
below five polls in the window, and `excludes_zero_80` is forced false for
those. **The site must not display non-reportable house effects as findings.**
The institute's poll still informs the trend; only the house-effect estimate is
withheld.

This is a mitigation, not a fix. The proper treatment is stronger hierarchical
shrinkage so thin institutes are pulled towards zero rather than filtered after
the fact.

## Backtest against the baselines

`uv run python` via `wahlwetter.model.backtest`, writing
`data/backtest/model_vs_baselines.json`. Three elections, horizons of 1, 14 and
30 days, a 365-day fitting window clamped to the earliest available poll.

Both sides are scored on the same quantity, deliberately:

* **Availability is publication**, not fieldwork, for the model and the
  baselines alike.
* **The party set is the model's.** The model pools rarely-reported parties
  into the reference category, so the official result is aggregated the same
  way and the baselines are re-run on that set. Scoring the model on parties it
  does not estimate, while the baselines impute them, would rig the comparison.

### Result: the model does not currently beat the baselines

Mean MAE in percentage points, averaged over the three elections:

| method | 1d | 14d | 30d |
| --- | --- | --- | --- |
| `bayesian_trend` | 0.998 | 1.428 | 1.886 |
| `dawum_style_trend` | 1.024 | 1.414 | 1.918 |
| `last_poll_per_institute` | 1.113 | 1.513 | 1.979 |
| `rolling_average_14d` | 1.158 | 1.518 | 1.909 |
| `rolling_average_14d_n_weighted` | 1.150 | 1.518 | 1.988 |
| `rolling_average_30d` | 1.341 | 1.677 | 2.061 |

That table appears to put the model narrowly ahead at 1d and 30d. **It should
not be read that way**, for two reasons.

**It wins 4 of 9 times.** Per election and horizon, against the best baseline
in each cell:

| | 1d | 14d | 30d |
| --- | --- | --- | --- |
| 2017 | −0.104 | −0.107 | −0.253 |
| 2021 | −0.106 | +0.038 | +0.067 |
| 2025 | +0.133 | +0.110 | +0.197 |

(negative = model better). The model wins every 2017 cell and loses every 2025
cell. That is a pattern worth understanding, not a win: with three elections it
is indistinguishable from noise, and the aggregate margin of 0.026 points at 1d
is an order of magnitude smaller than the spread between elections.

**Six of the nine fits failed their diagnostics**, and the aggregate is
therefore built mostly on fits that cannot be trusted:

| fit | diagnostics | problem |
| --- | --- | --- |
| 2017 h=1 | FAIL | R-hat 1.0148; 753/3000 transitions at max treedepth |
| 2017 h=14 | FAIL | 750/3000 at max treedepth |
| 2017 h=30 | pass | |
| 2021 h=1 | FAIL | R-hat 1.0108; 1500/3000 at max treedepth |
| 2021 h=14 | FAIL | 843/3000 at max treedepth |
| 2021 h=30 | FAIL | 750/3000 at max treedepth |
| 2025 h=1 | pass | |
| 2025 h=14 | pass | |
| 2025 h=30 | FAIL | R-hat 1.0135 |

The dominant failure is treedepth saturation, run at `max_treedepth = 10` and
`adapt_delta = 0.92` to keep nine fits tractable. Saturation is an efficiency
problem rather than outright bias, but combined with R-hat above 1.01 these
numbers are not trustworthy.

**The conclusion stands regardless: there is no evidence the model beats the
baselines.** It must not be presented on the site as better until there is.

### Log score and interval coverage

The brief asks for a log score alongside MAE. The baselines produce point
estimates with no predictive distribution, so only the model can be scored this
way; `score_posterior()` reports the mean log density of the official result
under the latent posterior, together with 80% interval coverage and the mean
absolute z-score.

**Read these as a calibration check, not as a forecast score.** They compare a
posterior for *opinion at the cutoff* against the *eventual result*. Between
the two lie the horizon and whatever systematic polling error existed. A
confident latent posterior will score badly, and that is the point: it
demonstrates directly that these intervals are not forecast intervals and must
never be presented as the probability of an election outcome.

These were added after the run reported above, so the numbers in
`model_vs_baselines.json` do not yet include them.

### Why the model might be losing on 2025

Worth investigating rather than assuming:

- The 2025 window is the one with the most polls and the least movement, which
  is where a well-tuned average is hardest to beat.
- BSW is modelled in 2025 but not in 2017 or 2021, so the party sets differ and
  the MAE denominators differ with them.
- The house effects are estimated over the whole window but scored at its end;
  if they drifted, the model is carrying stale corrections that the
  recency-weighted baselines are not.

## Known limitations

- **Not a forecast.** This estimates current opinion. Turning it into an
  election-day prediction requires modelling how opinion moves between now and
  then, which is not done here.
- **No backtest against the baselines yet.** Until the model is scored on past
  elections at the same horizons as [`baselines.md`](baselines.md), there is no
  evidence it beats a recency-weighted average. It must not be presented as
  better until there is.
- **House effects are assumed constant** over the window. Institutes change
  method and weighting; a slowly varying house effect would be more realistic.
- **No correlation between parties** beyond the softmax constraint. In reality
  movements between neighbouring parties are correlated.
- **Model output is not committed.** `data/model/bundestag_trend.json` is
  680 KB and would be regenerated daily; versioning it would add hundreds of
  megabytes a year. It is fully reproducible from the committed snapshot and a
  fixed seed, so the deploy workflow generates it instead.
- The window starts at the most recent election, so the latent series carries
  no information from before it. That is deliberate — the party landscape and
  parliament differ — but it means early-window estimates lean on the prior.
