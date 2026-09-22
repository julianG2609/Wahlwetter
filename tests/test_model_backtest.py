"""Backtest fairness.

The comparison is only worth anything if the model and the baselines are
scored on the same quantity. These tests pin that down without running Stan.
"""

from __future__ import annotations

from datetime import date

import pytest

from wahlwetter.model.backtest import (
    ModelBacktestRow,
    aggregate_actual,
    format_comparison,
    summarize_model_backtest,
)
from wahlwetter.polls import ElectionResult

ELECTION = ElectionResult(
    election_date=date(2025, 2, 23),
    election_year=2025,
    shares={"0": 3.0, "1": 28.5, "2": 16.4, "5": 8.8, "8": 1.5, "23": 5.0},
)


# --- party-set aggregation -------------------------------------------------


def test_unmodelled_parties_are_folded_into_the_reference():
    """Otherwise the model is scored on parties it deliberately does not estimate."""
    actual = aggregate_actual(ELECTION, ["0", "1", "2", "5"])
    assert actual["0"] == pytest.approx(3.0 + 1.5 + 5.0)
    assert actual["1"] == pytest.approx(28.5)
    assert set(actual) == {"0", "1", "2", "5"}


def test_aggregation_preserves_the_total():
    for parties in (["0", "1"], ["0", "1", "2", "5"], ["0", "1", "2", "5", "8", "23"]):
        actual = aggregate_actual(ELECTION, parties)
        assert sum(actual.values()) == pytest.approx(sum(ELECTION.shares.values()))


def test_full_party_set_is_unchanged():
    actual = aggregate_actual(ELECTION, ["0", "1", "2", "5", "8", "23"])
    assert actual == pytest.approx(ELECTION.shares)


def test_party_absent_from_the_result_scores_zero():
    """A party the model estimates but that did not contest gets a real zero."""
    actual = aggregate_actual(ELECTION, ["0", "1", "99"])
    assert actual["99"] == 0.0


# --- summary and formatting ------------------------------------------------


def row(method, year, horizon, mae):
    return ModelBacktestRow(
        method=method,
        election_year=year,
        election_date=f"{year}-01-01",
        horizon_days=horizon,
        as_of=f"{year}-01-01",
        parties=["0", "1"],
        n_polls_used=10,
        mae=mae,
        rmse=mae,
        max_abs_error=mae,
        worst_party="1",
        per_party_abs_error={"0": mae, "1": mae},
    )


def test_summary_averages_over_elections_per_method_and_horizon():
    rows = [
        row("bayesian_trend", 2017, 1, 2.0),
        row("bayesian_trend", 2021, 1, 1.0),
        row("rolling_average_14d", 2017, 1, 3.0),
    ]
    summary = {(s["method"], s["horizon_days"]): s for s in summarize_model_backtest(rows)}
    assert summary[("bayesian_trend", 1)]["mean_mae"] == pytest.approx(1.5)
    assert summary[("bayesian_trend", 1)]["n_elections"] == 2
    assert summary[("rolling_average_14d", 1)]["mean_mae"] == pytest.approx(3.0)


def test_comparison_table_puts_the_model_first():
    rows = [row("rolling_average_14d", 2025, 1, 3.0), row("bayesian_trend", 2025, 1, 1.0)]
    table = format_comparison(rows, [1])
    lines = [line for line in table.splitlines() if line and not line.startswith("-")]
    assert lines[1].startswith("bayesian_trend")


def test_comparison_table_covers_every_horizon():
    rows = [row("bayesian_trend", 2025, h, 1.0) for h in (1, 14, 30)]
    table = format_comparison(rows, [1, 14, 30])
    assert "1d" in table and "14d" in table and "30d" in table


def test_rows_record_whether_the_fit_converged():
    r = ModelBacktestRow(
        method="bayesian_trend",
        election_year=2025,
        election_date="2025-02-23",
        horizon_days=1,
        as_of="2025-02-22",
        parties=["0", "1"],
        n_polls_used=10,
        mae=1.0,
        rmse=1.0,
        max_abs_error=1.0,
        worst_party="1",
        per_party_abs_error={},
        diagnostics_ok=False,
        diagnostic_problems=["max R-hat 1.2 exceeds 1.01"],
    )
    assert r.diagnostics_ok is False
    assert r.diagnostic_problems


def test_baseline_rows_carry_no_diagnostics():
    """Baselines have no sampler, so the field stays None rather than True."""
    assert row("rolling_average_14d", 2025, 1, 1.0).diagnostics_ok is None


# --- posterior scoring -----------------------------------------------------


class PosteriorFit:
    """Supplies a `share` array with a controllable mean and spread."""

    def __init__(self, means_pct, sd_pct, n_days=3, n_draws=4000):
        import numpy as np

        rng = np.random.default_rng(1)
        n_parties = len(means_pct)
        draws = np.empty((n_draws, n_days, n_parties))
        for k, m in enumerate(means_pct):
            draws[:, :, k] = rng.normal(m / 100.0, sd_pct / 100.0, (n_draws, n_days))
        self._share = draws

    def stan_variable(self, name):
        assert name == "share"
        return self._share


def _md(parties):
    from datetime import date as _date

    from wahlwetter.model.data import ModelData

    return ModelData(
        parties=list(parties),
        institutes=["1"],
        methods=["3"],
        start_date=_date(2025, 6, 1),
        end_date=_date(2025, 6, 3),
        polls=[],
        stan_data={},
    )


def test_perfect_posterior_scores_better_than_a_wrong_one():
    from datetime import date as _date

    from wahlwetter.model.backtest import score_posterior

    md = _md(["0", "1"])
    actual = {"0": 40.0, "1": 60.0}
    good = score_posterior(PosteriorFit([40.0, 60.0], 1.0), md, _date(2025, 6, 3), actual)
    bad = score_posterior(PosteriorFit([30.0, 70.0], 1.0), md, _date(2025, 6, 3), actual)
    assert good["mean_log_score"] > bad["mean_log_score"]


def test_overconfident_posterior_is_punished():
    """A narrow posterior centred away from the truth scores far worse."""
    from datetime import date as _date

    from wahlwetter.model.backtest import score_posterior

    md = _md(["0", "1"])
    actual = {"0": 40.0, "1": 60.0}
    wide = score_posterior(PosteriorFit([35.0, 65.0], 5.0), md, _date(2025, 6, 3), actual)
    narrow = score_posterior(PosteriorFit([35.0, 65.0], 0.3), md, _date(2025, 6, 3), actual)
    assert narrow["mean_log_score"] < wide["mean_log_score"]
    assert narrow["mean_abs_z"] > wide["mean_abs_z"]


def test_coverage_is_one_when_the_truth_sits_at_the_centre():
    from datetime import date as _date

    from wahlwetter.model.backtest import score_posterior

    md = _md(["0", "1"])
    result = score_posterior(
        PosteriorFit([40.0, 60.0], 2.0), md, _date(2025, 6, 3), {"0": 40.0, "1": 60.0}
    )
    assert result["coverage_80"] == pytest.approx(1.0)


def test_coverage_is_zero_when_the_truth_is_far_outside():
    from datetime import date as _date

    from wahlwetter.model.backtest import score_posterior

    md = _md(["0", "1"])
    result = score_posterior(
        PosteriorFit([40.0, 60.0], 0.2), md, _date(2025, 6, 3), {"0": 50.0, "1": 50.0}
    )
    assert result["coverage_80"] == pytest.approx(0.0)
    assert result["mean_abs_z"] > 10


def test_baseline_rows_have_no_posterior_scores():
    r = row("rolling_average_14d", 2025, 1, 1.0)
    assert r.mean_log_score is None
    assert r.coverage_80 is None
