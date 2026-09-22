# Conventions for Wahlwetter

Working agreements for AI sessions on this repository. Derived from
`PROJECT_BRIEF.md`, which remains the authoritative statement of intent.

## Non-negotiable

- **Never invent data.** Do not write poll numbers, election results, seat
  counts, thresholds or party lists from memory into code, tests, docs or
  fixtures. If a needed value is not available from a source in this repo,
  either stop and ask, or create a clearly marked CSV template with a
  `source_url` column for the maintainer to fill in and verify.
  This applies to legal texts too (see `LICENSE-DATA`).
- **Inspect before coding.** Never write field names against an external API
  from assumption. Fetch once, record the observed schema in `docs/`, then code
  against what was actually observed.
- **No backend, no paid infrastructure.** GitHub Actions computes, GitHub Pages
  serves static files. Any design needing a server is out of scope.
- **Be polite to upstream.** At most one request per scheduled run, and only
  when the source reports an update. Always send a User-Agent naming this
  repository.
- **Nothing is silently dropped.** Rows failing validation go to
  `data/quarantine/` with the reason, preserved verbatim.

## Architecture

- Python everywhere except front-end chart code.
- src layout: `src/wahlwetter/`. Tests in `tests/`, offline only — mock HTTP
  with `respx`, never hit the network in a test.
- **Sources are pluggable.** Every data source implements the `Source`
  interface. Modeling code must never import a concrete source; own scrapers
  will be added later as further implementations, and that must not require
  touching anything downstream.
- pydantic v2 models at every boundary — external payloads and internal tables
  alike. Use `extra="forbid"` on raw-payload models so an upstream schema change
  fails loudly rather than being silently ignored.
- Storage is Parquet files in the repo, queried with DuckDB. No database server.
- Ingestion is **idempotent**: running twice produces a clean `git diff`. That
  means deterministic row and column order, and no wall-clock values inside
  table bodies.
- Stable identifiers: `survey_id` is a deterministic hash of
  `(source, source_id)`, so IDs survive re-ingestion and coexist across sources.
- Modeling with Stan via `cmdstanpy`; the compiled model is cached in CI.

## Data placement

- `main` carries normalized tables, state files and quarantine reports.
- Raw snapshots (gzipped, timestamped, content-hashed) go to the orphan
  `data-raw` branch — never to `main`.

## Licensing

Code is MIT. Data is ODbL v1.0, inherited from dawum.de, so the derived database
is share-alike and must carry attribution on the site.

## Working style

- Small, focused commits with clear messages.
- Tests for every non-trivial function, including a deliberately malformed
  fixture per validation rule.
- Explain modeling decisions briefly in code comments and in `docs/`.
- **Present trade-offs, do not resolve them silently.** If a decision has real
  consequences, lay out the options and ask.
- **Work one phase at a time and stop at the end of each**, summarizing what was
  built, what is untested or uncertain, and what to do next.
- When the site cites studies or other models, name who is behind them, note any
  financial interests, and flag possible framing.
