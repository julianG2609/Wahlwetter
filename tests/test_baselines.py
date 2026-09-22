"""Baseline estimators. All inputs here are synthetic and hand-computable."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from wahlwetter.baselines import (
    DawumStyleTrend,
    LastPollPerInstitute,
    RollingAverage,
    available,
    complete_vector,
    newest_per_institute,
    normalize,
    residual_ratios,
)
from wahlwetter.polls import PollObservation

PARTIES = ["0", "1", "2", "8"]


def poll(
    survey_id: str,
    institute: str,
    end: str,
    shares: dict[str, float],
    *,
    published: str | None = None,
    sample_size: int | None = 1000,
) -> PollObservation:
    end_date = date.fromisoformat(end)
    pub = date.fromisoformat(published) if published else end_date
    return PollObservation(
        survey_id=survey_id,
        institute_id=institute,
        published_at=pub,
        fieldwork_start=end_date,
        fieldwork_end=end_date,
        fieldwork_midpoint=end_date,
        sample_size=sample_size,
        shares=shares,
    )


# --- availability ----------------------------------------------------------


def test_availability_uses_publication_not_fieldwork():
    """Otherwise a backtest leaks polls that had not been published yet."""
    p = poll("a", "1", "2025-01-10", {"1": 50, "2": 50}, published="2025-01-20")
    assert p.is_available_on(date(2025, 1, 15)) is False
    assert p.is_available_on(date(2025, 1, 20)) is True
    assert available([p], date(2025, 1, 15)) == []


# --- selection -------------------------------------------------------------


def test_newest_per_institute_keeps_one_each():
    polls = [
        poll("a", "1", "2025-01-01", {"1": 50, "2": 50}),
        poll("b", "1", "2025-01-10", {"1": 60, "2": 40}),
        poll("c", "2", "2025-01-05", {"1": 40, "2": 60}),
    ]
    picked = newest_per_institute(polls)
    assert {p.survey_id for p in picked} == {"b", "c"}


def test_newest_per_institute_breaks_ties_deterministically():
    polls = [
        poll("b", "1", "2025-01-10", {"1": 50, "2": 50}),
        poll("a", "1", "2025-01-10", {"1": 60, "2": 40}),
    ]
    assert [p.survey_id for p in newest_per_institute(polls)] == ["b"]
    assert [p.survey_id for p in newest_per_institute(polls[::-1])] == ["b"]


# --- missing-party imputation ---------------------------------------------


def test_residual_ratio_is_share_of_the_other_bucket():
    polls = [poll("a", "1", "2025-01-01", {"1": 50, "8": 2.0, "0": 8.0})]
    ratios = residual_ratios(polls, PARTIES)
    assert ratios["8"] == pytest.approx(2.0 / 10.0)


def test_missing_party_is_taken_out_of_sonstige():
    ratios = {"8": 0.2}
    p = poll("a", "1", "2025-01-01", {"1": 45.0, "2": 45.0, "0": 10.0})
    vector = complete_vector(p, PARTIES, ratios)
    assert vector["8"] == pytest.approx(2.0)
    assert vector["0"] == pytest.approx(8.0)
    assert sum(vector.values()) == pytest.approx(100.0)


def test_missing_party_is_not_treated_as_zero():
    """Zero would bias small parties downwards; it is folded into Sonstige."""
    ratios = {"8": 0.25}
    p = poll("a", "1", "2025-01-01", {"1": 50.0, "2": 42.0, "0": 8.0})
    assert complete_vector(p, PARTIES, ratios)["8"] > 0


def test_imputation_never_drives_sonstige_negative():
    ratios = {"1": 0.9, "2": 0.9, "8": 0.9}
    p = poll("a", "1", "2025-01-01", {"0": 4.0})
    vector = complete_vector(p, PARTIES, ratios)
    assert vector["0"] == pytest.approx(0.0)
    assert all(v >= 0 for v in vector.values())


def test_party_nobody_reports_stays_at_zero():
    """With no institute breaking it out, there is nothing to infer from."""
    p = poll("a", "1", "2025-01-01", {"1": 50.0, "2": 40.0, "0": 10.0})
    vector = complete_vector(p, PARTIES, residual_ratios([p], PARTIES))
    assert vector["8"] == 0.0


def test_normalize_scales_to_one_hundred():
    assert sum(normalize({"1": 1.0, "2": 1.0}).values()) == pytest.approx(100.0)


def test_normalize_handles_all_zero():
    assert normalize({"1": 0.0}) == {"1": 0.0}


# --- estimators ------------------------------------------------------------


def test_last_poll_per_institute_averages_unweighted():
    polls = [
        poll("a", "1", "2025-01-10", {"1": 60.0, "2": 40.0}),
        poll("b", "2", "2025-01-10", {"1": 40.0, "2": 60.0}),
        poll("c", "1", "2025-01-01", {"1": 100.0, "2": 0.0}),  # superseded
    ]
    est = LastPollPerInstitute().estimate(polls, date(2025, 1, 15), ["1", "2"])
    assert est["1"] == pytest.approx(50.0)
    assert est["2"] == pytest.approx(50.0)


def test_rolling_average_respects_its_window():
    polls = [
        poll("a", "1", "2025-01-14", {"1": 60.0, "2": 40.0}),
        poll("b", "2", "2025-01-01", {"1": 0.0, "2": 100.0}),  # outside 7d
    ]
    est = RollingAverage(window_days=7).estimate(polls, date(2025, 1, 15), ["1", "2"])
    assert est["1"] == pytest.approx(60.0)


def test_rolling_average_counts_prolific_institutes_repeatedly():
    polls = [
        poll("a", "1", "2025-01-14", {"1": 60.0, "2": 40.0}),
        poll("b", "1", "2025-01-13", {"1": 60.0, "2": 40.0}),
        poll("c", "2", "2025-01-13", {"1": 30.0, "2": 70.0}),
    ]
    est = RollingAverage(window_days=7).estimate(polls, date(2025, 1, 15), ["1", "2"])
    assert est["1"] == pytest.approx(50.0)


def test_sample_size_weighting_shifts_the_estimate():
    polls = [
        poll("a", "1", "2025-01-14", {"1": 60.0, "2": 40.0}, sample_size=3000),
        poll("b", "2", "2025-01-14", {"1": 40.0, "2": 60.0}, sample_size=1000),
    ]
    plain = RollingAverage(window_days=7).estimate(polls, date(2025, 1, 15), ["1", "2"])
    weighted = RollingAverage(window_days=7, weight_by_sample_size=True).estimate(
        polls, date(2025, 1, 15), ["1", "2"]
    )
    assert plain["1"] == pytest.approx(50.0)
    assert weighted["1"] == pytest.approx(55.0)


def test_empty_input_returns_zeros():
    for baseline in (LastPollPerInstitute(), RollingAverage(), DawumStyleTrend()):
        assert baseline.estimate([], date(2025, 1, 15), ["1", "2"]) == {"1": 0.0, "2": 0.0}


# --- dawum-style -----------------------------------------------------------


def test_dawum_weight_endpoints_match_the_documented_values():
    """dawum documents full weight at the newest poll and 1/3 at the limit."""
    trend = DawumStyleTrend()
    latest = date(2025, 1, 20)
    assert trend.weight_for(latest, latest) == pytest.approx(1.0)
    assert trend.weight_for(latest, latest - timedelta(days=20)) == pytest.approx(1 / 3)


def test_dawum_weight_interpolates_linearly_between_them():
    """The shape is our assumption; dawum documents only the endpoints."""
    trend = DawumStyleTrend()
    latest = date(2025, 1, 20)
    mid = trend.weight_for(latest, date(2025, 1, 10))
    assert mid == pytest.approx((1.0 + 1 / 3) / 2)


def test_dawum_weight_is_floored_beyond_the_window():
    trend = DawumStyleTrend()
    assert trend.weight_for(date(2025, 1, 20), date(2024, 1, 1)) == pytest.approx(1 / 3)


def test_dawum_excludes_polls_older_than_the_window():
    polls = [
        poll("a", "1", "2025-01-20", {"1": 60.0, "2": 40.0}),
        poll("b", "2", "2024-12-01", {"1": 0.0, "2": 100.0}),
    ]
    est = DawumStyleTrend().estimate(polls, date(2025, 1, 21), ["1", "2"])
    assert est["1"] == pytest.approx(60.0)


def test_dawum_window_is_relative_to_the_newest_poll_not_to_as_of():
    """A stale set of polls is still usable if they are close to each other."""
    polls = [
        poll("a", "1", "2024-06-10", {"1": 60.0, "2": 40.0}),
        poll("b", "2", "2024-06-05", {"1": 40.0, "2": 60.0}),
    ]
    est = DawumStyleTrend().estimate(polls, date(2025, 1, 21), ["1", "2"])
    assert est["1"] > 0


def test_dawum_recent_poll_outweighs_older_one():
    polls = [
        poll("a", "1", "2025-01-20", {"1": 60.0, "2": 40.0}),
        poll("b", "2", "2025-01-05", {"1": 40.0, "2": 60.0}),
    ]
    est = DawumStyleTrend().estimate(polls, date(2025, 1, 21), ["1", "2"])
    assert 50.0 < est["1"] < 60.0


def test_all_estimators_return_shares_summing_to_one_hundred():
    polls = [
        poll("a", "1", "2025-01-20", {"1": 30.0, "2": 25.0, "8": 2.0, "0": 43.0}),
        poll("b", "2", "2025-01-18", {"1": 32.0, "2": 24.0, "0": 44.0}),
    ]
    for baseline in (LastPollPerInstitute(), RollingAverage(), DawumStyleTrend()):
        est = baseline.estimate(polls, date(2025, 1, 21), PARTIES)
        assert sum(est.values()) == pytest.approx(100.0), baseline.name
