"""End-to-end ingestion, idempotency and state handling."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest
import respx

from wahlwetter.ingest import (
    IngestResult,
    load_state,
    run_ingest,
    save_state,
    write_tables,
)
from wahlwetter.sources.base import RawSnapshot, SourceState
from wahlwetter.sources.dawum import API_URL, LAST_UPDATE_URL, DawumSource
from wahlwetter.storage import read_json, read_parquet

TOKEN = "2026-09-21T07:46:18+02:00"


def make_source() -> DawumSource:
    """A source with no reviewed-party config, so tests stay self-contained."""
    return DawumSource(parties_by_parliament={})


@pytest.fixture
def dirs(tmp_path):
    return {
        "tables_dir": tmp_path / "tables",
        "state_dir": tmp_path / "state",
        "quarantine_dir": tmp_path / "quarantine",
        "raw_dir": tmp_path / "raw",
    }


@pytest.fixture
def mock_api(minimal_payload_dict):
    def _mock(token=TOKEN, payload=None):
        body = json.dumps(payload or minimal_payload_dict)
        respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=token))
        respx.get(API_URL).mock(
            return_value=httpx.Response(200, text=body, headers={"ETag": '"v1"'})
        )

    return _mock


# --- state -----------------------------------------------------------------


def test_state_roundtrip(tmp_path):
    state = SourceState(last_update=TOKEN, etag='"v1"', content_sha256="abc")
    save_state("dawum", state, tmp_path)
    assert load_state("dawum", tmp_path) == state


def test_missing_state_is_empty(tmp_path):
    assert load_state("dawum", tmp_path) == SourceState()


# --- full runs -------------------------------------------------------------


@respx.mock
def test_first_run_writes_everything(dirs, mock_api):
    mock_api()
    result = run_ingest(make_source(), **dirs)

    assert result.fetched is True
    assert result.n_surveys == 3
    assert result.n_quarantined == 0

    for name in (
        "surveys",
        "results",
        "parliaments",
        "institutes",
        "taskers",
        "methods",
        "parties",
    ):
        assert (dirs["tables_dir"] / f"{name}.parquet").exists()

    assert read_parquet(dirs["tables_dir"] / "surveys.parquet").shape[0] == 3
    assert load_state("dawum", dirs["state_dir"]).last_update == TOKEN


@respx.mock
def test_second_run_without_update_fetches_nothing(dirs, mock_api):
    mock_api()
    run_ingest(make_source(), **dirs)
    respx.reset()

    route = respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=TOKEN))
    api = respx.get(API_URL).mock(return_value=httpx.Response(200, text="{}"))

    result = run_ingest(make_source(), **dirs)
    assert result.fetched is False
    assert route.call_count == 1
    assert api.call_count == 0, "the full database must not be downloaded"


@respx.mock
def test_ingestion_is_idempotent(dirs, mock_api):
    """Running twice must leave byte-identical tables."""
    mock_api()
    run_ingest(make_source(), **dirs)
    first = {p.name: p.read_bytes() for p in sorted(dirs["tables_dir"].glob("*.parquet"))}

    run_ingest(make_source(), force=True, **dirs)
    second = {p.name: p.read_bytes() for p in sorted(dirs["tables_dir"].glob("*.parquet"))}

    assert first.keys() == second.keys()
    for name in first:
        assert first[name] == second[name], f"{name} changed between identical runs"


@respx.mock
def test_force_fetches_despite_unchanged_token(dirs, mock_api):
    mock_api()
    run_ingest(make_source(), **dirs)
    respx.reset()
    mock_api()
    result = run_ingest(make_source(), force=True, **dirs)
    assert result.fetched is True


@respx.mock
def test_snapshot_is_archived_gzipped_and_content_addressed(dirs, mock_api):
    mock_api()
    result = run_ingest(make_source(), **dirs)
    path = result.snapshot_path
    assert path.suffixes == [".json", ".gz"]
    assert result.snapshot.sha256[:12] in path.name
    restored = json.loads(gzip.decompress(path.read_bytes()))
    assert restored["Database"]["Last_Update"] == TOKEN


@respx.mock
def test_archived_snapshot_is_byte_stable(dirs, mock_api):
    """gzip must not embed an mtime, or identical content would differ."""
    mock_api()
    first = run_ingest(make_source(), **dirs).snapshot_path.read_bytes()
    second_dirs = {k: v.parent / f"{v.name}2" for k, v in dirs.items()}
    respx.reset()
    mock_api()
    second = run_ingest(make_source(), **second_dirs).snapshot_path.read_bytes()
    assert first == second


# --- quarantine reporting --------------------------------------------------


@respx.mock
def test_quarantine_report_is_written_even_when_clean(dirs, mock_api):
    mock_api()
    result = run_ingest(make_source(), **dirs)
    report = read_json(result.quarantine_path)
    assert report["n_surveys_accepted"] == 3
    assert report["n_surveys_quarantined"] == 0
    assert report["quarantined"] == []
    assert report["snapshot_sha256"] == result.snapshot.sha256


@respx.mock
def test_quarantine_report_records_bad_rows(dirs, mock_api, mutate):
    payload = mutate("200", Date="10.03.2026")
    mock_api(payload=payload)
    result = run_ingest(make_source(), **dirs)

    assert result.n_quarantined == 1
    report = read_json(result.quarantine_path)
    assert report["n_surveys_quarantined"] == 1
    entry = report["quarantined"][0]
    assert entry["source_id"] == "200"
    assert entry["record"]["Date"] == "10.03.2026"
    assert report["findings_by_rule"]["dates_parseable"]["error"] == 1
    # The clean rows still made it through.
    assert read_parquet(dirs["tables_dir"] / "surveys.parquet").shape[0] == 2


@respx.mock
def test_warning_counts_are_reported(dirs, mock_api, mutate):
    payload = mutate("200", Surveyed_Persons="")
    mock_api(payload=payload)
    result = run_ingest(make_source(), **dirs)
    assert result.n_warnings == 1
    report = read_json(result.quarantine_path)
    assert report["findings_by_rule"]["sample_size"]["warning"] == 1


# --- result helpers --------------------------------------------------------


def test_empty_result_reports_zeroes():
    result = IngestResult(source="dawum", fetched=False, reason="no update")
    assert (result.n_surveys, result.n_quarantined, result.n_warnings) == (0, 0, 0)


def test_write_tables_returns_every_path(minimal_snapshot, tmp_path):
    tables = make_source().normalize(minimal_snapshot)
    paths = write_tables(tables, tmp_path)
    assert len(paths) == 7
    assert all(p.exists() for p in paths)


def test_snapshot_requires_aware_timestamp():
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        RawSnapshot(source="x", content=b"{}", retrieved_at=datetime(2026, 1, 1))


@respx.mock
def test_identical_content_keeps_original_retrieved_at(dirs, mock_api):
    """A redundant fetch must not rewrite every row with a new timestamp."""
    mock_api()
    first = run_ingest(make_source(), **dirs)
    respx.reset()
    mock_api()
    second = run_ingest(make_source(), force=True, **dirs)

    assert second.snapshot.sha256 == first.snapshot.sha256
    assert second.snapshot.retrieved_at == first.snapshot.retrieved_at
    assert set(second.tables.surveys["retrieved_at"]) == set(first.tables.surveys["retrieved_at"])


@respx.mock
def test_changed_content_updates_retrieved_at(dirs, mock_api, mutate):
    mock_api()
    first = run_ingest(make_source(), **dirs)
    respx.reset()
    mock_api(token="2026-09-22T09:00:00+02:00", payload=mutate("200", Surveyed_Persons="1600"))
    second = run_ingest(make_source(), **dirs)

    assert second.snapshot.sha256 != first.snapshot.sha256
    assert second.snapshot.retrieved_at > first.snapshot.retrieved_at


@respx.mock
def test_replay_makes_no_request(dirs, mock_api):
    mock_api()
    first = run_ingest(make_source(), **dirs)
    snapshot_path = first.snapshot_path
    respx.reset()
    # No routes are mocked, so any HTTP call would raise.
    result = run_ingest(make_source(), from_snapshot=snapshot_path, **dirs)
    assert result.fetched is False
    assert "replayed" in result.reason
    assert result.n_surveys == 3


@respx.mock
def test_replay_reproduces_identical_tables(dirs, mock_api):
    """This is the invariant the CI idempotency step relies on."""
    mock_api()
    first = run_ingest(make_source(), **dirs)
    before = {p.name: p.read_bytes() for p in sorted(dirs["tables_dir"].glob("*.parquet"))}
    respx.reset()
    run_ingest(make_source(), from_snapshot=first.snapshot_path, **dirs)
    after = {p.name: p.read_bytes() for p in sorted(dirs["tables_dir"].glob("*.parquet"))}
    assert before == after
