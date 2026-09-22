"""Source lookup by name.

Downstream code resolves sources through this registry and never imports a
concrete implementation, so adding a scraper later is a one-line change here.
"""

from __future__ import annotations

from collections.abc import Callable

from wahlwetter.sources.base import Source
from wahlwetter.sources.dawum import DawumSource

_REGISTRY: dict[str, Callable[[], Source]] = {
    "dawum": DawumSource,
}


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def get_source(name: str) -> Source:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown source {name!r}; available: {', '.join(available_sources())}"
        ) from None
    return factory()
