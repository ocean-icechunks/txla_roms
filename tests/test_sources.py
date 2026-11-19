"""Offline tests of listing parsing and run selection."""

from __future__ import annotations

import numpy as np
import pytest

from txla_roms_icechunk import config, sources

MONTH_PAGE = """
<tr><th><a href="?C=N;O=D">Name</a></th></tr>
<tr><td><a href="/data/MASTR_MODELS/txla/history/2026/">Parent Directory</a></td></tr>
<tr><td valign="top"><img src="/icons/unknown.gif" alt="[   ]"></td><td><a href="txla2_his_f_latest_z.2026-11-15-213301.nc">txla2_his_f_latest_z.2026-11-15-21..&gt;</a></td><td align="right">2026-11-15 21:35  </td><td align="right">9.2G</td><td>&nbsp;</td></tr>
<tr><td valign="top"><img src="/icons/unknown.gif" alt="[   ]"></td><td><a href="notes.txt">notes.txt</a></td><td align="right">2026-11-01 10:00  </td><td align="right">1.0K</td><td>&nbsp;</td></tr>
"""


def test_parse_listing_rows() -> None:
    names, rows = sources.parse_listing(MONTH_PAGE)
    assert "txla2_his_f_latest_z.2026-11-15-213301.nc" in names
    assert rows["txla2_his_f_latest_z.2026-11-15-213301.nc"] == ("2026-11-15 21:35", "9.2G")
    assert "?C=N;O=D" not in names  # sort links are not entries
    assert not any(n.startswith("/") for n in names)  # nor is the parent directory


def test_name_pattern() -> None:
    assert config.NAME_RE.match("txla2_his_f_latest_z.2026-11-15-213301.nc")
    assert not config.NAME_RE.match("notes.txt")


def test_sort_urls_by_file_name() -> None:
    p = config.SOURCE_PREFIX
    urls = [
        p + "2026/09/txla2_his_f_latest_z.2026-09-01-000000.nc",
        p + "2026/08/txla2_his_f_latest_z.2026-08-31-171807.nc",
    ]
    assert sources.sort_urls(urls) == urls[::-1]


def _times(day: str, n: int) -> np.ndarray:
    return np.datetime64(f"{day}T01:00") + np.arange(n) * np.timedelta64(1, "h")


def _url(stamp: str) -> str:
    return config.SOURCE_PREFIX + f"2026/11/txla2_his_f_latest_z.{stamp}.nc"


def test_run_from_times() -> None:
    r = sources.run_from_times(_url("2026-11-16-213301"), _times("2026-11-15", 96))
    assert r.frt == np.datetime64("2026-11-15T00:00", "ns")
    assert r.n == 96
    assert r.archived == "2026-11-16-213301"


def test_run_from_times_rejects_bad_start() -> None:
    t = np.datetime64("2026-11-15T02:00") + np.arange(3) * np.timedelta64(1, "h")
    with pytest.raises(ValueError, match="not T01:00"):
        sources.run_from_times(_url("2026-11-16-213301"), t)


def test_run_from_times_rejects_gaps() -> None:
    t = _times("2026-11-15", 4)[[0, 1, 3]]
    with pytest.raises(ValueError, match="hourly"):
        sources.run_from_times(_url("2026-11-16-213301"), t)


def test_select_runs_newest_archived_wins() -> None:
    a = sources.run_from_times(_url("2026-08-30-114753"), _times("2026-08-30", 1))
    b = sources.run_from_times(_url("2026-08-31-171807"), _times("2026-08-30", 96))
    c = sources.run_from_times(_url("2026-09-01-000000"), _times("2026-08-31", 96))
    kept, excluded = sources.select_runs([c, a, b])
    assert [r.archived for r in kept] == ["2026-08-31-171807", "2026-09-01-000000"]
    assert [u for u, _ in excluded] == [a.url]
    assert "superseded" in excluded[0][1]
