"""One deliberately malformed input per validation rule."""

from __future__ import annotations

from datetime import date

import pytest

from wahlwetter.config import ValidationConfig
from wahlwetter.findings import Severity, has_errors
from wahlwetter.validation import (
    Dimensions,
    SurveyCandidate,
    ValidationContext,
    find_duplicates,
    parse_iso_date,
    parse_sample_size,
    rule_dates_parseable,
    rule_fieldwork_before_publication,
    rule_fieldwork_order,
    rule_fieldwork_present,
    rule_fieldwork_span,
    rule_known_parties_for_parliament,
    rule_publication_lag,
    rule_publication_plausible,
    rule_referential_integrity,
    rule_results_present,
    rule_sample_size,
    rule_share_range,
    rule_share_sum,
    validate_survey,
)

DIMS = Dimensions(
    parliaments=frozenset({"0", "3"}),
    institutes=frozenset({"1", "2"}),
    taskers=frozenset({"1", "2"}),
    methods=frozenset({"0", "3"}),
    parties=frozenset({"0", "1", "2", "5"}),
)


def make_ctx(**kwargs) -> ValidationContext:
    return ValidationContext(
        dimensions=kwargs.pop("dimensions", DIMS),
        config=kwargs.pop("config", ValidationConfig()),
        today=kwargs.pop("today", date(2026, 9, 22)),
        parties_by_parliament=kwargs.pop("parties_by_parliament", {}),
    )


def make_candidate(**overrides) -> SurveyCandidate:
    base = {
        "source": "dawum",
        "source_id": "200",
        "parliament_id": "0",
        "institute_id": "1",
        "tasker_id": "1",
        "method_id": "3",
        "published_at_raw": "2026-03-10",
        "fieldwork_start_raw": "2026-03-04",
        "fieldwork_end_raw": "2026-03-08",
        "sample_size_raw": "1500",
        "results": {"1": 30.0, "2": 25.0, "5": 10.0, "0": 35.0},
    }
    base.update(overrides)
    c = SurveyCandidate(**base)
    c.published_at = parse_iso_date(c.published_at_raw)
    c.fieldwork_start = parse_iso_date(c.fieldwork_start_raw)
    c.fieldwork_end = parse_iso_date(c.fieldwork_end_raw)
    c.sample_size = parse_sample_size(c.sample_size_raw)
    return c


def test_clean_candidate_produces_no_findings():
    assert validate_survey(make_candidate(), make_ctx()) == []


# --- parsing helpers -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-03-04", date(2026, 3, 4)), ("", None), ("not-a-date", None), ("2026-13-01", None)],
)
def test_parse_iso_date(raw, expected):
    assert parse_iso_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1500", 1500), (1500, 1500), ("", None), ("0", None), ("abc", None), ("-5", None)],
)
def test_parse_sample_size(raw, expected):
    """A missing sample size must never silently become zero."""
    assert parse_sample_size(raw) == expected


# --- one malformed input per rule -----------------------------------------


def test_rule_dates_parseable_flags_bad_publication_date():
    findings = rule_dates_parseable(make_candidate(published_at_raw="10.03.2026"), make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]
    assert findings[0].rule == "dates_parseable"


def test_rule_dates_parseable_flags_bad_fieldwork_date():
    findings = rule_dates_parseable(make_candidate(fieldwork_end_raw="2026-02-31"), make_ctx())
    assert has_errors(findings)


def test_rule_dates_parseable_ignores_empty_fieldwork():
    """Empty is 'missing', handled by rule_fieldwork_present, not an error here."""
    c = make_candidate(fieldwork_start_raw="", fieldwork_end_raw="")
    assert rule_dates_parseable(c, make_ctx()) == []


def test_rule_fieldwork_present_warns_when_missing():
    c = make_candidate(fieldwork_start_raw="", fieldwork_end_raw="")
    findings = rule_fieldwork_present(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_missing_fieldwork_falls_back_to_publication_date():
    c = make_candidate(fieldwork_start_raw="", fieldwork_end_raw="")
    assert c.used_publication_fallback is True
    assert c.fieldwork_midpoint == date(2026, 3, 10)


def test_rule_fieldwork_order_flags_reversed_period():
    c = make_candidate(fieldwork_start_raw="2026-03-08", fieldwork_end_raw="2026-03-04")
    findings = rule_fieldwork_order(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_fieldwork_before_publication_flags_late_fieldwork():
    c = make_candidate(fieldwork_end_raw="2026-03-11")
    findings = rule_fieldwork_before_publication(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_publication_plausible_flags_future_date():
    c = make_candidate(published_at_raw="2026-12-01", fieldwork_end_raw="2026-11-30")
    findings = rule_publication_plausible(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_publication_plausible_flags_ancient_date():
    c = make_candidate(
        published_at_raw="1970-01-01",
        fieldwork_start_raw="1969-12-01",
        fieldwork_end_raw="1969-12-20",
    )
    findings = rule_publication_plausible(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_fieldwork_span_warns_on_long_period():
    c = make_candidate(fieldwork_start_raw="2025-01-01", fieldwork_end_raw="2026-03-08")
    findings = rule_fieldwork_span(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_rule_publication_lag_warns_on_stale_publication():
    c = make_candidate(fieldwork_start_raw="2024-01-01", fieldwork_end_raw="2024-01-05")
    findings = rule_publication_lag(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_rule_sample_size_errors_on_unparseable_value():
    findings = rule_sample_size(make_candidate(sample_size_raw="viele"), make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_sample_size_warns_on_missing_value():
    findings = rule_sample_size(make_candidate(sample_size_raw=""), make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


@pytest.mark.parametrize("raw", ["3", "500000"])
def test_rule_sample_size_warns_outside_plausible_range(raw):
    findings = rule_sample_size(make_candidate(sample_size_raw=raw), make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_rule_results_present_errors_on_empty_results():
    findings = rule_results_present(make_candidate(results={}), make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_share_range_flags_negative_and_oversized():
    c = make_candidate(results={"1": -1.0, "2": 101.0, "0": 0.0})
    findings = rule_share_range(c, make_ctx())
    assert len(findings) == 2
    assert all(f.severity is Severity.ERROR for f in findings)


def test_rule_share_sum_errors_beyond_tolerance():
    c = make_candidate(results={"1": 30.0, "2": 25.0, "0": 40.0})  # sums to 95
    findings = rule_share_sum(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_share_sum_warns_within_error_band():
    c = make_candidate(results={"1": 30.0, "2": 25.0, "0": 43.0})  # sums to 98
    findings = rule_share_sum(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_rule_share_sum_accepts_exact_hundred():
    assert rule_share_sum(make_candidate(), make_ctx()) == []


def test_share_sum_tolerances_match_documented_observation():
    """+/-2 accepts every sum observed in the real database; +/-1 flags the tail."""
    cfg = ValidationConfig()
    for observed_sum in (98, 99, 100, 101, 102):
        c = make_candidate(results={"1": float(observed_sum)})
        findings = rule_share_sum(c, make_ctx(config=cfg))
        assert not has_errors(findings), observed_sum


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parliament_id", "99"),
        ("institute_id", "99"),
        ("tasker_id", "99"),
        ("method_id", "99"),
    ],
)
def test_rule_referential_integrity_flags_unknown_dimension_id(field, value):
    findings = rule_referential_integrity(make_candidate(**{field: value}), make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]


def test_rule_referential_integrity_flags_unknown_party_id():
    c = make_candidate(results={"1": 50.0, "77": 50.0})
    findings = rule_referential_integrity(c, make_ctx())
    assert [f.severity for f in findings] == [Severity.ERROR]
    assert "77" in findings[0].message


def test_rule_known_parties_warns_but_does_not_quarantine():
    """A new party must not cost us an otherwise valid poll."""
    ctx = make_ctx(parties_by_parliament={"0": {"0", "1", "2"}})
    findings = rule_known_parties_for_parliament(make_candidate(), ctx)
    assert [f.severity for f in findings] == [Severity.WARNING]
    assert findings[0].context["unexpected_party_ids"] == ["5"]
    assert not has_errors(findings)


def test_rule_known_parties_silent_when_config_absent():
    assert rule_known_parties_for_parliament(make_candidate(), make_ctx()) == []


# --- cross-survey rules ----------------------------------------------------


def test_find_duplicates_quarantines_all_but_the_first():
    a = make_candidate(source_id="100")
    b = make_candidate(source_id="200")
    findings = find_duplicates([b, a], make_ctx())
    errors = [f for f in findings if f.severity is Severity.ERROR]
    assert [f.source_id for f in errors] == ["200"]
    assert errors[0].context["duplicate_of"] == "100"


def test_find_duplicates_warns_on_repeated_fieldwork_end():
    a = make_candidate(source_id="100")
    b = make_candidate(source_id="200", results={"1": 40.0, "2": 30.0, "0": 30.0})
    findings = find_duplicates([a, b], make_ctx())
    assert all(f.severity is Severity.WARNING for f in findings)
    assert {f.rule for f in findings} == {"repeated_fieldwork_end"}


def test_find_duplicates_silent_on_distinct_surveys():
    a = make_candidate(source_id="100")
    b = make_candidate(source_id="200", fieldwork_end_raw="2026-03-09")
    assert find_duplicates([a, b], make_ctx()) == []


def test_fieldwork_midpoint_floors_even_spans():
    c = make_candidate(fieldwork_start_raw="2026-03-04", fieldwork_end_raw="2026-03-06")
    assert c.fieldwork_midpoint == date(2026, 3, 5)
    c = make_candidate(fieldwork_start_raw="2026-03-04", fieldwork_end_raw="2026-03-07")
    assert c.fieldwork_midpoint == date(2026, 3, 5)
