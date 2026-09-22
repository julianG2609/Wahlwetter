"""Shared fixtures.

`tests/fixtures/dawum_minimal.json` is SYNTHETIC. It mirrors the structure
observed in the real dawum payload (see docs/data_source.md) but every name and
number in it is made up, so that the test suite asserts nothing about real
polls and no real data is vendored into the repository.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wahlwetter.sources.base import RawSnapshot

FIXTURE_DIR = Path(__file__).parent / "fixtures"
MINIMAL_PATH = FIXTURE_DIR / "dawum_minimal.json"

# Fixed instant, so nothing in the suite depends on the wall clock.
FIXED_RETRIEVED_AT = datetime(2026, 9, 22, 11, 31, 17, tzinfo=UTC)


@pytest.fixture
def minimal_payload_dict() -> dict[str, Any]:
    return json.loads(MINIMAL_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def make_snapshot():
    def _make(payload: dict[str, Any], *, source: str = "dawum") -> RawSnapshot:
        return RawSnapshot(
            source=source,
            content=json.dumps(payload).encode("utf-8"),
            retrieved_at=FIXED_RETRIEVED_AT,
            update_token=payload.get("Database", {}).get("Last_Update"),
        )

    return _make


@pytest.fixture
def minimal_snapshot(minimal_payload_dict, make_snapshot) -> RawSnapshot:
    return make_snapshot(minimal_payload_dict)


@pytest.fixture
def mutate(minimal_payload_dict):
    """Return a deep copy of the fixture with one survey field replaced."""

    def _mutate(source_id: str, **changes: Any) -> dict[str, Any]:
        payload = copy.deepcopy(minimal_payload_dict)
        payload["Surveys"][source_id].update(changes)
        return payload

    return _mutate
