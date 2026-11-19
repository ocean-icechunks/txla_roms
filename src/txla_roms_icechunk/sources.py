"""Discover TXLA source files on TAMU-CC's Apache server and open them virtually.

Each source file is one forecast run of up to 96 hourly records. In NetCDF-3 every variable
at every record is one contiguous byte range, so every chunk is one hour of one variable.
"""

from __future__ import annotations

import concurrent.futures as cf
import html
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.parsers import NetCDF3Parser

from txla_roms_icechunk.config import N_LEAD, NAME_RE, SOURCE_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable

    import xarray as xr

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- discovery
_HREF = re.compile(r'<a href="([^"?/][^"]*)">')
_ROW = re.compile(
    r'<a href="([^"?/][^"]*)">[^<]*</a></td><td align="right">([\d-]+ [\d:]+)\s*</td>'
    r'<td align="right">\s*([^<]*?)\s*</td>'
)


def _get(url: str, tries: int = 4) -> str:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # transient listing failures: retry, then raise
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise AssertionError


def parse_listing(page: str) -> tuple[list[str], dict[str, tuple[str, str]]]:
    """Hrefs on an Apache index page, and (last modified, size) for rows that have them."""
    names = [html.unescape(n) for n in _HREF.findall(page)]
    rows = {html.unescape(n): (mt, sz) for n, mt, sz in _ROW.findall(page)}
    return names, rows


def discover_listing(prefix: str = SOURCE_PREFIX) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Walk the YYYY/MM/ folders of the Apache listing.

    Returns ({matching URL: (listed mtime, listed size)}, other entries that did not match)."""
    found: dict[str, tuple[str, str]] = {}
    other: list[str] = []
    for y in parse_listing(_get(prefix))[0]:
        if not re.fullmatch(r"\d{4}/", y):
            other.append(y)
            continue
        for m in parse_listing(_get(prefix + y))[0]:
            if not re.fullmatch(r"\d{2}/", m):
                other.append(y + m)
                continue
            names, rows = parse_listing(_get(prefix + y + m))
            for f in names:
                if NAME_RE.match(f):
                    found[prefix + y + m + f] = rows.get(f, ("", ""))
                else:
                    other.append(y + m + f)
    return found, other


def sort_urls(urls: Iterable[str]) -> list[str]:
    """Sort by file name (the archive timestamp), not by folder."""
    return sorted(urls, key=lambda u: u.rsplit("/", 1)[1])


def discover(prefix: str = SOURCE_PREFIX) -> tuple[list[str], list[str]]:
    """(sorted matching URLs, other entries that did not match the pattern)."""
    found, other = discover_listing(prefix)
    return sort_urls(found), other


# ---------------------------------------------------------------- per-file virtual open
@dataclass
class Run:
    url: str
    archived: str  # timestamp in the filename (UTC time the file was archived)
    frt: np.datetime64  # forecast reference time = first record - 1 h
    leads: np.ndarray  # integer lead hours of each record (1..n)
    vds: xr.Dataset | None

    @property
    def n(self) -> int:
        return len(self.leads)


def make_registry(prefix: str = SOURCE_PREFIX) -> ObjectStoreRegistry:
    return ObjectStoreRegistry({prefix: from_url(prefix)})


def run_from_times(url: str, t: np.ndarray, vds: xr.Dataset | None = None) -> Run:
    """Check the time axis is hourly leads 1..n<=96 starting at T01:00 and build a Run."""
    t = t.astype("datetime64[s]")
    frt = t[0] - np.timedelta64(1, "h")
    if frt != frt.astype("datetime64[D]"):
        msg = f"{url}: first record {t[0]} is not T01:00; cannot infer the run start"
        raise ValueError(msg)
    leads = ((t - frt) // np.timedelta64(1, "h")).astype(int)
    if not np.array_equal(leads, np.arange(1, len(t) + 1)) or len(t) > N_LEAD:
        msg = f"{url}: time axis is not hourly leads 1..n<=96: {leads[:5]}..."
        raise ValueError(msg)
    mo = NAME_RE.match(url.rsplit("/", 1)[1])
    if mo is None:
        msg = f"{url}: file name does not match {NAME_RE.pattern}"
        raise ValueError(msg)
    return Run(url, mo.group(1), frt.astype("datetime64[ns]"), leads, vds)


def open_run(url: str, registry: ObjectStoreRegistry, load: tuple[str, ...] = ("time", "Depth")) -> Run:
    vds = open_virtual_dataset(url, registry=registry, parser=NetCDF3Parser(), loadable_variables=list(load))
    return run_from_times(url, vds["time"].values, vds)


def open_runs(urls: list[str], registry: ObjectStoreRegistry, workers: int = 4) -> list[Run]:
    """Header + time reads only. A few workers: this is someone else's server."""
    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda u: open_run(u, registry), urls))


def select_runs(runs: list[Run]) -> tuple[list[Run], list[tuple[str, str]]]:
    """One run per forecast_reference_time: the newest archived file wins (owner decision,
    2026-11-18). Returns (kept runs sorted by frt, [(excluded url, reason)])."""
    by_frt: dict[np.datetime64, list[Run]] = {}
    for r in runs:
        by_frt.setdefault(r.frt, []).append(r)
    kept, excluded = [], []
    for frt in sorted(by_frt):
        rs = sorted(by_frt[frt], key=lambda r: r.archived)
        kept.append(rs[-1])
        excluded.extend(
            (
                r.url,
                (
                    f"superseded by later-archived run {rs[-1].archived} "
                    f"for the same forecast_reference_time ({r.n} records)"
                ),
            )
            for r in rs[:-1]
        )
    return kept, excluded


# ---------------------------------------------------------------- consistency checks
def check_compatible(runs: list[Run]) -> list[str]:
    """Every array must agree on everything except the record count."""
    ref = runs[0].vds
    assert ref is not None
    data_vars = [str(v) for v in ref.data_vars]
    for r in runs[1:]:
        assert r.vds is not None
        if sorted(map(str, r.vds.data_vars)) != sorted(data_vars):
            msg = f"{r.url}: variable set differs"
            raise ValueError(msg)
        if not np.array_equal(r.vds["Depth"].values, ref["Depth"].values):
            msg = f"{r.url}: Depth levels differ"
            raise ValueError(msg)
        for v in data_vars:
            a, b = ref[v].data.metadata, r.vds[v].data.metadata
            if (
                a.data_type,
                a.chunk_grid.chunk_shape[1:],
                a.codecs,
                a.fill_value,
                a.shape[1:],
            ) != (
                b.data_type,
                b.chunk_grid.chunk_shape[1:],
                b.codecs,
                b.fill_value,
                b.shape[1:],
            ) or a.chunk_grid.chunk_shape[0] != 1:
                msg = f"{r.url}: encoding of {v} differs from {runs[0].url}"
                raise ValueError(msg)
    return data_vars


def load_grid(kept: list[Run], registry: ObjectStoreRegistry) -> xr.Dataset:
    """lon/lat from the first and last kept runs; they must match exactly."""
    ends = [kept[0].url, kept[-1].url]
    gs = [
        open_virtual_dataset(u, registry=registry, parser=NetCDF3Parser(), loadable_variables=["lon", "lat"])[
            ["lon", "lat"]
        ]
        for u in ends
    ]
    for k in ("lon", "lat"):
        if not np.array_equal(gs[0][k].values, gs[1][k].values):
            msg = f"{k} differs between {ends[0]} and {ends[1]}"
            raise ValueError(msg)
    return gs[0]
