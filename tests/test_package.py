from __future__ import annotations

import importlib.metadata

import txla_roms_icechunk as m


def test_version() -> None:
    assert importlib.metadata.version("txla_roms_icechunk") == m.__version__
