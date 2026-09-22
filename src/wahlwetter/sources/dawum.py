"""The dawum.de source.

Schema observed on 2026-09-22 and documented in docs/data_source.md. Field
names here were read off the actual payload, not from dawum's prose docs.

Two-level strictness, deliberately:

* The pydantic models below guard the payload's *shape*. ``extra="forbid"``
  means a new upstream field fails loudly instead of being silently ignored.
* Field *values* are kept as strings here and checked by the validation layer,
  so that one malformed date quarantines one survey rather than aborting the
  whole run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from wahlwetter.config import (
    DEFAULT_VALIDATION,
    USER_AGENT,
    ValidationConfig,
    load_parties_by_parliament,
)
from wahlwetter.sources.base import (
    NormalizedTables,
    RawSnapshot,
    SourceState,
    UpdateDecision,
)

API_URL = "https://api.dawum.de/"
LAST_UPDATE_URL = "https://api.dawum.de/last_update.txt"

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DawumLicense(_Strict):
    Name: str
    Shortcut: str
    Link: str


class DawumDatabase(_Strict):
    License: DawumLicense
    Publisher: str
    Author: str
    Last_Update: str


class DawumParliament(_Strict):
    Shortcut: str
    Name: str
    Election: str


class DawumNamed(_Strict):
    """Institutes, Taskers and Methods all carry only a name."""

    Name: str


class DawumParty(_Strict):
    Shortcut: str
    Name: str


class DawumSurveyPeriod(_Strict):
    # Kept as strings: an unparseable date must become a quarantine finding,
    # not a crash during payload parsing.
    Date_Start: str
    Date_End: str


class DawumSurvey(_Strict):
    Date: str
    Survey_Period: DawumSurveyPeriod
    # Observed as a string of digits in every survey, but coerced from int too
    # in case upstream changes representation.
    Surveyed_Persons: str | int
    Parliament_ID: str
    Institute_ID: str
    Tasker_ID: str
    Method_ID: str
    Results: dict[str, float]


class DawumPayload(_Strict):
    Database: DawumDatabase
    Parliaments: dict[str, DawumParliament]
    Institutes: dict[str, DawumNamed]
    Taskers: dict[str, DawumNamed]
    Methods: dict[str, DawumNamed]
    Parties: dict[str, DawumParty]
    Surveys: dict[str, DawumSurvey] = Field(default_factory=dict)


class DawumSource:
    """Fetches and normalizes the dawum database."""

    name = "dawum"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        validation_config: ValidationConfig | None = None,
        parties_by_parliament: dict[str, set[str]] | None = None,
    ) -> None:
        self._client = client
        # Injected rather than read from disk inside normalize(), so that
        # normalization stays a pure function of its inputs and tests never
        # depend on the repository's own config files.
        self._validation_config = validation_config or DEFAULT_VALIDATION
        self._parties_by_parliament = (
            parties_by_parliament
            if parties_by_parliament is not None
            else load_parties_by_parliament()
        )

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    def check_for_update(self, state: SourceState) -> UpdateDecision:
        """Conditional GET against the 25-byte last_update.txt.

        On an unchanged day this costs either a 304 with no body, or 25 bytes.
        The full 1 MB database is only fetched when this says so.
        """
        headers: dict[str, str] = {}
        if state.etag:
            headers["If-None-Match"] = state.etag

        client = self._get_client()
        try:
            response = client.get(LAST_UPDATE_URL, headers=headers)
        finally:
            if self._client is None:
                client.close()

        if response.status_code == 304:
            return UpdateDecision(
                should_fetch=False,
                reason="last_update.txt unchanged (HTTP 304)",
                remote_token=state.last_update,
                etag=state.etag,
            )

        response.raise_for_status()
        remote_token = response.text.strip()
        etag = response.headers.get("etag")

        if state.last_update is not None and remote_token == state.last_update:
            return UpdateDecision(
                should_fetch=False,
                reason=f"last_update unchanged ({remote_token})",
                remote_token=remote_token,
                etag=etag,
            )

        previous = state.last_update or "none"
        return UpdateDecision(
            should_fetch=True,
            reason=f"last_update changed: {previous} -> {remote_token}",
            remote_token=remote_token,
            etag=etag,
        )

    def fetch(self) -> RawSnapshot:
        """Download the full database. One request."""
        client = self._get_client()
        try:
            response = client.get(API_URL)
            response.raise_for_status()
            content = response.content
            etag = response.headers.get("etag")
        finally:
            if self._client is None:
                client.close()

        # Read the authoritative update token out of the payload itself rather
        # than trusting the separate text file to have been in sync.
        try:
            token = json.loads(content)["Database"]["Last_Update"]
        except (ValueError, KeyError, TypeError):
            token = None

        return RawSnapshot(
            source=self.name,
            content=content,
            retrieved_at=datetime.now(UTC),
            update_token=token,
            etag=etag,
        )

    def parse(self, snapshot: RawSnapshot) -> DawumPayload:
        return DawumPayload.model_validate_json(snapshot.content)

    def normalize(self, snapshot: RawSnapshot) -> NormalizedTables:
        # Imported here to keep the module import graph acyclic: normalization
        # depends on validation, which depends on findings, not on sources.
        from wahlwetter.normalize import normalize_dawum

        return normalize_dawum(
            self.parse(snapshot),
            snapshot,
            config=self._validation_config,
            parties_by_parliament=self._parties_by_parliament,
        )
