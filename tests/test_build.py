"""Offline tests of the /best assembly and metadata repairs."""

from __future__ import annotations

import numpy as np

from txla_roms_icechunk import build, config, sources


def _run(day: str, n: int, stamp: str) -> sources.Run:
    t = np.datetime64(f"{day}T01:00") + np.arange(n) * np.timedelta64(1, "h")
    return sources.run_from_times(config.SOURCE_PREFIX + f"x/txla2_his_f_latest_z.{stamp}.nc", t)


def test_best_takes_newest_run() -> None:
    r1 = _run("2026-11-01", 96, "2026-11-02-000000")
    r2 = _run("2026-11-02", 96, "2026-11-03-000000")
    times, slots = build.best_slots([r1, r2])
    assert len(times) == 24 + 96
    assert all(s is not None for s in slots)
    # hours of 11-02 01:00 onward come from the newer run
    i = int(np.argmax(times == np.datetime64("2026-11-02T01:00", "ns")))
    assert slots[i - 1][0] is r1
    assert slots[i][0] is r2
    assert slots[-1] == (r2, 95)


def test_best_marks_gaps() -> None:
    r1 = _run("2026-11-01", 2, "2026-11-02-000000")
    r2 = _run("2026-11-10", 1, "2026-11-11-000000")
    times, slots = build.best_slots([r1, r2])
    assert times[0] == np.datetime64("2026-11-01T01:00", "ns")
    assert times[-1] == np.datetime64("2026-11-10T01:00", "ns")
    assert sum(s is None for s in slots) == len(times) - 3


def test_repair_attrs() -> None:
    a = build.repair_attrs("sustr", {"units": "N s-2", "coordinates": "depth", "long_name": "x"})
    assert a == {"units": "N m-2", "standard_name": "surface_downward_eastward_stress", "long_name": "x"}
    assert "standard_name" not in build.repair_attrs("pH", {"standard_name": "potential_hydrogen"})
