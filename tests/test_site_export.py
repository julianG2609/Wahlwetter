"""Precomputed site data.

Covers the guard that failed open in the first version: house effects fitted to
too few polls were published because a missing flag defaulted to "reportable".
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from wahlwetter.polls import PollObservation
from wahlwetter.site.export import (
    ATTRIBUTION,
    build_site_data,
    latest_estimates,
    poll_deviation,
    poll_sd_pp,
    thinned_trend,
    trend_on,
    write_site_data,
)


def poll(shares=None, sample_size=1000, mid="2026-09-16"):
    day = date.fromisoformat(mid)
    return PollObservation(
        survey_id="s1",
        institute_id="5",
        published_at=day,
        fieldwork_start=day,
        fieldwork_end=day,
        fieldwork_midpoint=day,
        sample_size=sample_size,
        shares=shares if shares is not None else {"1": 20.0, "7": 28.0, "0": 52.0},
        method_id="3",
        tasker_id="3",
    )


def series(party, shortcut, dates, median):
    return {
        "party_id": party,
        "party_shortcut": shortcut,
        "dates": dates,
        "median": median,
        "q2_5": [m - 1.0 for m in median],
        "q10": [m - 0.5 for m in median],
        "q25": [m - 0.25 for m in median],
        "q75": [m + 0.25 for m in median],
        "q90": [m + 0.5 for m in median],
        "q97_5": [m + 1.0 for m in median],
    }


DATES = ["2026-09-14", "2026-09-15", "2026-09-16"]


def model_output(house_rows):
    return {
        "generated_at": "2026-09-22T12:00:00+00:00",
        "window": {
            "start_date": "2025-02-23",
            "end_date": "2026-09-16",
            "n_polls": 382,
            "n_institutes": 11,
        },
        "diagnostics": {"ok": True, "problems": []},
        "posterior_predictive_check": {"outside_95_fraction": 0.035},
        "parameters": {"design_effect": {"median": 0.136}},
        "trend": {
            "start_date": DATES[0],
            "end_date": DATES[-1],
            "n_days": len(DATES),
            "parties": ["0", "1", "7"],
            "series": [
                series("0", "Sonstige", DATES, [5.4, 5.4, 5.4]),
                series("1", "CDU/CSU", DATES, [19.8, 19.9, 20.0]),
                series("7", "AfD", DATES, [27.8, 27.9, 28.0]),
            ],
        },
        "house_effects": house_rows,
    }


def house(institute="5", party="7", n_polls=161, reportable=True):
    return {
        "institute_id": institute,
        "institute_name": "INSA",
        "party_id": party,
        "party_shortcut": "AfD",
        "median_log_ratio": 0.1,
        "q10_log_ratio": 0.05,
        "q90_log_ratio": 0.15,
        "approx_effect_pp": 2.0,
        "excludes_zero_80": True,
        "n_polls": n_polls,
        "reportable": reportable,
    }


NAMES = {
    "party_names": {"0": "Sonstige", "1": "CDU/CSU", "7": "AfD"},
    "institute_names": {"5": "INSA"},
    "tasker_names": {"3": "BILD am Sonntag"},
    "method_names": {"3": "Online"},
}


# --- the guard that failed open -------------------------------------------


def test_missing_reportable_flag_is_refused():
    """Defaulting it to True published exactly what the flag suppresses."""
    stale = house()
    del stale["reportable"]
    with pytest.raises(ValueError, match="reportable"):
        build_site_data(model_output([stale]), [poll()], **NAMES)


def test_error_names_the_remedy():
    stale = house()
    del stale["reportable"]
    with pytest.raises(ValueError, match="re-run `wahlwetter model`"):
        build_site_data(model_output([stale]), [poll()], **NAMES)


def test_non_reportable_house_effects_are_separated_out():
    rows = [house(), house(institute="24", n_polls=1, reportable=False)]
    data = build_site_data(model_output(rows), [poll()], **NAMES)
    assert [h["institute_id"] for h in data["house_effects"]] == ["5"]
    assert [h["institute_id"] for h in data["house_effects_withheld"]] == ["24"]


# --- poll uncertainty ------------------------------------------------------


def test_sd_uses_the_estimated_design_effect():
    """A design effect below 1 means published values vary less than binomial."""
    plain = poll_sd_pp(28.0, 1000, 1.0)
    shrunk = poll_sd_pp(28.0, 1000, 0.136)
    assert shrunk < plain
    assert shrunk == pytest.approx(plain * 0.136**0.5)


def test_sd_grows_as_sample_size_shrinks():
    assert poll_sd_pp(28.0, 500, 1.0) > poll_sd_pp(28.0, 4000, 1.0)


def test_sd_is_nan_without_a_sample_size():
    import math

    assert math.isnan(poll_sd_pp(28.0, None, 1.0))


def test_deviation_expresses_the_gap_in_standard_deviations():
    trend = {"1": 20.0, "7": 28.0, "0": 52.0}
    rows = {r["party_id"]: r for r in poll_deviation(poll(), trend, 0.136)}
    assert rows["7"]["difference_pp"] == pytest.approx(0.0)
    assert rows["7"]["z"] == pytest.approx(0.0)
    assert rows["7"]["notable"] is False


def test_large_gap_is_flagged_notable():
    trend = {"1": 20.0, "7": 24.0, "0": 56.0}
    rows = {r["party_id"]: r for r in poll_deviation(poll(), trend, 0.136)}
    assert rows["7"]["difference_pp"] == pytest.approx(4.0)
    assert rows["7"]["z"] > 2
    assert rows["7"]["notable"] is True


def test_same_gap_matters_more_for_a_small_party():
    """Two points is routine at 28% and enormous at 3%."""
    big = poll_sd_pp(28.0, 1500, 0.136)
    small = poll_sd_pp(3.0, 1500, 0.136)
    assert 2.0 / small > 2.0 / big


def test_deviation_skips_parties_the_model_does_not_estimate():
    rows = poll_deviation(poll(), {"1": 20.0}, 0.136)
    assert [r["party_id"] for r in rows] == ["1"]


# --- trend helpers ---------------------------------------------------------


def test_trend_on_returns_the_right_day():
    out = model_output([house()])
    assert trend_on(out, "2026-09-14")["7"] == pytest.approx(27.8)
    assert trend_on(out, "2026-09-16")["7"] == pytest.approx(28.0)


def test_trend_on_unknown_day_is_empty():
    assert trend_on(model_output([house()]), "2020-01-01") == {}


def test_latest_estimates_are_sorted_largest_first():
    rows = latest_estimates(model_output([house()]))
    assert [r["party_shortcut"] for r in rows] == ["AfD", "CDU/CSU", "Sonstige"]
    assert rows[0]["median"] == pytest.approx(28.0)


def test_latest_estimates_carry_intervals():
    row = latest_estimates(model_output([house()]))[0]
    assert row["q10"] < row["median"] < row["q90"]
    assert row["q2_5"] < row["q10"] and row["q90"] < row["q97_5"]


def test_thinning_keeps_the_final_point():
    """The last day is the current estimate; dropping it would be wrong."""
    thinned = thinned_trend(model_output([house()]), keep_every=2)
    for s in thinned["series"]:
        assert s["dates"][-1] == DATES[-1]
        assert len(s["median"]) == len(s["dates"])


def test_thinning_reduces_length():
    full = thinned_trend(model_output([house()]), keep_every=1)
    thin = thinned_trend(model_output([house()]), keep_every=3)
    assert len(thin["series"][0]["dates"]) < len(full["series"][0]["dates"])


# --- assembly and output ---------------------------------------------------


def test_polls_are_listed_newest_first_with_client_and_method():
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    row = data["polls"][0]
    assert row["institute"] == "INSA"
    assert row["tasker"] == "BILD am Sonntag"
    assert row["method"] == "Online"


def test_newest_poll_block_is_built():
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    assert data["newest_poll"]["institute"] == "INSA"
    assert data["newest_poll"]["parties"]


def test_attribution_travels_with_the_data():
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    assert data["attribution"] == ATTRIBUTION
    assert "dawum.de" in ATTRIBUTION["polls"]["text"]
    assert "Bundeswahlleiterin" in ATTRIBUTION["election_results"]["text"]


def test_output_is_split_per_page(tmp_path):
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    written = write_site_data(data, tmp_path)
    names = {p.name for p in written}
    assert names == {"overview.json", "trend.json", "pollsters.json", "diagnostics.json"}


def test_overview_does_not_carry_the_whole_trend(tmp_path):
    """No page should download data it does not use."""
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    write_site_data(data, tmp_path)
    overview = json.loads((tmp_path / "overview.json").read_text(encoding="utf-8"))
    assert "trend" not in overview
    assert "latest" in overview


def test_every_page_file_carries_attribution(tmp_path):
    data = build_site_data(model_output([house()]), [poll()], **NAMES)
    for path in write_site_data(data, tmp_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["attribution"]["polls"]["url"] == "https://dawum.de"
