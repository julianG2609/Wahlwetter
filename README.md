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

## Data source and attribution

Poll data comes from the **[dawum.de](https://dawum.de) API**, which is
published under the Open Data Commons Open Database License (ODbL) v1.0.

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

## Privacy

The site will carry no trackers and no third-party CDNs or fonts; all assets are
self-hosted. Note that GitHub Pages itself logs visitor IP addresses — this will
be stated in the Datenschutzerklärung.

## Roadmap

| Phase | Scope |
| --- | --- |
| 0 | Scaffolding, tooling, CI |
| 1 | Ingestion from the dawum API into tidy Parquet tables |
| 2 | Official election results as ground truth; simple baselines |
| 3 | Bayesian state-space model (Bundestag), backtested against the baselines |
| 4 | Seat allocation (Sainte-Laguë/Schepers) and coalition probabilities |
| 5 | Quarto website |
