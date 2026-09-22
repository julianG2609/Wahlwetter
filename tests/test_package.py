"""Smoke tests: the package imports and exposes its version.

Phase 0 has no behaviour to test yet; these exist so CI is meaningful from the
first commit and so a broken src-layout install fails loudly.
"""

import wahlwetter


def test_version_is_exposed() -> None:
    assert isinstance(wahlwetter.__version__, str)
    assert wahlwetter.__version__


def test_package_is_typed() -> None:
    """The py.typed marker must ship with the package."""
    from importlib.resources import files

    assert files("wahlwetter").joinpath("py.typed").is_file()
