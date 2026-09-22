"""Source-agnostic interfaces.

Everything downstream of ingestion depends only on what is defined here.
Concrete sources are resolved through the registry, so adding a scraper later
must never require a change to normalization, validation or modeling code.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from wahlwetter.findings import Finding

# Length of the hex digest used for survey IDs. 16 hex chars = 64 bits, which
# makes a collision implausible at any plausible number of polls while keeping
# the identifier readable in a Parquet file.
SURVEY_ID_LENGTH = 16


def make_survey_id(source: str, source_id: str) -> str:
    """Deterministic, stable identifier for a poll.

    A hash of (source, source_id) rather than a running number, so that IDs
    survive re-ingestion, never depend on row order, and cannot collide when a
    second source is added later.
    """
    digest = hashlib.sha256(f"{source}:{source_id}".encode())
    return digest.hexdigest()[:SURVEY_ID_LENGTH]


@dataclass(frozen=True, slots=True)
class SourceState:
    """What we remember about a source between runs.

    Persisted to ``data/state/<source>.json`` and committed, so a scheduled run
    can tell whether upstream changed without downloading anything large.
    """

    last_update: str | None = None
    etag: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SourceState:
        if not raw:
            return cls()
        return cls(
            last_update=raw.get("last_update"),
            etag=raw.get("etag"),
            content_sha256=raw.get("content_sha256"),
            retrieved_at=raw.get("retrieved_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_update": self.last_update,
            "etag": self.etag,
            "content_sha256": self.content_sha256,
            "retrieved_at": self.retrieved_at,
        }


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    """Result of the cheap "did anything change?" check."""

    should_fetch: bool
    reason: str
    remote_token: str | None = None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class RawSnapshot:
    """An immutable, content-addressed copy of what a source returned."""

    source: str
    content: bytes
    retrieved_at: datetime
    update_token: str | None = None
    etag: str | None = None

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def snapshot_name(self) -> str:
        """Filename for the archived snapshot on the ``data-raw`` branch."""
        stamp = self.retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{stamp}-{self.sha256[:12]}.json.gz"


@dataclass(frozen=True, slots=True)
class NormalizedTables:
    """Tidy tables plus everything that did not make it into them."""

    surveys: pd.DataFrame
    results: pd.DataFrame
    dimensions: dict[str, pd.DataFrame] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class Source(Protocol):
    """The contract every data source implements."""

    name: str

    def check_for_update(self, state: SourceState) -> UpdateDecision:
        """Cheaply determine whether a full fetch is warranted."""
        ...

    def fetch(self) -> RawSnapshot:
        """Download the full payload. At most one request per run."""
        ...

    def normalize(self, snapshot: RawSnapshot) -> NormalizedTables:
        """Turn a raw snapshot into tidy tables and findings. Pure function."""
        ...
