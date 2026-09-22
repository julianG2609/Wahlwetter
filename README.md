# Wahlwetter

An open-source aggregator for German election polls, with uncertainty-aware
statistics on top: trends with credible intervals, pollster house effects,
threshold probabilities, seat distributions and coalition probabilities.

Think of dawum.de as the data layer and this project as the analysis layer.

> **Status: early scaffolding (Phase 0).** There is no model, no website and no
> published data yet. Nothing here should be read as a forecast.

## What this is

- A **static** site. GitHub Actions does all computation; GitHub Pages serves
  precomputed files. There is no backend and no paid infrastructure.
- A **Bayesian** aggregator (planned): latent daily vote shares as a random walk
  on a log-ratio scale, with institute house effects, method effects and an
  explicit extra-variance term for non-sampling error.
- **Reproducible**: every number on the site traces back to a timestamped,
  content-hashed raw snapshot.

## What this is not

- Not a forecast of the election outcome. Poll aggregation estimates *current*
  opinion; turning that into an election-day prediction is a further modeling
  step with its own assumptions.
- Not a replacement for dawum.de. dawum collects the data; this project
  re-analyses it.
- Not a source of direct-mandate predictions. Seat allocation (Phase 4) will
  treat direct mandates as explicit, configurable assumptions, stated on the
  site — they are not inferred from polls.

## Data sources and attribution

**Polls** come from the **[dawum.de](https://dawum.de) API**, which is
published under the Open Data Commons Open Database License (ODbL) v1.0.

**Official election results** come from
**[Die Bundeswahlleiterin](https://www.bundeswahlleiterin.de)** /
Statistisches Bundesamt (Destatis), published under
[Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0),
which also requires attribution. See
[`docs/election_results.md`](docs/election_results.md) for what was retrieved
and how it was verified.

Because the tables and model outputs here are a derived database, they are
published under ODbL v1.0 as well, with attribution to dawum.de. See
[`LICENSE-DATA`](LICENSE-DATA).

The code is MIT-licensed — see [`LICENSE`](LICENSE).

We fetch at most **one request per scheduled run**, only when dawum reports an
update, with a User-Agent identifying this repository.

## Repository layout

```
src/wahlwetter/       library code (ingestion, validation, modeling)
tests/                pytest suite; no test touches the network
docs/                 data source documentation, modeling notes
data/tables/          normalized Parquet tables (committed)
data/state/           last-seen update token per source (committed)
data/quarantine/      validation failure reports (committed)
data/raw/             local scratch for snapshots (git-ignored)
.github/workflows/    ci, ingest, model, deploy
```

Raw snapshots are **not** stored on `main`. They live on an orphan `data-raw`
branch, so a normal clone stays small while the snapshots remain fully
versioned. Trade-off: that branch grows without bound (one gzipped snapshot per
day on which dawum published a change); pruning or repacking may become
necessary later.

## Running locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev        # create .venv and install deps
uv run pytest              # run tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pre-commit install  # enable git hooks (once)
```

The Stan toolchain is an optional extra and is not needed for the test suite:

```bash
uv sync --group dev --extra model
```

## Ingesting data

```bash
uv run wahlwetter ingest --source dawum
```

This checks `last_update.txt` with a conditional request and does nothing
further unless dawum reports a change, so running it repeatedly is cheap and
polite. Useful flags:

| Flag | Effect |
| --- | --- |
| `--force` | fetch even when dawum reports no update |
| `--from-snapshot PATH` | replay an archived `.json.gz`, making no request at all |
| `--fail-on-error-findings` | exit non-zero if any survey was quarantined |

Official election results, used as ground truth for validating the model:

```bash
uv run wahlwetter fetch-elections
```

This writes `data/reference/bundestag_election_results.csv`, with a
`source_url` on every row. It refuses to write the file unless the numbers
reconcile with the official published figures: shares summing to 100, vote and
seat counts matching the official totals exactly, and each recomputed share
within half a rounding step of the published percentage.

Ingestion is idempotent: running it twice leaves the Parquet files
byte-identical, which is asserted both in the test suite and in
`ingest.yml`.

## Baselines

```bash
uv run wahlwetter backtest
```

Scores three simple estimators against the 2017, 2021 and 2025 results. These
are the benchmarks the Bayesian model has to beat — currently about **1.0
percentage point** mean absolute error one day out, and **2.5** at three months.
See [`docs/baselines.md`](docs/baselines.md).

## The model

```bash
uv sync --extra model
uv run python -m cmdstanpy.install_cmdstan   # once
uv run wahlwetter model
```

Fits a Bayesian state-space model for the Bundestag: latent daily vote shares as
a random walk on a log-ratio scale, pushed through a softmax so shares sum to
one by construction, with institute house effects and an estimated effective
sample size. Writes `data/model/bundestag_trend.json`.

Convergence is **gated, not reported** — the command refuses to write output
from a fit that fails its R-hat, ESS, divergence or treedepth checks. See
[`docs/model.md`](docs/model.md), which also documents why survey-method effects
are disabled by default and why the estimated design effect needs careful
reading.

## Data model

`data/tables/` holds tidy Parquet, queryable directly with DuckDB:

```sql
SELECT p.shortcut, r.share, s.fieldwork_midpoint
FROM 'data/tables/results.parquet' r
JOIN 'data/tables/surveys.parquet' s USING (survey_id)
JOIN 'data/tables/parties.parquet' p USING (party_id)
WHERE s.parliament_id = '0'
ORDER BY s.fieldwork_midpoint DESC;
```

- `surveys` — one row per poll: institute, commissioning client, method,
  parliament, publication date, fieldwork start/end and midpoint, sample size,
  `source`, `source_id`, `retrieved_at`, and a `warnings` column naming any
  non-fatal validation rule that fired.
- `results` — long format: `survey_id`, `party_id`, `share`. A party absent
  from a poll has **no row**; that is not the same as a zero share (see
  [`docs/data_source.md`](docs/data_source.md)).
- `parliaments`, `institutes`, `taskers`, `methods`, `parties` — dimensions.

Surveys failing a validation rule at error severity are kept out of these
tables and written verbatim to `data/quarantine/`, never dropped.
`config/parties_by_parliament.json` is a reviewed allowlist: a party appearing
somewhere new raises a warning so it can be checked by hand.

## Privacy

The site will carry no trackers and no third-party CDNs or fonts; all assets are
self-hosted. Note that GitHub Pages itself logs visitor IP addresses — this will
be stated in the Datenschutzerklärung.

## Roadmap

| Phase | Scope |
| --- | --- |
| 0 | Scaffolding, tooling, CI (done) |
| 1 | Ingestion from the dawum API into tidy Parquet tables (done) |
| 2 | Official election results as ground truth; simple baselines (done) |
| 3 | Bayesian state-space model (Bundestag), backtested against the baselines (model built; backtest pending) |
| 4 | Seat allocation (Sainte-Laguë/Schepers) and coalition probabilities |
| 5 | Quarto website |
