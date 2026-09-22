"""Official election results from Die Bundeswahlleiterin.

`tests/fixtures/bwl_minimal.csv` is SYNTHETIC: it reproduces the real file's
column layout, German number formatting and EN-DASH "no value" marker, but the
numbers are round and invented so the assertions below are exact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from wahlwetter.reference.bundeswahlleiterin import (
    RESULTS_CSV_URL,
    ElectionResultRow,
    fetch_results_csv,
    parse_results_csv,
    rows_to_csv,
    to_rows,
    validate_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bwl_minimal.csv"

MAPPING = {
    "parliament_id": "0",
    "residual_party_id": "0",
    "mapping": {
        "1": {"shortcut": "CDU/CSU", "official_labels": ["CDU", "CSU"]},
        "2": {"shortcut": "SPD", "official_labels": ["SPD"]},
        "4": {"shortcut": "Grüne", "official_labels": ["GRÜNE"]},
        "7": {"shortcut": "AfD", "official_labels": ["AfD"]},
    },
}

STAMP = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def rows() -> list[ElectionResultRow]:
    data = parse_results_csv(FIXTURE.read_bytes())
    return to_rows(data, mapping=MAPPING, years=[2025], retrieved_at=STAMP)


def by_party(rows: list[ElectionResultRow]) -> dict[str, ElectionResultRow]:
    return {r.party_id: r for r in rows}


# --- parsing ---------------------------------------------------------------


def test_header_block_is_skipped():
    data = parse_results_csv(FIXTURE.read_bytes())
    assert [r[0] for r in data][:2] == ["Wahlberechtigte", "gültige Stimmen/Sitze insgesamt"]


def test_missing_header_raises():
    with pytest.raises(ValueError, match="Merkmal/Partei"):
        parse_results_csv(b"nothing;useful\n")


def test_german_number_and_dash_parsing(rows):
    """Thousands separators are dots, and EN DASH means 'no value'."""
    assert by_party(rows)["2"].second_votes == 250_000
    assert by_party(rows)["2"].valid_votes_total == 1_000_000


# --- aggregation -----------------------------------------------------------


def test_cdu_and_csu_are_summed_into_one_dawum_party(rows):
    cdu_csu = by_party(rows)["1"]
    assert cdu_csu.second_votes == 300_000
    assert cdu_csu.share_pct_computed == pytest.approx(30.0)
    assert cdu_csu.official_labels == "CDU+CSU"
    assert cdu_csu.seats_total == 35
    assert cdu_csu.seats_constituency == 30


def test_combined_party_has_no_published_percentage(rows):
    """There is no official CDU/CSU line to compare a combined share against."""
    assert by_party(rows)["1"].share_pct_official is None
    assert by_party(rows)["2"].share_pct_official == 25.0


def test_unmapped_parties_fall_into_the_residual(rows):
    """Whatever institutes would report as 'Sonstige'."""
    sonstige = by_party(rows)["0"]
    assert sonstige.second_votes == 100_000
    assert sonstige.share_pct_computed == pytest.approx(10.0)
    assert sonstige.seats_total == 5


def test_shares_are_recomputed_from_votes_not_summed_from_percentages(rows):
    total = sum(r.share_pct_computed for r in rows)
    assert total == pytest.approx(100.0)


def test_votes_and_seats_reconcile_with_official_totals(rows):
    assert sum(r.second_votes for r in rows) == rows[0].valid_votes_total
    assert sum(r.seats_total for r in rows) == rows[0].seats_total_all_parties


def test_every_row_carries_provenance(rows):
    for r in rows:
        assert r.source_url == RESULTS_CSV_URL
        assert r.election_date_source_url.startswith("https://www.bundeswahlleiterin.de/")
        assert r.license_url == "https://www.govdata.de/dl-de/by-2-0"
        assert r.attribution
        assert r.retrieved_at == STAMP.isoformat()


def test_election_date_is_attached(rows):
    assert {r.election_date for r in rows} == {"2025-02-23"}
    assert {r.bundestag_number for r in rows} == {"21"}


def test_unknown_year_is_refused():
    data = parse_results_csv(FIXTURE.read_bytes())
    with pytest.raises(KeyError, match="1998"):
        to_rows(data, mapping=MAPPING, years=[1998])


def test_year_with_no_rows_is_refused():
    data = parse_results_csv(FIXTURE.read_bytes())
    with pytest.raises(ValueError, match="2017"):
        to_rows(data, mapping=MAPPING, years=[2017])


# --- reconciliation checks -------------------------------------------------


def test_clean_table_reports_no_problems(rows):
    assert validate_rows(rows) == []


def test_check_catches_wrong_share_sum(rows):
    broken = [*rows[:-1]]  # drop the residual
    problems = validate_rows(broken)
    assert any("sum to" in p for p in problems)


def test_check_catches_seat_mismatch(rows):
    import dataclasses

    broken = [dataclasses.replace(rows[0], seats_total=999), *rows[1:]]
    assert any("seats sum to" in p for p in validate_rows(broken))


def test_check_catches_share_diverging_from_published(rows):
    import dataclasses

    broken = [dataclasses.replace(r, share_pct_official=1.0) for r in rows]
    problems = validate_rows(broken)
    assert any("differs from published" in p for p in problems)


def test_check_tolerates_half_a_rounding_step(rows):
    import dataclasses

    spd = next(r for r in rows if r.party_id == "2")
    nudged = dataclasses.replace(spd, share_pct_official=spd.share_pct_computed - 0.05)
    assert not any("differs from published" in p for p in validate_rows([nudged]))


# --- fetching --------------------------------------------------------------


@respx.mock
def test_fetch_uses_identifying_user_agent():
    route = respx.get(RESULTS_CSV_URL).mock(
        return_value=httpx.Response(200, content=FIXTURE.read_bytes())
    )
    content = fetch_results_csv()
    assert route.call_count == 1
    assert "github.com" in route.calls[0].request.headers["User-Agent"]
    assert content.startswith("﻿".encode())


@respx.mock
def test_fetch_raises_on_error():
    respx.get(RESULTS_CSV_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_results_csv()


# --- output ----------------------------------------------------------------


def test_csv_output_roundtrips(rows, tmp_path):
    import csv as _csv

    out = tmp_path / "elections.csv"
    rows_to_csv(rows, out)
    written = list(_csv.DictReader(out.open(encoding="utf-8")))
    assert len(written) == len(rows)
    assert written[0]["source_url"] == RESULTS_CSV_URL
    assert "share_pct_computed" in written[0]
