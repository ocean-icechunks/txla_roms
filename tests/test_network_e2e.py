"""End-to-end against TAMU-CC's server. Run explicitly: ``pytest -m network``."""

from __future__ import annotations

import pytest

from txla_roms_icechunk import config, sources


@pytest.mark.network
def test_discover_and_open_newest_run() -> None:
    urls, _ = sources.discover()
    assert urls
    assert all(u.startswith(config.SOURCE_PREFIX) for u in urls)
    run = sources.open_run(urls[-1], sources.make_registry())
    assert 1 <= run.n <= config.N_LEAD
