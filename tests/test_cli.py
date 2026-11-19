"""CLI option resolution, without touching a real store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from txla_roms_icechunk import cli, store

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

runner = CliRunner()


def _captured_target(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> store.StoreTarget:
    captured: dict[str, store.StoreTarget] = {}

    def fake_open_repo(target: store.StoreTarget, create: bool = False) -> None:  # noqa: ARG001
        captured["target"] = target
        msg = "stop before touching a real store"
        raise RuntimeError(msg)

    monkeypatch.setattr(store, "open_repo", fake_open_repo)
    runner.invoke(cli.app, [*args, "status"])
    return captured["target"]


def test_local_path_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    t = _captured_target(monkeypatch, ["--store-path", str(tmp_path)])
    assert t.local_path == tmp_path
    assert t.s3_bucket is None


def test_s3_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TXLA_S3_BUCKET", "bucket")
    monkeypatch.setenv("TXLA_S3_PREFIX", "org/txla.icechunk")
    monkeypatch.setenv("TXLA_S3_ENDPOINT", "https://data.source.coop")
    monkeypatch.setenv("TXLA_S3_ACL", "none")
    t = _captured_target(monkeypatch, [])
    assert (t.s3_bucket, t.s3_prefix, t.s3_endpoint, t.s3_acl) == (
        "bucket",
        "org/txla.icechunk",
        "https://data.source.coop",
        None,
    )


def test_no_target_is_an_error() -> None:
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code != 0


def test_missing_store_exits_cleanly(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["--store-path", str(tmp_path / "nope"), "status"])
    assert result.exit_code == 1
    assert "No Icechunk repository" in result.output
