"""Stan input assembly. No Stan involved -- this is the part bugs hide in."""

from __future__ import annotations

import math
from datetime import date

import pytest

from wahlwetter.model.data import (
    REFERENCE_PARTY_ID,
    build_model_data,
    detect_rounding_grid,
    poll_groups,
    select_parties,
)
from wahlwetter.polls import PollObservation


def poll(
    survey_id="a",
    institute="1",
    method="3",
    mid="2025-06-01",
    shares=None,
    sample_size=1000,
):
    day = date.fromisoformat(mid)
    return PollObservation(
        survey_id=survey_id,
        institute_id=institute,
        published_at=day,
        fieldwork_start=day,
        fieldwork_end=day,
        fieldwork_midpoint=day,
        sample_size=sample_size,
        shares=shares if shares is not None else {"1": 30.0, "2": 25.0, "0": 45.0},
        method_id=method,
    )


# --- rounding grid ---------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([30.0, 25.0, 45.0], 1.0),
        ([30.5, 25.0, 44.5], 0.5),
        ([30.1, 25.0, 44.9], 0.1),
        ([29.7, 25.4, 44.9], 0.1),
    ],
)
def test_detect_rounding_grid(values, expected):
    assert detect_rounding_grid(values) == expected


def test_rounding_sd_is_uniform_error_of_the_grid():
    """A value rounded to grid d carries uniform error with variance d^2/12."""
    md = build_model_data(
        [poll(shares={"1": 30.0, "2": 25.0, "0": 45.0})],
        date(2025, 6, 1),
        date(2025, 6, 1),
        min_party_coverage=0.0,
    )
    expected = (1.0 / 100.0) / math.sqrt(12.0)
    assert md.stan_data["poll_rounding_sd"][0] == pytest.approx(expected)


def test_finer_grid_gives_smaller_rounding_error():
    coarse = build_model_data(
        [poll(shares={"1": 30.0, "2": 25.0, "0": 45.0})],
        date(2025, 6, 1),
        date(2025, 6, 1),
        min_party_coverage=0.0,
    )
    fine = build_model_data(
        [poll(shares={"1": 30.1, "2": 25.0, "0": 44.9})],
        date(2025, 6, 1),
        date(2025, 6, 1),
        min_party_coverage=0.0,
    )
    assert fine.stan_data["poll_rounding_sd"][0] < coarse.stan_data["poll_rounding_sd"][0]


# --- party selection -------------------------------------------------------


def test_reference_party_is_always_modelled():
    assert select_parties([]) == [REFERENCE_PARTY_ID]


def test_rarely_reported_party_is_pooled_into_the_reference():
    """A party broken out by almost nobody would have a prior-driven path."""
    polls = [poll(survey_id=str(i), shares={"1": 30.0, "2": 25.0, "0": 45.0}) for i in range(9)]
    polls.append(poll(survey_id="rare", shares={"1": 30.0, "2": 25.0, "8": 2.0, "0": 43.0}))
    assert "8" not in select_parties(polls, min_coverage=0.5)


def test_widely_reported_party_is_modelled():
    polls = [
        poll(survey_id=str(i), shares={"1": 30.0, "2": 25.0, "8": 2.0, "0": 43.0})
        for i in range(10)
    ]
    assert "8" in select_parties(polls, min_coverage=0.5)


def test_parties_are_ordered_with_reference_first():
    polls = [poll(shares={"7": 20.0, "1": 30.0, "0": 50.0})]
    assert select_parties(polls, min_coverage=0.0)[0] == REFERENCE_PARTY_ID


# --- grouping --------------------------------------------------------------


def test_reported_party_is_its_own_group():
    parties = ["0", "1", "2"]
    groups = poll_groups(poll(shares={"1": 30.0, "2": 25.0, "0": 45.0}), parties)
    singles = {tuple(m): v for m, v in groups}
    assert singles[(1,)] == pytest.approx(0.30)
    assert singles[(2,)] == pytest.approx(0.25)


def test_unreported_party_joins_the_reference_group():
    """It was folded into Sonstige, so the model compares the SUM."""
    parties = ["0", "1", "2", "3"]
    groups = poll_groups(poll(shares={"1": 30.0, "2": 25.0, "0": 45.0}), parties)
    reference = next(g for g in groups if 0 in g[0])
    assert set(reference[0]) == {0, 3}
    assert reference[1] == pytest.approx(0.45)


def test_party_reported_but_not_modelled_lands_in_the_reference_value():
    parties = ["0", "1", "2"]
    groups = poll_groups(poll(shares={"1": 30.0, "2": 25.0, "8": 2.0, "0": 43.0}), parties)
    reference = next(g for g in groups if 0 in g[0])
    assert reference[1] == pytest.approx(0.45)


def test_group_values_sum_to_one():
    parties = ["0", "1", "2", "3"]
    groups = poll_groups(poll(shares={"1": 30.0, "2": 25.0, "0": 45.0}), parties)
    assert sum(v for _, v in groups) == pytest.approx(1.0)


def test_every_modelled_party_appears_in_exactly_one_group():
    parties = ["0", "1", "2", "3", "4"]
    groups = poll_groups(poll(shares={"1": 30.0, "4": 10.0, "0": 60.0}), parties)
    members = [m for group, _ in groups for m in group]
    assert sorted(members) == list(range(len(parties)))


# --- assembly --------------------------------------------------------------


def test_stan_data_shapes_are_consistent():
    polls = [
        poll(survey_id="a", institute="1", mid="2025-06-01"),
        poll(survey_id="b", institute="2", mid="2025-06-10"),
    ]
    md = build_model_data(polls, date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0)
    sd = md.stan_data
    assert sd["n_polls"] == 2
    assert sd["n_days"] == 30
    assert len(sd["poll_day"]) == sd["n_polls"]
    assert len(sd["obs_value"]) == sd["n_obs"]
    assert len(sd["obs_mask"]) == sd["n_obs"]
    assert all(len(mask) == sd["n_parties"] for mask in sd["obs_mask"])
    assert all(1 <= d <= sd["n_days"] for d in sd["poll_day"])
    assert all(1 <= i <= sd["n_polls"] for i in sd["obs_poll"])


def test_day_index_is_one_based_from_the_window_start():
    md = build_model_data(
        [poll(mid="2025-06-10")], date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0
    )
    assert md.day_index(date(2025, 6, 1)) == 1
    assert md.day_index(date(2025, 6, 10)) == 10
    assert md.stan_data["poll_day"] == [10]


def test_date_for_index_round_trips():
    md = build_model_data(
        [poll(mid="2025-06-10")], date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0
    )
    for day in (date(2025, 6, 1), date(2025, 6, 15), date(2025, 6, 30)):
        assert md.date_for_index(md.day_index(day)) == day


def test_polls_outside_the_window_are_excluded():
    polls = [poll(survey_id="in", mid="2025-06-10"), poll(survey_id="out", mid="2025-01-01")]
    md = build_model_data(polls, date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0)
    assert md.stan_data["n_polls"] == 1
    assert [p.survey_id for p in md.polls] == ["in"]


def test_empty_window_is_refused():
    with pytest.raises(ValueError, match="no polls"):
        build_model_data([poll(mid="2025-01-01")], date(2025, 6, 1), date(2025, 6, 30))


def test_institute_and_method_indices_are_one_based():
    polls = [
        poll(survey_id="a", institute="5", method="1"),
        poll(survey_id="b", institute="2", method="3"),
    ]
    md = build_model_data(polls, date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0)
    sd = md.stan_data
    assert sorted(sd["poll_institute"]) == [1, 2]
    assert sorted(sd["poll_method"]) == [1, 2]
    assert md.institutes == ["2", "5"]


def test_method_effects_are_off_by_default():
    """9 of 11 institutes use a single method; see docs/model.md."""
    md = build_model_data([poll()], date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0)
    assert md.stan_data["include_method_effects"] == 0


def test_method_effects_can_be_enabled():
    md = build_model_data(
        [poll()],
        date(2025, 6, 1),
        date(2025, 6, 30),
        min_party_coverage=0.0,
        include_method_effects=True,
    )
    assert md.stan_data["include_method_effects"] == 1


def test_design_effect_shrinks_effective_sample_size():
    md = build_model_data(
        [poll(sample_size=2000)],
        date(2025, 6, 1),
        date(2025, 6, 30),
        min_party_coverage=0.0,
        design_effect=2.0,
    )
    assert md.stan_data["poll_n_eff"] == [1000.0]


def test_observed_values_are_proportions_not_percentages():
    md = build_model_data([poll()], date(2025, 6, 1), date(2025, 6, 30), min_party_coverage=0.0)
    assert all(0.0 <= v <= 1.0 for v in md.stan_data["obs_value"])
    assert sum(md.stan_data["obs_value"]) == pytest.approx(1.0)
