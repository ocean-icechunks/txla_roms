"""Assemble the /forecast and /best groups from per-run virtual datasets and write them.

Store: one Icechunk repository with two groups that reference the same source bytes
    /forecast  dims (forecast_reference_time, lead, [Depth], Y, X)  every kept run
    /best      dims (time, [Depth], Y, X)  each hour from the newest run covering it

Nothing is rewritten; only metadata is repaired (see ATTR_REPAIRS).

The smoke-test notebook imports this module as ``tb``; it re-exports the discovery and
repository helpers it uses.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import xarray as xr
import zarr
from virtualizarr.manifests import ChunkManifest, ManifestArray
from virtualizarr.manifests.utils import copy_and_replace_metadata

from txla_roms_icechunk.config import (
    DATA_THROUGH_ATTR,
    FINGERPRINT_ATTR,
    LAST_UPDATE_ATTR,
    N_LEAD,
    SOURCE_PREFIX,
    TIME_ENC,
)
from txla_roms_icechunk.sources import (  # noqa: F401, TC001 - re-exported for the notebooks
    Run,
    check_compatible,
    discover,
    load_grid,
    make_registry,
    open_run,
    open_runs,
    select_runs,
)
from txla_roms_icechunk.store import open_or_create, open_readonly  # noqa: F401 - re-exported

if TYPE_CHECKING:
    import icechunk as ic

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- metadata repair
DROP_VAR_ATTRS = ("_CoordinateAxes", "field", "time", "coordinates")
# keep _FillValue: the base64 text is xarray's own Zarr-v3 encoding of 1e37, needed for masking
ATTR_REPAIRS: dict[str, dict[str, str | None]] = {
    # var: {attr: new value or None to delete}; reasons in README "Metadata repairs"
    "sustr": {"units": "N m-2", "standard_name": "surface_downward_eastward_stress"},
    "svstr": {"units": "N m-2", "standard_name": "surface_downward_northward_stress"},
    "O2": {
        "long_name": "Dissolved oxygen concentration",
        "standard_name": "mass_concentration_of_oxygen_in_sea_water",
    },
    "pH": {"standard_name": None, "units": "1"},
    "omega": {"standard_name": None, "units": "1", "long_name": "Aragonite saturation state (omega)"},
    "Depth": {"axis": "Z"},
}


def repair_attrs(name: str, attrs: dict[str, Any]) -> dict[str, Any]:
    a = {k: v for k, v in attrs.items() if k not in DROP_VAR_ATTRS}
    for k, v in ATTR_REPAIRS.get(name, {}).items():
        if v is None:
            a.pop(k, None)
        else:
            a[k] = v
    return a


def _global_attrs(ref: xr.Dataset, layout: str, kept: list[Run], excluded: list[tuple[str, str]]) -> dict[str, Any]:
    g = {k: v for k, v in ref.attrs.items() if k not in ("history", "type")}
    g.update(
        title=f"TXLA ROMS forecast, z-levels ({layout})",
        source_archive=SOURCE_PREFIX,
        source_format="NetCDF-3 classic, referenced in place (virtual Icechunk)",
        layout=layout,
        source_files=[r.url.removeprefix(SOURCE_PREFIX) for r in kept],
        excluded_source_files=[f"{u.removeprefix(SOURCE_PREFIX)}: {why}" for u, why in excluded],
        metadata_repairs=(
            "Dropped per-variable _CoordinateAxes, field, time, coordinates; sustr/svstr units N s-2 -> "
            "N m-2 and CF surface_downward_*_stress names; O2 long_name 'Bottom oxygen' -> dissolved oxygen "
            "(it is 3-D) with CF mass_concentration_of_oxygen_in_sea_water; pH and omega "
            "non-CF standard_names removed, units -> 1; Depth axis=Z; dropped per-file "
            "global history and type."
        ),
    )
    return g


# ---------------------------------------------------------------- manifest assembly
def _stack(slots: list[tuple[Run, int] | None], var: str, lead_shape: tuple[int, ...]) -> ManifestArray:
    """ManifestArray whose leading axes have shape `lead_shape`, one source record per slot
    (None = missing chunk -> reads as the fill value)."""
    first = next(s for s in slots if s is not None)[0].vds
    assert first is not None
    template = first[var].data
    rest_grid = template.manifest._paths.shape[1:]
    paths = np.full(len(slots), "", dtype=np.dtypes.StringDType())
    paths = np.broadcast_to(paths.reshape(-1, *(1,) * len(rest_grid)), (len(slots), *rest_grid)).copy()
    offsets = np.zeros(paths.shape, np.uint64)
    lengths = np.zeros(paths.shape, np.uint64)
    for i, s in enumerate(slots):
        if s is None:
            continue
        run, rec = s
        assert run.vds is not None
        m = run.vds[var].data.manifest
        paths[i], offsets[i], lengths[i] = m._paths[rec], m._offsets[rec], m._lengths[rec]
    shp = (*lead_shape, *rest_grid)
    manifest = ChunkManifest.from_arrays(
        paths=paths.reshape(shp), offsets=offsets.reshape(shp), lengths=lengths.reshape(shp)
    )
    md = copy_and_replace_metadata(
        template.metadata,
        new_shape=[*lead_shape, *template.shape[1:]],
        new_chunks=[*(1,) * len(lead_shape), *template.metadata.chunk_grid.chunk_shape[1:]],
    )
    return ManifestArray(metadata=md, chunkmanifest=manifest)


def _coords(ref: xr.Dataset, grid: xr.Dataset) -> dict[str, Any]:
    return {
        "Depth": ("Depth", ref["Depth"].values, repair_attrs("Depth", ref["Depth"].attrs)),
        "lon": (("Y", "X"), grid["lon"].values, repair_attrs("lon", grid["lon"].attrs)),
        "lat": (("Y", "X"), grid["lat"].values, repair_attrs("lat", grid["lat"].attrs)),
    }


def build_forecast(kept: list[Run], excluded: list[tuple[str, str]], grid: xr.Dataset) -> xr.Dataset:
    ref = kept[0].vds
    assert ref is not None
    frt = np.array([r.frt for r in kept], dtype="datetime64[ns]")
    lead = np.arange(1, N_LEAD + 1) * np.timedelta64(1, "h")
    slots = [(r, j) if j < r.n else None for r in kept for j in range(N_LEAD)]
    data = {}
    for v in ref.data_vars:
        dims = ("forecast_reference_time", "lead", *ref[v].dims[1:])
        data[v] = (dims, _stack(slots, str(v), (len(kept), N_LEAD)), repair_attrs(str(v), ref[v].attrs))
    coords = _coords(ref, grid)
    coords.update(
        forecast_reference_time=(
            "forecast_reference_time",
            frt,
            {"long_name": "Forecast run start (first record is lead 1 h)", "standard_name": "forecast_reference_time"},
        ),
        lead=(
            "lead",
            lead.astype("timedelta64[ns]"),
            {"long_name": "Hours after forecast_reference_time", "standard_name": "forecast_period"},
        ),
        valid_time=(
            ("forecast_reference_time", "lead"),
            frt[:, None] + lead[None, :].astype("timedelta64[ns]"),
            {"long_name": "Valid time", "standard_name": "time"},
        ),
    )
    return xr.Dataset(data, coords, attrs=_global_attrs(ref, "forecast", kept, excluded))


def best_slots(kept: list[Run]) -> tuple[np.ndarray, list[tuple[Run, int] | None]]:
    """Map every valid hour to (run, record) from the newest run that has it."""
    owner: dict[np.datetime64, tuple[Run, int]] = {}
    for r in kept:  # sorted by frt, so later runs overwrite earlier ones
        for j in range(r.n):
            owner[r.frt + np.timedelta64(int(r.leads[j]), "h")] = (r, j)
    times = np.array(sorted(owner), dtype="datetime64[ns]")
    full = np.arange(times[0], times[-1] + np.timedelta64(1, "h"), np.timedelta64(1, "h"))
    return full, [owner.get(t) for t in full]


def build_best(kept: list[Run], excluded: list[tuple[str, str]], grid: xr.Dataset) -> xr.Dataset:
    ref = kept[0].vds
    assert ref is not None
    times, slots = best_slots(kept)
    data = {
        v: (("time", *ref[v].dims[1:]), _stack(slots, str(v), (len(times),)), repair_attrs(str(v), ref[v].attrs))
        for v in ref.data_vars
    }
    nat = np.datetime64("NaT", "ns")
    coords = _coords(ref, grid)
    coords.update(
        time=("time", times, {"long_name": "Valid time", "standard_name": "time", "axis": "T"}),
        forecast_reference_time=(
            "time",
            np.array([s[0].frt if s else nat for s in slots], "datetime64[ns]"),
            {"long_name": "Run that supplies this hour", "standard_name": "forecast_reference_time"},
        ),
        lead=(
            "time",
            np.array([int(s[0].leads[s[1]]) if s else -1 for s in slots]) * np.timedelta64(1, "h"),
            {"long_name": "Lead time of the supplying run", "standard_name": "forecast_period"},
        ),
    )
    attrs = _global_attrs(ref, "best", kept, excluded)
    attrs["missing_hours"] = int(sum(s is None for s in slots))
    return xr.Dataset(data, coords, attrs=attrs)


# ---------------------------------------------------------------- icechunk
def set_encodings(ds: xr.Dataset) -> None:
    """Materialized coordinates are written as ONE chunk each.
    vz.to_icechunk takes no `encoding=` argument, so set .encoding in place."""
    for k, v in ds.coords.items():
        e: dict[str, Any] = {"chunks": v.shape}
        if np.issubdtype(v.dtype, np.datetime64):
            e |= TIME_ENC
        elif np.issubdtype(v.dtype, np.timedelta64):
            e |= {"units": "hours", "dtype": "int32"}
        ds[k].encoding = e


def write(
    repo: ic.Repository,
    forecast: xr.Dataset,
    best: xr.Dataset,
    message: str,
    fingerprint: str | None = None,
) -> str:
    """Replace both groups in one commit (the newest runs change /best every day).

    The root group records the source fingerprint, so an unchanged archive is a no-op next time."""
    set_encodings(forecast)
    set_encodings(best)
    session = repo.writable_session("main")
    forecast.vz.to_icechunk(session.store, group="forecast", mode="w")
    best.vz.to_icechunk(session.store, group="best", mode="w")
    root = zarr.open_group(session.store, mode="a")
    root.attrs.update(
        {
            FINGERPRINT_ATTR: fingerprint or "",
            LAST_UPDATE_ATTR: datetime.now(tz=UTC).isoformat(timespec="seconds"),
            DATA_THROUGH_ATTR: str(best.time.values[-1])[:19],
        }
    )
    return session.commit(message)
