"""CLI behaviour, including the CI failure switch."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from wahlwetter.cli import main
from wahlwetter.sources.dawum import API_URL, LAST_UPDATE_URL

TOKEN = "2026-09-21T07:46:18+02:00"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never write into the real repository from a test."""
    for name in ("TABLES_DIR", "STATE_DIR", "QUARANTINE_DIR", "RAW_DIR"):
        monkeypatch.setattr(f"wahlwetter.config.{name}", tmp_path / name.lower(), raising=True)
        monkeypatch.setattr(f"wahlwetter.ingest.{name}", tmp_path / name.lower(), raising=True)


def mock_api(payload):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=TOKEN))
    respx.get(API_URL).mock(return_value=httpx.Response(200, text=json.dumps(payload)))


@respx.mock
def test_ingest_reports_success(minimal_payload_dict, capsys):
    mock_api(minimal_payload_dict)
    assert main(["ingest", "--source", "dawum"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["fetched"] is True
    assert summary["surveys"] == 3
    assert summary["quarantined"] == 0


@respx.mock
def test_fail_switch_trips_on_quarantined_rows(mutate, capsys):
    mock_api(mutate("200", Date="10.03.2026"))
    assert main(["ingest", "--fail-on-error-findings"]) == 1
    assert "quarantined" in capsys.readouterr().err


@respx.mock
def test_fail_switch_passes_on_clean_data(minimal_payload_dict):
    mock_api(minimal_payload_dict)
    assert main(["ingest", "--fail-on-error-findings"]) == 0


@respx.mock
def test_warnings_are_printed_to_stderr(mutate, capsys):
    mock_api(mutate("200", Surveyed_Persons=""))
    assert main(["ingest"]) == 0
    assert "warning: sample_size" in capsys.readouterr().err


def test_unknown_source_is_rejected():
    with pytest.raises(SystemExit):
        main(["ingest", "--source", "nope"])
