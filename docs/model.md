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
- The window starts at the most recent election, so the latent series carries
  no information from before it. That is deliberate — the party landscape and
  parliament differ — but it means early-window estimates lean on the prior.
