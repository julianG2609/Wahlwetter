"""Source registry: downstream code resolves sources by name only."""

from __future__ import annotations

import pytest

from wahlwetter.sources import Source, available_sources, get_source
from wahlwetter.sources.dawum import DawumSource


def test_dawum_is_registered():
    assert "dawum" in available_sources()


def test_get_source_returns_an_instance():
    assert isinstance(get_source("dawum"), DawumSource)


def test_dawum_satisfies_the_source_protocol():
    """Adding a scraper later must not require touching downstream code."""
    assert isinstance(DawumSource(parties_by_parliament={}), Source)


def test_unknown_source_names_the_alternatives():
    with pytest.raises(KeyError, match="dawum"):
        get_source("nope")
