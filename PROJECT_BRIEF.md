# Project brief: German election poll aggregator with Bayesian analysis

You are helping me build a public, open-source website that aggregates German election polls
(like dawum.de) but goes much deeper statistically: uncertainty-aware trends, pollster house
effects, threshold probabilities, seat distributions and coalition probabilities.

Read this whole brief before doing anything. Then propose a plan for the current phase, wait for my
approval, and only then implement. Work one phase at a time and stop at the end of each phase so I
can review.

## Hard constraints

- **Hosting:** public GitHub repository. GitHub Actions (standard runners) does all computation;
  GitHub Pages serves a purely static site. There is no backend server and no paid infrastructure.
- **Language:** Python for everything except front-end chart code.
- **Data source for now:** the dawum.de API only. No scraping of institutes or media sites yet.
  Design the ingestion layer so that my own scrapers can later be added as additional sources
  behind the same interface without touching the modeling code.
- **Never invent data.** Do not hardcode poll numbers, election results or seat counts from memory.
  If something is needed that the API does not provide (e.g. official election results), stop and
  tell me, or create a clearly marked CSV template with a `source_url` column for me to fill in or
  verify.
- **License compliance:** dawum data is under ODC-ODbL. Show attribution on the site. Treat any
  published derived database as share-alike under ODbL, and note this in the README.

## Tech stack (propose changes if you see a good reason, but ask first)

- Project setup: `uv`, `pyproject.toml`, src layout (`src/pollagg/`), `ruff`, `pytest`, `pre-commit`.
- HTTP and validation: `httpx`, `pydantic` v2 models for every external payload and internal table.
- Storage: Parquet files in the repo, queried with DuckDB. No database server.
- Modeling: Stan via `cmdstanpy` (cache the compiled model in CI).
- Site: Quarto static website; interactive charts with Observable JS or Plotly reading precomputed
  JSON. No trackers, no external fonts or CDNs that leak visitor IPs; self-host all assets.
- CI/CD: GitHub Actions workflows for ingest, model and deploy.

## Phase 0: Scaffolding

- Repository skeleton, tooling config, a minimal CI workflow running `ruff` and `pytest`.
- README with project goal, data source and license notes, and how to run locally.
- A `CLAUDE.md` capturing the conventions from this brief for future sessions.

## Phase 1: Ingestion from the dawum API

- First, fetch the API once and **inspect the actual JSON schema**. Do not assume field names.
  Document the observed structure (parliaments, institutes, commissioning clients/taskers, methods,
  parties, surveys with dates, fieldwork period, sample size, results) in `docs/data_source.md`.
- Implement a `Source` interface (e.g. `fetch() -> RawSnapshot`, `normalize(snapshot) -> tables`)
  and a `DawumSource` implementation.
- Only download the full database when dawum reports an update (use whatever last-update
  mechanism the API offers). Be polite: one request per run, sensible User-Agent with repo URL.
- Store raw snapshots gzip-compressed with a timestamp and content hash, but keep the main branch
  lean. Propose where they live (data branch or release assets) and explain the trade-off.
- Normalize into tidy Parquet tables: `surveys` (one row per poll, including fieldwork start/end,
  sample size, institute, client, method, parliament, `source`, `source_id`, `retrieved_at`),
  `results` (long format: survey_id, party, share) and dimension tables.
- Validation with explicit, tested rules: shares sum to roughly 100, valid dates, fieldwork end
  not after publication, sample size plausible or missing, known parties per parliament.
  Failures go to a `data/quarantine/` report, never silently dropped.
- Ingestion must be idempotent: running twice produces no changes.
- Workflow `ingest.yml`: scheduled daily plus manual trigger; commits changed data as a bot;
  uses a `concurrency` group so runs never overlap.

## Phase 2: Ground truth and baselines

- Official election results for recent Bundestag elections are needed for validation. Check whether
  the API provides them; if not, create the template CSV described above and stop for me.
- Implement simple baselines: last-poll-per-institute, rolling average, and a dawum-style
  population-free average. These are the benchmarks the Bayesian model must beat.

## Phase 3: Bayesian state-space model (Bundestag only)

- Latent daily vote shares for all parties as a random walk on a log-ratio (softmax) scale so
  shares sum to 1.
- Observation model per poll: effective sample size from the reported sample size, plus an
  estimated extra-variance term for non-sampling error; account for rounding of published values.
- Institute house effects (sum-to-zero per party) and survey-method effects (online vs. phone/mixed).
- Place each poll at its fieldwork midpoint; fall back to publication date if fieldwork is missing
  and flag it.
- Validation: backtest by fitting only on data available N days before past elections and compare
  against the baselines (MAE per party and log score). Include posterior predictive checks and
  convergence diagnostics (R-hat, ESS, divergences) that fail the CI run if they look bad.
- Output: posterior summaries (median, 50%/80%/95% intervals per party per day) as JSON.

## Phase 4: Seats and coalitions

- Implement the current Bundestag seat allocation (630 seats, Sainte-Laguë/Schepers, 5% threshold,
  the three-direct-mandate rule). Direct mandates are not modeled from polls: make them explicit,
  configurable assumptions and state them on the site.
- Unit-test the allocation against an official past result that I have verified.
- Push every posterior draw through the allocation to get seat distributions, P(party >= 5%) and
  P(majority) for each coalition. Coalition definitions live in a config file, including an option
  to exclude coalitions that parties have ruled out.

## Phase 5: Website

- Quarto site: overview (current estimates with intervals), trend chart, threshold probabilities,
  seat and coalition probabilities, pollster page (house effects, list of polls with client and
  method), methodology page, and an "is this new poll's change meaningful?" indicator.
- Methodology page must explain the model in plain language and list assumptions and limitations.
  When it cites studies or other models, name who is behind them and any financial interests, and
  note possible framing.
- Placeholder pages for Impressum and Datenschutzerklärung that I will fill in; note that GitHub
  Pages logs visitor IPs.
- Workflows: `model.yml` (runs after a successful ingest with changed data), `deploy.yml`
  (builds Quarto and deploys to Pages).

## Later phases (do not start without my instruction)

- Pollster diagnostics: signed bias per party, accuracy against the trend, herding tests.
- Hierarchical Bund/Länder model for states with stale polls.
- Analysis of commissioning clients (e.g. whether house effects differ by media outlet).
- Own scrapers as additional `Source` implementations, validated against dawum in parallel.

## Working style

- Small, focused commits with clear messages. Tests for every non-trivial function.
- Explain modeling decisions briefly in code comments and in `docs/`.
- If a decision has real trade-offs, present the options and ask me instead of choosing silently.
- At the end of each phase: summarize what was built, what is untested or uncertain, and what you
  recommend next.
