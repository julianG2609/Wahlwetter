"""Normalization into tidy tables."""

from __future__ import annotations

from datetime import date

import pytest

from wahlwetter.findings import Severity
from wahlwetter.normalize import RESULT_COLUMNS, SURVEY_COLUMNS, normalize_dawum
from wahlwetter.sources.base import make_survey_id
from wahlwetter.sources.dawum import DawumPayload, DawumSource


def norm(payload_dict, snapshot, **kwargs):
    return normalize_dawum(DawumPayload.model_validate(payload_dict), snapshot, **kwargs)


def test_tables_have_fixed_columns(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    assert list(tables.surveys.columns) == SURVEY_COLUMNS
    assert list(tables.results.columns) == RESULT_COLUMNS


def test_all_clean_surveys_are_accepted(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    assert len(tables.surveys) == 3
    assert tables.quarantined == []


def test_results_are_long_format(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    assert len(tables.results) == 4 + 3 + 4
    assert set(tables.results["survey_id"]) == set(tables.surveys["survey_id"])


def test_rows_are_sorted_by_numeric_source_id(minimal_payload_dict, minimal_snapshot):
    """dawum's JSON key order is not sorted; ours must be."""
    tables = norm(minimal_payload_dict, minimal_snapshot)
    assert list(tables.surveys["source_id"]) == ["100", "150", "200"]


def test_survey_id_is_deterministic_and_source_scoped():
    a = make_survey_id("dawum", "200")
    assert a == make_survey_id("dawum", "200")
    assert a != make_survey_id("other", "200")
    assert a != make_survey_id("dawum", "201")


def test_survey_id_matches_helper(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    row = tables.surveys.set_index("source_id").loc["200"]
    assert row["survey_id"] == make_survey_id("dawum", "200")


def test_fieldwork_midpoint_is_computed(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    row = tables.surveys.set_index("source_id").loc["200"]
    assert row["fieldwork_start"] == date(2026, 3, 4)
    assert row["fieldwork_end"] == date(2026, 3, 8)
    assert row["fieldwork_midpoint"] == date(2026, 3, 6)
    assert bool(row["used_publication_fallback"]) is False


def test_dimensions_are_present_and_sorted(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    assert set(tables.dimensions) == {
        "parliaments",
        "institutes",
        "taskers",
        "methods",
        "parties",
    }
    assert list(tables.dimensions["parties"]["party_id"]) == ["0", "1", "2", "5"]


def test_retrieved_at_comes_from_snapshot_not_the_clock(minimal_payload_dict, minimal_snapshot):
    tables = norm(minimal_payload_dict, minimal_snapshot)
    stamps = set(tables.surveys["retrieved_at"])
    assert len(stamps) == 1
    assert stamps.pop().to_pydatetime() == minimal_snapshot.retrieved_at


def test_absent_party_is_absent_not_zero(minimal_payload_dict, minimal_snapshot):
    """Survey 100 omits party 5; that must not become a zero row."""
    tables = norm(minimal_payload_dict, minimal_snapshot)
    sid = make_survey_id("dawum", "100")
    parties = set(tables.results.loc[tables.results["survey_id"] == sid, "party_id"])
    assert parties == {"0", "1", "2"}
    assert (tables.results["share"] > 0).all()


# --- quarantine ------------------------------------------------------------


def test_broken_survey_is_quarantined_with_record_preserved(mutate, make_snapshot):
    payload = mutate("200", Date="10.03.2026")
    tables = norm(payload, make_snapshot(payload))
    assert len(tables.surveys) == 2
    assert [q["source_id"] for q in tables.quarantined] == ["200"]
    record = tables.quarantined[0]["record"]
    assert record["Date"] == "10.03.2026"
    assert record["Results"] == {"1": 30.0, "2": 25.0, "5": 10.0, "0": 35.0}


def test_quarantine_lists_every_reason(mutate, make_snapshot):
    payload = mutate("200", Date="10.03.2026", Surveyed_Persons="viele")
    tables = norm(payload, make_snapshot(payload))
    rules = {r["rule"] for r in tables.quarantined[0]["reasons"]}
    assert rules == {"dates_parseable", "sample_size"}


def test_warnings_are_recorded_on_the_row(mutate, make_snapshot):
    payload = mutate("200", Surveyed_Persons="")
    tables = norm(payload, make_snapshot(payload))
    row = tables.surveys.set_index("source_id").loc["200"]
    assert row["warnings"] == "sample_size"
    assert row["sample_size"] is None or str(row["sample_size"]) == "<NA>"


def test_warning_does_not_quarantine(mutate, make_snapshot):
    payload = mutate("200", Surveyed_Persons="")
    tables = norm(payload, make_snapshot(payload))
    assert len(tables.surveys) == 3
    assert tables.quarantined == []


def test_today_defaults_to_snapshot_date(mutate, make_snapshot):
    """Re-normalizing an old snapshot must give the same answer forever."""
    payload = mutate(
        "200",
        Date="2026-09-25",
        Survey_Period={"Date_Start": "2026-09-20", "Date_End": "2026-09-24"},
    )
    tables = norm(payload, make_snapshot(payload))
    # Snapshot was retrieved 2026-09-22, so a 2026-09-25 publication is future.
    assert [q["source_id"] for q in tables.quarantined] == ["200"]


def test_parties_config_produces_warning_not_loss(minimal_payload_dict, minimal_snapshot):
    tables = norm(
        minimal_payload_dict,
        minimal_snapshot,
        parties_by_parliament={"0": {"0", "1", "2"}, "3": {"0", "1", "2", "5"}},
    )
    assert len(tables.surveys) == 3
    warned = tables.surveys.set_index("source_id").loc["200", "warnings"]
    assert warned == "known_parties_for_parliament"


def test_findings_are_returned_for_reporting(mutate, make_snapshot):
    payload = mutate("200", Date="10.03.2026")
    tables = norm(payload, make_snapshot(payload))
    assert any(f.severity is Severity.ERROR for f in tables.findings)
    assert all(f.source == "dawum" for f in tables.findings)


def test_empty_survey_set_still_yields_typed_tables(minimal_payload_dict, make_snapshot):
    minimal_payload_dict["Surveys"] = {}
    snapshot = make_snapshot(minimal_payload_dict)
    tables = norm(minimal_payload_dict, snapshot)
    assert list(tables.surveys.columns) == SURVEY_COLUMNS
    assert tables.surveys.empty
    assert str(tables.surveys["sample_size"].dtype) == "Int64"


def test_source_normalize_delegates(minimal_snapshot):
    tables = DawumSource(parties_by_parliament={}).normalize(minimal_snapshot)
    assert len(tables.surveys) == 3


@pytest.mark.parametrize("bad_id", ["99", "abc"])
def test_unknown_parliament_is_quarantined(mutate, make_snapshot, bad_id):
    payload = mutate("200", Parliament_ID=bad_id)
    tables = norm(payload, make_snapshot(payload))
    assert [q["source_id"] for q in tables.quarantined] == ["200"]
