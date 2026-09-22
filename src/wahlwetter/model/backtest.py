"""Scoring the trend model against past elections, on the baselines' terms.

The comparison is only meaningful if both are scored on the same quantity. Two
things are held identical between the model and the baselines:

* **Availability.** Both see only polls *published* on or before the cutoff.
* **Party set.** The model pools rarely-reported parties into the reference
  category, so the official result is aggregated the same way and the baselines
  are re-run on that same set. Scoring the model on parties it deliberately
  does not estimate, while the baselines impute them, would rig the comparison.

What is scored is the estimate of opinion *at the cutoff*, against the eventual
result -- exactly what the baselines are scored on. Neither is a forecast: no
attempt is made to model movement between the cutoff and election day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from wahlwetter.backtest import score
from wahlwetter.baselines import Baseline, default_baselines
from wahlwetter.model.data import REFERENCE_PARTY_ID, build_model_data
from wahlwetter.model.fit import SamplingConfig, collect_diagnostics, compile_model, sample
from wahlwetter.polls import ElectionResult, PollObservation

#: Days of polling history fed to each backtest fit. Long enough for the random
#: walk and house effects to be informed, short enough that nine fits finish in
#: a reasonable time.
DEFAULT_WINDOW_DAYS = 365

DEFAULT_BACKTEST_HORIZONS = (1, 14, 30)


@dataclass(frozen=True, slots=True)
class ModelBacktestRow:
    method: str
    election_year: int
    election_date: str
    horizon_days: int
    as_of: str
    parties: list[str]
    n_polls_used: int
    mae: float
    rmse: float
    max_abs_error: float
    worst_party: str
    per_party_abs_error: dict[str, float]
    diagnostics_ok: bool | None = None
    diagnostic_problems: list[str] = field(default_factory=list)


def aggregate_actual(election: ElectionResult, parties: Sequence[str]) -> dict[str, float]:
    """Official result folded onto the modelled party set.

    Parties the model does not estimate are added to the reference category,
    which is exactly where the institutes put them too.
    """
    modelled = set(parties)
    out = {p: 0.0 for p in parties}
    for party, share in election.shares.items():
        if party in modelled:
            out[party] += share
        else:
            out[REFERENCE_PARTY_ID] += share
    return out


def model_estimate_at(fit, model_data, day: date) -> dict[str, float]:
    """Posterior median share per party on one day, in percentage points."""
    import numpy as np

    share = fit.stan_variable("share")
    index = model_data.day_index(day) - 1
    values = share[:, index, :] * 100.0
    return {party: float(np.median(values[:, k])) for k, party in enumerate(model_data.parties)}


def run_model_backtest(
    polls: Sequence[PollObservation],
    elections: Sequence[ElectionResult],
    *,
    horizons: Sequence[int] = DEFAULT_BACKTEST_HORIZONS,
    window_days: int = DEFAULT_WINDOW_DAYS,
    config: SamplingConfig | None = None,
    baselines: Sequence[Baseline] | None = None,
    include_method_effects: bool = False,
    progress=None,
) -> list[ModelBacktestRow]:
    config = config or SamplingConfig()
    baselines = baselines if baselines is not None else default_baselines()
    model = compile_model()
    earliest = min(p.fieldwork_midpoint for p in polls)

    rows: list[ModelBacktestRow] = []
    for election in elections:
        for horizon in horizons:
            as_of = election.election_date - timedelta(days=horizon)
            # Availability is publication, as for the baselines.
            usable = [p for p in polls if p.is_available_on(as_of)]
            if not usable:
                continue
            # Never open a window that reaches back before any data exists:
            # the leading empty stretch would be pure prior.
            start = max(as_of - timedelta(days=window_days), earliest)

            model_data = build_model_data(
                usable,
                start,
                as_of,
                include_method_effects=include_method_effects,
            )
            actual = aggregate_actual(election, model_data.parties)

            if progress:
                progress(
                    f"{election.election_year} h={horizon}d: fitting "
                    f"{model_data.stan_data['n_polls']} polls, "
                    f"{model_data.n_days} days, {model_data.n_parties} parties"
                )

            fit = sample(model_data, config, model=model)
            diagnostics = collect_diagnostics(fit, config)
            estimate = model_estimate_at(fit, model_data, as_of)

            mae, rmse, worst, worst_party = score(estimate, actual)
            rows.append(
                ModelBacktestRow(
                    method="bayesian_trend",
                    election_year=election.election_year,
                    election_date=election.election_date.isoformat(),
                    horizon_days=horizon,
                    as_of=as_of.isoformat(),
                    parties=list(model_data.parties),
                    n_polls_used=model_data.stan_data["n_polls"],
                    mae=round(mae, 4),
                    rmse=round(rmse, 4),
                    max_abs_error=round(worst, 4),
                    worst_party=worst_party,
                    per_party_abs_error={
                        p: round(abs(estimate.get(p, 0.0) - actual[p]), 4)
                        for p in model_data.parties
                    },
                    diagnostics_ok=diagnostics.ok,
                    diagnostic_problems=list(diagnostics.problems),
                )
            )

            # Same cutoff, same party set, same actual -- so the numbers are
            # directly comparable rather than merely adjacent.
            for baseline in baselines:
                baseline_estimate = baseline.estimate(polls, as_of, model_data.parties)
                b_mae, b_rmse, b_worst, b_worst_party = score(baseline_estimate, actual)
                rows.append(
                    ModelBacktestRow(
                        method=baseline.name,
                        election_year=election.election_year,
                        election_date=election.election_date.isoformat(),
                        horizon_days=horizon,
                        as_of=as_of.isoformat(),
                        parties=list(model_data.parties),
                        n_polls_used=len(usable),
                        mae=round(b_mae, 4),
                        rmse=round(b_rmse, 4),
                        max_abs_error=round(b_worst, 4),
                        worst_party=b_worst_party,
                        per_party_abs_error={
                            p: round(abs(baseline_estimate.get(p, 0.0) - actual[p]), 4)
                            for p in model_data.parties
                        },
                    )
                )
    return rows


def summarize_model_backtest(rows: Sequence[ModelBacktestRow]) -> list[dict]:
    grouped: dict[tuple[str, int], list[float]] = {}
    for row in rows:
        grouped.setdefault((row.method, row.horizon_days), []).append(row.mae)
    return [
        {
            "method": method,
            "horizon_days": horizon,
            "mean_mae": round(sum(values) / len(values), 4),
            "n_elections": len(values),
        }
        for (method, horizon), values in sorted(grouped.items())
    ]


def format_comparison(rows: Sequence[ModelBacktestRow], horizons: Sequence[int]) -> str:
    summary = {
        (s["method"], s["horizon_days"]): s["mean_mae"] for s in summarize_model_backtest(rows)
    }
    methods = sorted({r.method for r in rows}, key=lambda m: (m != "bayesian_trend", m))
    width = max(len(m) for m in methods) + 2
    header = "method".ljust(width) + "".join(f"{h:>8}d" for h in horizons)
    lines = [header, "-" * len(header)]
    for method in methods:
        line = method.ljust(width)
        for horizon in horizons:
            value = summary.get((method, horizon))
            line += f"{value:>9.3f}" if value is not None else " " * 9
        lines.append(line)
    return "\n".join(lines)


def rows_to_dicts(rows: Sequence[ModelBacktestRow]) -> list[dict]:
    return [asdict(r) for r in rows]
