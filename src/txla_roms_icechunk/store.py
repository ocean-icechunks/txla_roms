"""Icechunk repo plumbing: storage targets, the virtual chunk container, store state.

CLI commands take a *storage target* (a local path or an S3 bucket/prefix), so the same
commands run on a laptop against a directory and in GitHub Actions against the published store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import icechunk as ic
import xarray as xr
import zarr

from txla_roms_icechunk.config import FINGERPRINT_ATTR, LAYOUT_VERSION, SOURCE_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- Storage targets ---------------------------------------------------------


@dataclass(frozen=True)
class StoreTarget:
    """Where the Icechunk repo lives. Exactly one destination is set."""

    local_path: Path | None = None
    s3_bucket: str | None = None
    s3_prefix: str = "txla_roms.icechunk"
    s3_region: str = "us-west-2"
    s3_endpoint: str | None = None
    s3_anonymous: bool = False
    s3_acl: str | None = "bucket-owner-full-control"

    def __post_init__(self) -> None:
        if (self.local_path is not None) + (self.s3_bucket is not None) != 1:
            msg = "Exactly one of local_path or s3_bucket must be set"
            raise ValueError(msg)

    def storage(self) -> ic.Storage:
        """Icechunk ``Storage`` for this target.

        S3 writes carry ``x-amz-acl: bucket-owner-full-control`` (Source Cooperative needs it
        for cross-account uploads; a no-op for same-account writes). ``s3_acl=None`` sends none.
        """
        if self.local_path is not None:
            return ic.local_filesystem_storage(str(self.local_path))
        assert self.s3_bucket is not None
        return ic.s3_storage(
            bucket=self.s3_bucket,
            prefix=self.s3_prefix,
            region=self.s3_region,
            endpoint_url=self.s3_endpoint,
            anonymous=self.s3_anonymous or None,
            from_env=not self.s3_anonymous,
            allow_http=(self.s3_endpoint or "").startswith("http://"),
            force_path_style=bool(self.s3_endpoint),
            write_headers={"x-amz-acl": self.s3_acl} if self.s3_acl else None,
        )

    def describe(self) -> str:
        if self.local_path is not None:
            return str(self.local_path)
        return f"s3://{self.s3_bucket}/{self.s3_prefix}"


def as_target(where: StoreTarget | str | Path) -> StoreTarget:
    """Accept a StoreTarget or a local path (notebooks pass a temporary directory)."""
    return where if isinstance(where, StoreTarget) else StoreTarget(local_path=Path(where))


# --- Repository --------------------------------------------------------------


def repo_config(prefix: str = SOURCE_PREFIX) -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(url_prefix=prefix, store=ic.http_store()))
    return cfg


def virtual_credentials(prefix: str = SOURCE_PREFIX) -> dict[str, Any]:
    """Anonymous HTTPS: no secret, but every prefix must still be authorized explicitly."""
    return {prefix: ic.credentials.HttpAccess}


def open_repo(where: StoreTarget | str | Path, create: bool = False) -> ic.Repository:
    """Open the repository with the source container registered and authorized.

    A missing repository raises ``FileNotFoundError`` unless ``create=True`` (only
    ``txla update --create`` passes it), so a typo'd target never silently becomes a new store.
    """
    target = as_target(where)
    storage = target.storage()
    if ic.Repository.exists(storage):
        repo = ic.Repository.open(storage, config=repo_config(), authorize_virtual_chunk_access=virtual_credentials())
    elif create:
        repo = ic.Repository.create(storage, config=repo_config(), authorize_virtual_chunk_access=virtual_credentials())
    else:
        msg = f"No Icechunk repository at {target.describe()}; run `txla update --create`"
        raise FileNotFoundError(msg)
    repo.save_config()
    return repo


def open_or_create(where: StoreTarget | str | Path) -> ic.Repository:
    return open_repo(where, create=True)


def open_readonly(
    where: StoreTarget | str | Path,
    group: str,
    chunks: Any = None,
    snapshot_id: str | None = None,
) -> xr.Dataset:
    """Consumer read path: a fresh Repository.open with explicit anonymous HTTP authorization."""
    repo = ic.Repository.open(as_target(where).storage(), authorize_virtual_chunk_access=virtual_credentials())
    session = repo.readonly_session(snapshot_id=snapshot_id) if snapshot_id else repo.readonly_session("main")
    return xr.open_zarr(session.store, group=group, consolidated=False, chunks=chunks, zarr_format=3)


# --- Store state -------------------------------------------------------------


def fingerprint(listing: Mapping[str, Any]) -> str:
    """Hash of what the source archive lists, plus the layout version.

    If it matches the one recorded on ``main``, a rebuild would produce the same store, so
    ``txla update`` does nothing and creates no snapshot.
    """
    payload = json.dumps({"layout": LAYOUT_VERSION, "files": sorted(listing.items())}, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def root_attrs(repo: ic.Repository, branch: str = "main") -> dict[str, Any]:
    """Root-group attributes on ``branch`` (empty for a new repository)."""
    session = repo.readonly_session(branch)
    try:
        return dict(zarr.open_group(session.store, mode="r").attrs)
    except (zarr.errors.GroupNotFoundError, FileNotFoundError):
        return {}


def stored_fingerprint(repo: ic.Repository) -> str | None:
    return root_attrs(repo).get(FINGERPRINT_ATTR)
