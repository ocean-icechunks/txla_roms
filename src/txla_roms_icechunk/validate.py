"""Validate a committed store from the consumer read path against the source files."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import netCDF4
import numpy as np

from txla_roms_icechunk.config import BEST_GROUP, FORECAST_GROUP
from txla_roms_icechunk.store import open_readonly

if TYPE_CHECKING:
    from txla_roms_icechunk.sources import Run
    from txla_roms_icechunk.store import StoreTarget

log = logging.getLogger(__name__)


def spot_check(target: StoreTarget, kept: list[Run], n: int = 3, seed: int = 0) -> None:
    """Compare a few random (run, lead, variable) fields read through the store with the
    source read by netCDF4, then check that the newest /best hour comes from the newest run."""
    F = open_readonly(target, FORECAST_GROUP)
    B = open_readonly(target, BEST_GROUP)
    if list(F.forecast_reference_time.values) != [r.frt for r in kept]:
        msg = "runs in the store differ from the runs selected from the listing"
        raise AssertionError(msg)
    rng = random.Random(seed)
    for _ in range(n):
        r = rng.choice(kept)
        j = rng.randrange(r.n)
        v = rng.choice(["zeta", "temp", "salt", "u"])
        a = F[v].sel(forecast_reference_time=r.frt, lead=np.timedelta64(j + 1, "h")).values
        with netCDF4.Dataset(r.url + "#mode=bytes") as d:
            b = d[v][j].filled(np.nan)
        np.testing.assert_array_equal(a, b)
        log.info("spot check OK: %s run %s lead %d h", v, r.archived, j + 1)
    last = B.isel(time=-1)
    if last.forecast_reference_time.values != kept[-1].frt:
        msg = "newest /best hour does not come from the newest run"
        raise AssertionError(msg)
    np.testing.assert_array_equal(
        last.zeta.values, F.zeta.sel(forecast_reference_time=kept[-1].frt).isel(lead=-1).values
    )
    log.info("best/forecast consistency OK")
