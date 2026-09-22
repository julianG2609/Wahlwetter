"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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
