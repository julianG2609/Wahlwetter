"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from wahlwetter.findings import Severity
from wahlwetter.ingest import result_summary, run_ingest
from wahlwetter.sources.registry import available_sources, get_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wahlwetter")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="fetch and normalize a source")
    ingest.add_argument("--source", default="dawum", choices=available_sources())
    ingest.add_argument(
        "--force",
        action="store_true",
        help="fetch even when the source reports no update",
    )
    ingest.add_argument(
        "--from-snapshot",
        type=Path,
        help="replay an archived .json.gz snapshot instead of making any request",
    )
    ingest.add_argument(
        "--fail-on-error-findings",
        action="store_true",
        help="exit non-zero if any survey was quarantined (used in CI)",
    )
    elections = sub.add_parser(
        "fetch-elections",
        help="fetch official Bundestag election results from Die Bundeswahlleiterin",
    )
    elections.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output CSV (default: data/reference/bundestag_election_results.csv)",
    )

    back = sub.add_parser("backtest", help="score the baselines against past elections")
    back.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write full results as JSON (default: data/backtest/baselines.json)",
    )
    back.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=None,
        help="days before each election to evaluate at",
    )

    model = sub.add_parser("model", help="fit the Bayesian trend model")
    model.add_argument(
        "--start",
        type=date.fromisoformat,
        default=None,
        help="window start (default: the most recent election)",
    )
    model.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="window end (default: the latest fieldwork date)",
    )
    model.add_argument("--warmup", type=int, default=1000)
    model.add_argument("--sampling", type=int, default=1000)
    model.add_argument("--chains", type=int, default=4)
    model.add_argument("--adapt-delta", type=float, default=0.95)
    model.add_argument(
        "--include-method-effects",
        action="store_true",
        help="estimate survey-method effects (see docs/model.md on identifiability)",
    )
    model.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output JSON (default: data/model/bundestag_trend.json)",
    )
    model.add_argument(
        "--allow-bad-diagnostics",
        action="store_true",
        help="write the output even if convergence checks fail (never use in CI)",
    )

    mbt = sub.add_parser(
        "backtest-model",
        help="score the Bayesian model against the baselines on past elections",
    )
    mbt.add_argument("--horizons", type=int, nargs="+", default=None)
    mbt.add_argument("--window-days", type=int, default=365)
    mbt.add_argument("--warmup", type=int, default=1000)
    mbt.add_argument("--sampling", type=int, default=1000)
    mbt.add_argument("--chains", type=int, default=4)
    mbt.add_argument("--adapt-delta", type=float, default=0.95)
    mbt.add_argument("--max-treedepth", type=int, default=12)
    mbt.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output JSON (default: data/backtest/model_vs_baselines.json)",
    )

    sitedata = sub.add_parser("site-data", help="write the precomputed JSON the static site reads")
    sitedata.add_argument(
        "--model-json",
        type=Path,
        default=None,
        help="model output (default: data/model/bundestag_trend.json)",
    )
    sitedata.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: site/data)",
    )
    sitedata.add_argument("--recent-polls", type=int, default=60)
    sitedata.add_argument(
        "--trend-keep-every",
        type=int,
        default=1,
        help="thin the daily trend to every Nth day to shrink the payload",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "site-data":
        return _site_data(args)

    if args.command == "backtest-model":
        return _backtest_model(args)

    if args.command == "model":
        return _model(args)

    if args.command == "backtest":
        return _backtest(args)

    if args.command == "fetch-elections":
        return _fetch_elections(args)

    if args.command != "ingest":  # pragma: no cover - argparse enforces this
        return 2

    result = run_ingest(get_source(args.source), force=args.force, from_snapshot=args.from_snapshot)
    print(json.dumps(result_summary(result), indent=2, sort_keys=True))

    if result.tables is not None:
        by_rule: dict[str, int] = {}
        for f in result.tables.findings:
            if f.severity is Severity.WARNING:
                by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
        for rule, count in sorted(by_rule.items()):
            print(f"warning: {rule}: {count}", file=sys.stderr)

    if args.fail_on_error_findings and result.n_quarantined:
        print(
            f"error: {result.n_quarantined} survey(s) quarantined; see {result.quarantine_path}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _fetch_elections(args: argparse.Namespace) -> int:
    from wahlwetter.config import DATA_DIR
    from wahlwetter.reference.bundeswahlleiterin import (
        fetch_results_csv,
        parse_results_csv,
        rows_to_csv,
        to_rows,
        validate_rows,
    )

    out = args.out or DATA_DIR / "reference" / "bundestag_election_results.csv"
    rows = to_rows(parse_results_csv(fetch_results_csv()))

    problems = validate_rows(rows)
    if problems:
        # Never write a table that does not reconcile with the official figures.
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    rows_to_csv(rows, out)
    years = sorted({r.election_year for r in rows})
    print(json.dumps({"rows": len(rows), "elections": years, "out": str(out)}, indent=2))
    return 0


def _backtest(args: argparse.Namespace) -> int:
    from wahlwetter.backtest import (
        DEFAULT_HORIZONS,
        format_table,
        rows_to_dicts,
        run_backtest,
        summarize,
    )
    from wahlwetter.config import DATA_DIR
    from wahlwetter.polls import load_election_results, load_polls
    from wahlwetter.storage import write_json

    horizons = tuple(args.horizons) if args.horizons else DEFAULT_HORIZONS
    polls = load_polls()
    elections = load_election_results()
    rows = run_backtest(polls, elections, horizons=horizons)

    print(format_table(rows, horizons))
    print()
    print(
        "mean absolute error in percentage points, averaged over "
        f"{len({r.election_year for r in rows})} elections"
    )

    out = args.out or DATA_DIR / "backtest" / "baselines.json"
    write_json(
        {
            "horizons": list(horizons),
            "elections": sorted({r.election_year for r in rows}),
            "summary": summarize(rows),
            "rows": rows_to_dicts(rows),
        },
        out,
    )
    print(f"\nwrote {out}")
    return 0


def _model(args: argparse.Namespace) -> int:
    from wahlwetter.config import DATA_DIR, TABLES_DIR
    from wahlwetter.model import fit as fitting
    from wahlwetter.model.data import build_model_data
    from wahlwetter.model.output import build_output, latest_estimates
    from wahlwetter.polls import load_election_results, load_polls
    from wahlwetter.storage import read_parquet, write_json

    if not fitting.cmdstan_available():
        print(
            "error: CmdStan is not available. Install it with\n"
            "  uv sync --extra model && uv run python -m cmdstanpy.install_cmdstan",
            file=sys.stderr,
        )
        return 2

    polls = load_polls()
    # Default window starts at the most recent election: before it, the latent
    # series describes a different parliament and a different party landscape.
    start = args.start or max(e.election_date for e in load_election_results())
    end = args.end or max(p.fieldwork_midpoint for p in polls)

    model_data = build_model_data(
        polls, start, end, include_method_effects=args.include_method_effects
    )
    config = fitting.SamplingConfig(
        chains=args.chains,
        parallel_chains=args.chains,
        iter_warmup=args.warmup,
        iter_sampling=args.sampling,
        adapt_delta=args.adapt_delta,
    )

    print(
        f"fitting {model_data.stan_data['n_polls']} polls, "
        f"{model_data.n_parties} parties, {model_data.n_days} days "
        f"({start} to {end})",
        file=sys.stderr,
    )
    fit = fitting.sample(model_data, config)

    diagnostics = fitting.collect_diagnostics(fit, config)
    ppc = fitting.posterior_predictive_check(fit, model_data)

    for problem in diagnostics.problems:
        print(f"error: convergence: {problem}", file=sys.stderr)

    if not diagnostics.ok and not args.allow_bad_diagnostics:
        print(
            "error: refusing to write output from a fit that failed its convergence checks",
            file=sys.stderr,
        )
        return 1

    parties = read_parquet(TABLES_DIR / "parties.parquet")
    institutes = read_parquet(TABLES_DIR / "institutes.parquet")
    output = build_output(
        fit,
        model_data,
        diagnostics=diagnostics.to_dict(),
        ppc=ppc,
        party_names=parties.set_index("party_id")["shortcut"].to_dict(),
        institute_names=institutes.set_index("institute_id")["name"].to_dict(),
    )

    out = args.out or DATA_DIR / "model" / "bundestag_trend.json"
    write_json(output, out)

    names = parties.set_index("party_id")["shortcut"].to_dict()
    print(json.dumps({"diagnostics": diagnostics.to_dict(), "ppc": ppc}, indent=2))
    print()
    for party, values in sorted(latest_estimates(output).items(), key=lambda kv: -kv[1]["median"]):
        print(
            f"  {names.get(party, party):14s} {values['median']:5.1f}  "
            f"80%: [{values['q10']:4.1f}, {values['q90']:4.1f}]"
        )
    print(f"\nwrote {out}")
    return 0


def _backtest_model(args: argparse.Namespace) -> int:
    from wahlwetter.config import DATA_DIR
    from wahlwetter.model import fit as fitting
    from wahlwetter.model.backtest import (
        DEFAULT_BACKTEST_HORIZONS,
        format_comparison,
        rows_to_dicts,
        run_model_backtest,
        summarize_model_backtest,
    )
    from wahlwetter.polls import load_election_results, load_polls
    from wahlwetter.storage import write_json

    if not fitting.cmdstan_available():
        print("error: CmdStan is not available", file=sys.stderr)
        return 2

    horizons = tuple(args.horizons) if args.horizons else DEFAULT_BACKTEST_HORIZONS
    config = fitting.SamplingConfig(
        chains=args.chains,
        parallel_chains=args.chains,
        iter_warmup=args.warmup,
        iter_sampling=args.sampling,
        adapt_delta=args.adapt_delta,
        max_treedepth=args.max_treedepth,
    )

    rows = run_model_backtest(
        load_polls(),
        load_election_results(),
        horizons=horizons,
        window_days=args.window_days,
        config=config,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )

    print(format_comparison(rows, horizons))
    print()
    print("mean absolute error in percentage points, averaged over elections")

    model_rows = [r for r in rows if r.method == "bayesian_trend"]
    failed = [r for r in model_rows if r.diagnostics_ok is False]
    print(f"\nfits passing diagnostics: {len(model_rows) - len(failed)}/{len(model_rows)}")
    for row in failed:
        print(
            f"  FAILED {row.election_year} h={row.horizon_days}d: "
            f"{'; '.join(row.diagnostic_problems)}",
            file=sys.stderr,
        )

    wins = 0
    for row in model_rows:
        others = [
            r.mae
            for r in rows
            if r.method != "bayesian_trend"
            and r.election_year == row.election_year
            and r.horizon_days == row.horizon_days
        ]
        if others and row.mae < min(others):
            wins += 1
    print(f"model beats the best baseline in {wins} of {len(model_rows)} cells")
    if wins <= len(model_rows) / 2:
        print(
            "note: the model does not beat the baselines; it must not be presented as better",
            file=sys.stderr,
        )

    out = args.out or DATA_DIR / "backtest" / "model_vs_baselines.json"
    write_json(
        {
            "horizons": list(horizons),
            "window_days": args.window_days,
            "summary": summarize_model_backtest(rows),
            "rows": rows_to_dicts(rows),
        },
        out,
    )
    print(f"\nwrote {out}")
    return 0


def _site_data(args: argparse.Namespace) -> int:
    from wahlwetter.config import DATA_DIR, REPO_ROOT, TABLES_DIR
    from wahlwetter.polls import load_polls
    from wahlwetter.site.export import build_site_data, write_site_data
    from wahlwetter.storage import read_json, read_parquet

    model_json = args.model_json or DATA_DIR / "model" / "bundestag_trend.json"
    if not model_json.exists():
        print(
            f"error: {model_json} not found. Run `wahlwetter model` first, or "
            "download the artifact produced by model.yml.",
            file=sys.stderr,
        )
        return 2

    def lookup(name: str, key: str, value: str) -> dict[str, str]:
        frame = read_parquet(TABLES_DIR / f"{name}.parquet")
        return frame.set_index(key)[value].to_dict()

    payload = build_site_data(
        read_json(model_json),
        load_polls(),
        party_names=lookup("parties", "party_id", "shortcut"),
        institute_names=lookup("institutes", "institute_id", "name"),
        tasker_names=lookup("taskers", "tasker_id", "name"),
        method_names=lookup("methods", "method_id", "name"),
        recent_polls=args.recent_polls,
        trend_keep_every=args.trend_keep_every,
    )

    out = args.out or REPO_ROOT / "site" / "data"
    written = write_site_data(payload, out)
    total = sum(p.stat().st_size for p in written)
    for path in written:
        print(f"  {path.relative_to(REPO_ROOT)}  {path.stat().st_size / 1024:.0f} KB")
    print(f"wrote {len(written)} files, {total / 1024:.0f} KB total")
    return 0
