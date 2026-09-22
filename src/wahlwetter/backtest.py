"""Backtesting baselines against past elections.

For each election and each horizon, an estimator sees only the polls that had
been *published* by that cutoff, and its estimate is scored against the
official result.

Only point-forecast metrics are computed here. The brief also asks for a log
score, which needs a predictive distribution; these baselines produce point
estimates only, so a log score would require inventing a spread for them. That
is left for Phase 3, where the model supplies a real posterior.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from wahlwetter.baselines import RESIDUAL_PARTY_ID, Baseline, default_baselines
from wahlwetter.polls import ElectionResult, PollObservation

DEFAULT_HORIZONS = (1, 7, 14, 30, 60, 90)


@dataclass(frozen=True, slots=True)
class BacktestRow:
    baseline: str
    election_year: int
    election_date: str
    horizon_days: int
    as_of: str
    n_polls_available: int
    parties_without_poll_coverage: list[str]
    mae: float
    rmse: float
    max_abs_error: float
    worst_party: str
    per_party_abs_error: dict[str, float]


def score(estimate: dict[str, float], actual: dict[str, float]) -> tuple[float, float, float, str]:
    """MAE, RMSE, largest absolute error and which party it was on."""
    parties = sorted(actual, key=int)
    errors = {p: abs(estimate.get(p, 0.0) - actual[p]) for p in parties}
    n = len(parties)
    mae = sum(errors.values()) / n
    rmse = math.sqrt(sum(e * e for e in errors.values()) / n)
    worst_party = max(errors, key=lambda p: errors[p])
    return mae, rmse, errors[worst_party], worst_party


def run_backtest(
    polls: Sequence[PollObservation],
    elections: Sequence[ElectionResult],
    baselines: Sequence[Baseline] | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[BacktestRow]:
    baselines = baselines or default_baselines()
    rows: list[BacktestRow] = []

    for election in elections:
        parties = election.parties
        for horizon in horizons:
            as_of = election.election_date - timedelta(days=horizon)
            usable = [p for p in polls if p.is_available_on(as_of)]
            # A party no institute breaks out is invisible to every baseline:
            # it sits inside each poll's Sonstige and cannot be recovered. Its
            # error is still scored, but it must be visible why.
            uncovered = sorted(
                (
                    party
                    for party in parties
                    if party != RESIDUAL_PARTY_ID
                    and not any(party in poll.shares for poll in usable)
                ),
                key=int,
            )
            for baseline in baselines:
                estimate = baseline.estimate(polls, as_of, parties)
                mae, rmse, worst, worst_party = score(estimate, election.shares)
                rows.append(
                    BacktestRow(
                        baseline=baseline.name,
                        election_year=election.election_year,
                        election_date=election.election_date.isoformat(),
                        horizon_days=horizon,
                        as_of=as_of.isoformat(),
                        n_polls_available=len(usable),
                        parties_without_poll_coverage=uncovered,
                        mae=round(mae, 4),
                        rmse=round(rmse, 4),
                        max_abs_error=round(worst, 4),
                        worst_party=worst_party,
                        per_party_abs_error={
                            p: round(abs(estimate.get(p, 0.0) - election.shares[p]), 4)
                            for p in parties
                        },
                    )
                )
    return rows


def summarize(rows: Sequence[BacktestRow]) -> list[dict]:
    """Mean MAE per baseline per horizon, across elections."""
    grouped: dict[tuple[str, int], list[float]] = {}
    for row in rows:
        grouped.setdefault((row.baseline, row.horizon_days), []).append(row.mae)
    return [
        {
            "baseline": baseline,
            "horizon_days": horizon,
            "mean_mae": round(sum(values) / len(values), 4),
            "n_elections": len(values),
        }
        for (baseline, horizon), values in sorted(grouped.items())
    ]


def rows_to_dicts(rows: Sequence[BacktestRow]) -> list[dict]:
    return [asdict(r) for r in rows]


def format_table(rows: Sequence[BacktestRow], horizons: Sequence[int]) -> str:
    """Mean MAE by baseline and horizon, as a plain text table."""
    summary = {(s["baseline"], s["horizon_days"]): s["mean_mae"] for s in summarize(rows)}
    names = sorted({r.baseline for r in rows})
    width = max(len(n) for n in names) + 2
    header = "baseline".ljust(width) + "".join(f"{h:>8}d" for h in horizons)
    lines = [header, "-" * len(header)]
    for name in names:
        line = name.ljust(width)
        for horizon in horizons:
            value = summary.get((name, horizon))
            line += f"{value:>9.3f}" if value is not None else " " * 9
        lines.append(line)
    return "\n".join(lines)


def best_by_horizon(rows: Sequence[BacktestRow]) -> dict[int, tuple[str, float]]:
    summary = summarize(rows)
    out: dict[int, tuple[str, float]] = {}
    for entry in summary:
        horizon = entry["horizon_days"]
        current = out.get(horizon)
        if current is None or entry["mean_mae"] < current[1]:
            out[horizon] = (entry["baseline"], entry["mean_mae"])
    return out


def elections_covered(elections: Sequence[ElectionResult]) -> list[date]:
    return [e.election_date for e in elections]
