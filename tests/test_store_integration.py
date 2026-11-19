"""Store plumbing against a local temporary repository (no network)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import icechunk as ic
import pytest
import zarr

from txla_roms_icechunk import config, store

if TYPE_CHECKING:
    from pathlib import Path


def test_open_repo_refuses_to_create(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--create"):
        store.open_repo(tmp_path / "missing")


def test_create_persists_virtual_container(tmp_path: Path) -> None:
    store.open_repo(tmp_path / "repo", create=True)
    cfg = ic.Repository.fetch_config(ic.local_filesystem_storage(str(tmp_path / "repo")))
    assert cfg is not None
    assert list((cfg.virtual_chunk_containers or {}).keys()) == [config.SOURCE_PREFIX]


def test_fingerprint_roundtrip(tmp_path: Path) -> None:
    repo = store.open_repo(tmp_path / "repo", create=True)
    assert store.stored_fingerprint(repo) is None
    listing = {config.SOURCE_PREFIX + "a.nc": ("2026-11-01 10:00", "9.2G")}
    fp = store.fingerprint(listing)
    session = repo.writable_session("main")
    zarr.open_group(session.store, mode="a").attrs[config.FINGERPRINT_ATTR] = fp
    session.commit("fingerprint")
    assert store.stored_fingerprint(store.open_repo(tmp_path / "repo")) == fp
    # any change in the listing changes the fingerprint
    assert store.fingerprint({config.SOURCE_PREFIX + "a.nc": ("2026-11-02 10:00", "9.2G")}) != fp


def test_target_requires_one_destination(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        store.StoreTarget()
    with pytest.raises(ValueError, match="Exactly one"):
        store.StoreTarget(local_path=tmp_path, s3_bucket="b")
