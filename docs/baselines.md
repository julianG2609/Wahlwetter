# Baselines

Three simple estimators, benchmarked against the official results of the 2017,
2021 and 2025 Bundestag elections. They exist to give the Bayesian model
something to beat. A model that cannot beat a recency-weighted average of the
most recent poll per institute is not earning its complexity.

Run with `uv run wahlwetter backtest`.

## The estimators

### `last_poll_per_institute`

Unweighted mean of each institute's most recent poll. No time window and no
recency weighting: an institute that last polled months ago still counts in
full. Comparing it against `dawum_style_trend` isolates what recency weighting
is worth.

### `rolling_average_Nd`

Unweighted mean of every poll whose fieldwork ended within the last N days
(N = 14 and 30). Institutes that poll more often count more — the obvious naive
approach, and a useful contrast to the per-institute baselines. A
sample-size-weighted variant is included to test whether weighting by reported
sample size helps at all.

### `dawum_style_trend`

dawum's own published method, as documented on <https://dawum.de/Bundestag/>:

- only each institute's newest poll is used;
- a poll is included only while its last fieldwork day lies within **20 days**
  of the most recent last fieldwork day in the set;
- polls are weighted by that lag, **never below one third**;
- sample size is not used at all.

> **Assumption.** dawum documents the two endpoints (full weight at the newest
> poll, one third at the limit) but not the shape between them. We interpolate
> linearly. The weights panel on dawum's page is rendered client-side, so the
> shape could not be read off the page source. If this matters, it should be
> checked against dawum directly.

## Handling parties a poll does not report

A party absent from a poll has been folded into that institute's `Sonstige`; it
is **not** zero (see [`data_source.md`](data_source.md)). Treating it as zero
would bias small parties downwards, and dropping such polls would discard most
of the data.

We use the rule dawum documents: among institutes that do break the party out,
compute its share of the "other" bucket (its own value plus that poll's
`Sonstige`); apply that fraction to the institutes that do not, taking it out of
their `Sonstige`. Imputation is scaled back if it would drive `Sonstige`
negative.

**Where no institute at all breaks a party out, it stays at zero** — there is
nothing to infer from. This is real and it shows up: Freie Wähler was not broken
out by any institute before the 2017 election, so every baseline scores an error
of the party's full 1.00 points there. The backtest output records this per row
in `parties_without_poll_coverage` so the metric stays interpretable.

## Availability

A poll counts as available at a cutoff if it was **published** by then, not if
its fieldwork had finished. Using fieldwork would leak polls into the backtest
that no forecaster could have seen. Median publication lag is one day, so this
is a small but real distinction.

## Results

Mean absolute error in percentage points, averaged over the three elections:

| baseline | 1d | 7d | 14d | 30d | 60d | 90d |
| --- | --- | --- | --- | --- | --- | --- |
| `dawum_style_trend` | **0.949** | 1.259 | 1.331 | 1.723 | 2.464 | 2.586 |
| `last_poll_per_institute` | 1.008 | 1.260 | 1.404 | 1.773 | **2.346** | **2.424** |
| `rolling_average_14d` | 1.047 | **1.234** | 1.410 | **1.711** | 2.362 | 2.553 |
| `rolling_average_14d_n_weighted` | 1.061 | 1.261 | **1.402** | 1.776 | 2.307 | 2.569 |
| `rolling_average_30d` | 1.224 | 1.389 | 1.538 | 1.876 | 2.417 | 2.597 |

Per-election and per-party detail is written to
`data/backtest/baselines.json`.

### What this says

- **The bar is roughly 1 point at one day out, and about 2.5 at three months.**
  Any Phase 3 claim of improvement has to be measured against these numbers at
  the same horizons.
- **Recency weighting helps, but only close to the election.**
  `dawum_style_trend` wins at 1 day and is mid-pack at 60–90 days, where the
  unweighted per-institute mean is best. That is what one would expect: near the
  election there is real movement to track, far out the extra variance of
  weighting costs more than the staleness it removes.
- **Sample-size weighting is not obviously worth anything.** It is better at 60
  days and worse at 1, 7 and 30 — noise rather than signal at this sample size
  of three elections.
- **A 30-day window is worse than 14 at every horizon.** Averaging over stale
  polls hurts more than the variance reduction helps.

### Caveats

- **Three elections is a very small sample.** Differences of 0.05–0.1 points
  between baselines here are not meaningful. The ordering at 1 day
  (0.949 vs 1.008 vs 1.047) should be treated as a tie until there is more
  evidence.
- **2017 is much harder than 2021 or 2025** for all estimators (MAE ~1.3–1.7 at
  one day out, versus ~0.7–0.9), driven by a consistent overstatement of
  CDU/CSU. With three elections, that one hard case dominates the averages.
- **No log score.** The brief asks for one, but these baselines produce point
  estimates with no predictive distribution, and inventing a spread for them
  would make the comparison meaningless. Phase 3 supplies a real posterior; the
  log score belongs there, and the baselines will need an explicit,
  justified uncertainty model at that point to be scored the same way.
- **The party set comes from the official result**, so it differs per election
  (BSW exists only in 2025). Cross-election MAE comparisons are therefore not
  strictly like for like.
