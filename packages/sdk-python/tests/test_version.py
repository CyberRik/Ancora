"""Smoke test: the SDK imports and exposes a semver-ish version string."""

from __future__ import annotations

import ancora


def test_version_is_exposed() -> None:
    assert isinstance(ancora.__version__, str)
    assert ancora.__version__.count(".") >= 2
