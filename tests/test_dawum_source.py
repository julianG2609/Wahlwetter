"""DawumSource: update detection, fetching and payload strictness.

Every test is offline; httpx is intercepted with respx.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from wahlwetter.config import USER_AGENT
from wahlwetter.sources.base import SourceState
from wahlwetter.sources.dawum import (
    API_URL,
    LAST_UPDATE_URL,
    DawumPayload,
    DawumSource,
)

TOKEN = "2026-09-21T07:46:18+02:00"
NEWER = "2026-09-22T09:00:00+02:00"


@pytest.fixture
def source():
    return DawumSource()


# --- update detection ------------------------------------------------------


@respx.mock
def test_first_run_fetches(source):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=TOKEN))
    decision = source.check_for_update(SourceState())
    assert decision.should_fetch is True
    assert decision.remote_token == TOKEN


@respx.mock
def test_unchanged_token_does_not_fetch(source):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=TOKEN))
    decision = source.check_for_update(SourceState(last_update=TOKEN))
    assert decision.should_fetch is False


@respx.mock
def test_changed_token_fetches(source):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=NEWER))
    decision = source.check_for_update(SourceState(last_update=TOKEN))
    assert decision.should_fetch is True
    assert TOKEN in decision.reason and NEWER in decision.reason


@respx.mock
def test_304_does_not_fetch_and_keeps_state(source):
    route = respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(304))
    state = SourceState(last_update=TOKEN, etag='"abc"')
    decision = source.check_for_update(state)
    assert decision.should_fetch is False
    assert decision.remote_token == TOKEN
    assert decision.etag == '"abc"'
    assert route.calls[0].request.headers["If-None-Match"] == '"abc"'


@respx.mock
def test_no_conditional_header_without_stored_etag(source):
    route = respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=TOKEN))
    source.check_for_update(SourceState())
    assert "If-None-Match" not in route.calls[0].request.headers


@respx.mock
def test_update_check_raises_on_server_error(source):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        source.check_for_update(SourceState())


@respx.mock
def test_token_is_stripped(source):
    respx.get(LAST_UPDATE_URL).mock(return_value=httpx.Response(200, text=f"{TOKEN}\n"))
    assert source.check_for_update(SourceState()).remote_token == TOKEN


# --- fetching --------------------------------------------------------------


@respx.mock
def test_fetch_makes_exactly_one_request(source, minimal_payload_dict):
    body = json.dumps(minimal_payload_dict)
    route = respx.get(API_URL).mock(return_value=httpx.Response(200, text=body))
    snapshot = source.fetch()
    assert route.call_count == 1
    assert snapshot.source == "dawum"
    assert snapshot.update_token == TOKEN


@respx.mock
def test_fetch_sends_identifying_user_agent(source, minimal_payload_dict):
    route = respx.get(API_URL).mock(
        return_value=httpx.Response(200, text=json.dumps(minimal_payload_dict))
    )
    source.fetch()
    agent = route.calls[0].request.headers["User-Agent"]
    assert agent == USER_AGENT
    assert "github.com" in agent


@respx.mock
def test_fetch_survives_unparseable_body_for_token(source):
    respx.get(API_URL).mock(return_value=httpx.Response(200, text="not json"))
    snapshot = source.fetch()
    assert snapshot.update_token is None
    assert snapshot.content == b"not json"


@respx.mock
def test_fetch_raises_on_error_status(source):
    respx.get(API_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        source.fetch()


# --- payload strictness ----------------------------------------------------


def test_payload_parses_fixture(minimal_payload_dict):
    payload = DawumPayload.model_validate(minimal_payload_dict)
    assert len(payload.Surveys) == 3
    assert payload.Parliaments["0"].Shortcut == "Bundestag"


def test_payload_rejects_unknown_top_level_field(minimal_payload_dict):
    """A new upstream block must fail loudly, not be silently ignored."""
    minimal_payload_dict["Something_New"] = {}
    with pytest.raises(ValueError, match="Something_New"):
        DawumPayload.model_validate(minimal_payload_dict)


def test_payload_rejects_unknown_survey_field(mutate):
    payload = mutate("200", Margin_Of_Error="3")
    with pytest.raises(ValueError, match="Margin_Of_Error"):
        DawumPayload.model_validate(payload)


def test_payload_accepts_integer_sample_size(mutate):
    payload = DawumPayload.model_validate(mutate("200", Surveyed_Persons=1500))
    assert str(payload.Surveys["200"].Surveyed_Persons) == "1500"


def test_payload_keeps_unparseable_dates_as_strings(mutate):
    """Value problems belong to the validation layer, not to parsing."""
    payload = DawumPayload.model_validate(mutate("200", Date="10.03.2026"))
    assert payload.Surveys["200"].Date == "10.03.2026"
