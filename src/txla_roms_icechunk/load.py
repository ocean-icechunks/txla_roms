"""Open the TXLA ROMS virtual Icechunk store. Standalone: copy this file anywhere.

    from txla_roms_icechunk.load import open_txla
    best = open_txla("best", repo_path="local-store")        # local copy
    best = open_txla("best", s3_bucket="...", s3_prefix="...")  # published copy (anonymous)

Needs: icechunk>=2.3, xarray>=2026.9, zarr>=3.4. No credentials: the repository is read from a
local path or anonymously from S3, and the data bytes are read anonymously over HTTPS from
TAMU-CC's server.
"""

from __future__ import annotations

from typing import Any

import icechunk as ic
import xarray as xr
import zarr


def open_txla(
    group: str = "best",
    repo_path: str | None = "local-store",
    *,
    s3_bucket: str | None = None,
    s3_prefix: str = "txla-roms/txla_roms.icechunk",
    s3_region: str = "us-west-2",
    s3_endpoint: str | None = None,
    branch: str = "main",
    tag: str | None = None,
    snapshot_id: str | None = None,
    lazy: bool = True,
    concurrency: int = 32,
) -> xr.Dataset:
    """group: "best" or "forecast".

    lazy=True (default) -> lazy Dask arrays (chunks={}); lazy=False -> NumPy on access (chunks=None).
    Keep concurrency modest: every data read goes to one university web server.
    """
    chunks: Any = {} if lazy else None
    zarr.config.set({"async.concurrency": concurrency})
    if s3_bucket:
        storage = ic.s3_storage(
            bucket=s3_bucket,
            prefix=s3_prefix,
            region=s3_region,
            endpoint_url=s3_endpoint,
            anonymous=True,
            force_path_style=bool(s3_endpoint),
        )
    else:
        storage = ic.local_filesystem_storage(str(repo_path))
    # 1. read the persisted virtual chunk containers, 2. authorize each (anonymous HTTPS)
    prefixes = list((ic.Repository.fetch_config(storage).virtual_chunk_containers or {}).keys())
    repo = ic.Repository.open(
        storage, authorize_virtual_chunk_access=dict.fromkeys(prefixes, ic.credentials.HttpAccess)
    )
    if snapshot_id:
        session = repo.readonly_session(snapshot_id=snapshot_id)
    elif tag:
        session = repo.readonly_session(tag=tag)
    else:
        session = repo.readonly_session(branch)
    return xr.open_zarr(session.store, group=group, consolidated=False, chunks=chunks, zarr_format=3)


if __name__ == "__main__":
    ds = open_txla("best")
    print(ds)  # noqa: T201
    print(ds.temp.sel(time=ds.time[-1]).isel(Depth=0).mean().compute().item())  # noqa: T201
