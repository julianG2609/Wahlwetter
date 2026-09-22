"""Backtest harness and scoring."""

from __future__ import annotations

from datetime import date

import pytest

from wahlwetter.backtest import (
    best_by_horizon,
    format_table,
    run_backtest,
    score,
    summarize,
)
from wahlwetter.baselines import LastPollPerInstitute, RollingAverage
from wahlwetter.polls import ElectionResult, PollObservation


def poll(survey_id, institute, end, shares, published=None):
    end_date = date.fromisoformat(end)
    return PollObservation(
        survey_id=survey_id,
        institute_id=institute,
        published_at=date.fromisoformat(published) if published else end_date,
        fieldwork_start=end_date,
        fieldwork_end=end_date,
        fieldwork_midpoint=end_date,
        sample_size=1000,
        shares=shares,
    )


ELECTION = ElectionResult(
    election_date=date(2025, 2, 23),
    election_year=2025,
    shares={"0": 10.0, "1": 30.0, "2": 25.0, "8": 35.0},
)


# --- scoring ---------------------------------------------------------------


def test_score_computes_mae_rmse_and_worst_party():
    actual = {"1": 30.0, "2": 25.0}
    estimate = {"1": 32.0, "2": 21.0}
    mae, rmse, worst, worst_party = score(estimate, actual)
    assert mae == pytest.approx(3.0)
    assert rmse == pytest.approx(((2**2 + 4**2) / 2) ** 0.5)
    assert worst == pytest.approx(4.0)
    assert worst_party == "2"


def test_score_treats_a_missing_estimate_as_zero():
    mae, _, _, _ = score({}, {"1": 10.0})
    assert mae == pytest.approx(10.0)


def test_perfect_estimate_scores_zero():
    mae, rmse, worst, _ = score({"1": 30.0}, {"1": 30.0})
    assert (mae, rmse, worst) == (0.0, 0.0, 0.0)


# --- harness ---------------------------------------------------------------


def test_backtest_produces_a_row_per_baseline_and_horizon():
    polls = [poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0})]
    rows = run_backtest(polls, [ELECTION], baselines=[LastPollPerInstitute()], horizons=[1, 7])
    assert len(rows) == 2
    assert {r.horizon_days for r in rows} == {1, 7}
    assert all(r.election_year == 2025 for r in rows)


def test_backtest_only_uses_polls_published_before_the_cutoff():
    """The 30-day-out estimate must not see a poll published two days out."""
    polls = [
        poll("old", "1", "2025-01-01", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0}),
        poll("new", "2", "2025-02-20", {"1": 99.0, "0": 1.0}, published="2025-02-21"),
    ]
    rows = run_backtest(polls, [ELECTION], baselines=[LastPollPerInstitute()], horizons=[30])
    assert rows[0].n_polls_available == 1
    assert rows[0].mae == pytest.approx(0.0)


def test_as_of_is_the_election_date_minus_the_horizon():
    polls = [poll("a", "1", "2025-01-01", {"1": 50.0, "0": 50.0})]
    rows = run_backtest(polls, [ELECTION], baselines=[RollingAverage()], horizons=[7])
    assert rows[0].as_of == "2025-02-16"


def test_uncovered_parties_are_reported():
    """Party 8 is inside Sonstige for every institute, so no baseline can see it."""
    polls = [poll("a", "1", "2025-02-20", {"1": 45.0, "2": 25.0, "0": 30.0})]
    rows = run_backtest(polls, [ELECTION], baselines=[LastPollPerInstitute()], horizons=[1])
    assert rows[0].parties_without_poll_coverage == ["8"]


def test_covered_parties_are_not_reported_as_uncovered():
    polls = [poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0})]
    rows = run_backtest(polls, [ELECTION], baselines=[LastPollPerInstitute()], horizons=[1])
    assert rows[0].parties_without_poll_coverage == []


def test_per_party_errors_cover_every_party():
    polls = [poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0})]
    rows = run_backtest(polls, [ELECTION], baselines=[LastPollPerInstitute()], horizons=[1])
    assert set(rows[0].per_party_abs_error) == set(ELECTION.shares)


# --- reporting -------------------------------------------------------------


def test_summarize_averages_across_elections():
    other = ElectionResult(
        election_date=date(2021, 9, 26), election_year=2021, shares=ELECTION.shares
    )
    polls = [
        poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0}),
        poll("b", "1", "2021-09-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0}),
    ]
    rows = run_backtest(polls, [ELECTION, other], baselines=[LastPollPerInstitute()], horizons=[1])
    summary = summarize(rows)
    assert len(summary) == 1
    assert summary[0]["n_elections"] == 2


def test_best_by_horizon_picks_the_lowest_mae():
    polls = [poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0})]
    rows = run_backtest(
        polls,
        [ELECTION],
        baselines=[LastPollPerInstitute(), RollingAverage(window_days=1)],
        horizons=[1],
    )
    name, value = best_by_horizon(rows)[1]
    assert value == min(r.mae for r in rows)
    assert name in {r.baseline for r in rows}


def test_format_table_lists_every_baseline():
    polls = [poll("a", "1", "2025-02-20", {"1": 30.0, "2": 25.0, "8": 35.0, "0": 10.0})]
    rows = run_backtest(
        polls, [ELECTION], baselines=[LastPollPerInstitute(), RollingAverage()], horizons=[1, 7]
    )
    table = format_table(rows, [1, 7])
    assert "last_poll_per_institute" in table
    assert "rolling_average_14d" in table
