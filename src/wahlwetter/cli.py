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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

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
