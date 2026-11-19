"""The ``txla`` command-line interface (Typer).

Every command reads the store's state, compares it with the source archive and does only the
missing work. The storage target comes from global options with env-variable fallbacks
(``--store-path`` / ``TXLA_STORE_PATH``, ``--s3-bucket`` / ``TXLA_S3_BUCKET``, ...), so the
same commands run locally with flags and in GitHub Actions with env vars.
"""

from __future__ import annotations

import csv
import logging
import time
import warnings
from enum import StrEnum
from pathlib import Path  # noqa: TC003 - typer needs it at runtime
from typing import Annotated

import icechunk as ic
import typer
import zarr

from txla_roms_icechunk import build, config, maintenance, sources, store, validate

log = logging.getLogger(__name__)

app = typer.Typer(name="txla", help="Maintain the TXLA ROMS virtual Icechunk store.", no_args_is_help=True)


@app.callback()
def main_options(
    ctx: typer.Context,
    store_path: Annotated[
        Path | None, typer.Option(envvar="TXLA_STORE_PATH", help="Local Icechunk store directory.")
    ] = None,
    s3_bucket: Annotated[str | None, typer.Option(envvar="TXLA_S3_BUCKET", help="Destination S3 bucket.")] = None,
    s3_prefix: Annotated[
        str, typer.Option(envvar="TXLA_S3_PREFIX", help="Key prefix inside the bucket.")
    ] = "txla_roms.icechunk",
    s3_region: Annotated[str, typer.Option(envvar="TXLA_S3_REGION", help="Destination bucket region.")] = "us-west-2",
    s3_endpoint: Annotated[
        str | None,
        typer.Option(envvar="TXLA_S3_ENDPOINT", help="Custom S3 endpoint (e.g. https://data.source.coop)."),
    ] = None,
    s3_acl: Annotated[
        str,
        typer.Option(
            envvar="TXLA_S3_ACL",
            help="Canned ACL on every object written; empty or 'none' sends none.",
        ),
    ] = "bucket-owner-full-control",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Resolve the storage target shared by every subcommand."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    ic.set_logs_filter("info" if verbose else "error")
    warnings.filterwarnings("ignore", message="Numcodecs codecs are not in the Zarr version 3 specification")
    try:
        ctx.obj = store.StoreTarget(
            local_path=store_path,
            s3_bucket=s3_bucket,
            s3_prefix=s3_prefix,
            s3_region=s3_region,
            s3_endpoint=s3_endpoint,
            s3_acl=None if s3_acl.strip().lower() in {"", "none"} else s3_acl,
        )
    except ValueError as err:
        raise typer.BadParameter(str(err)) from err


def _open_repo(target: store.StoreTarget, *, create: bool = False) -> ic.Repository:
    """Open the repo, turning a missing store into a clean CLI exit."""
    try:
        return store.open_repo(target, create=create)
    except FileNotFoundError as err:
        typer.echo(f"ERROR: {err}")
        raise typer.Exit(1) from err


def write_manifest(path: Path, runs: list[sources.Run], excluded: list[tuple[str, str]]) -> None:
    """Every discovered file and the decision made for it."""
    why = dict(excluded)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "archived_utc", "forecast_reference_time", "records", "decision"])
        for r in sorted(runs, key=lambda r: r.archived):
            w.writerow(
                [
                    r.url.removeprefix(config.SOURCE_PREFIX),
                    r.archived,
                    str(r.frt)[:19],
                    r.n,
                    f"excluded: {why[r.url]}" if r.url in why else "kept",
                ]
            )


@app.command()
def update(
    ctx: typer.Context,
    create: Annotated[
        bool, typer.Option(help="Create the store if it does not exist. The only way a store is created.")
    ] = False,
    force: Annotated[bool, typer.Option(help="Rebuild even if the source listing is unchanged.")] = False,
    dry_run: Annotated[bool, typer.Option(help="Discover, open and assemble, but do not write.")] = False,
    validate_only: Annotated[bool, typer.Option(help="Validate and tag the current main without writing.")] = False,
    workers: Annotated[int, typer.Option(help="Parallel header reads against the source.")] = config.DEFAULT_WORKERS,
    manifest: Annotated[
        Path | None, typer.Option(help="Write the source manifest CSV (every file, the decision made) here.")
    ] = None,
) -> None:
    """List the archive and, if anything changed, rebuild /forecast and /best in one snapshot.

    A full rebuild is the update method on purpose: each new run changes the last three days
    of /best. Validates from the consumer read path and tags ``data-through-<last valid day>``.
    """
    target: store.StoreTarget = ctx.obj
    t0 = time.time()
    repo = None if dry_run else _open_repo(target, create=create)

    listing, other = sources.discover_listing()
    urls = sources.sort_urls(listing)
    typer.echo(f"Discovered {len(urls)} files; {len(other)} entries not matching the pattern")
    if other:
        log.warning("ignored entries: %s", other)
    if not urls:
        typer.echo("ERROR: no source files found")
        raise typer.Exit(1)
    fp = store.fingerprint(listing)
    if repo is not None and not (force or validate_only) and store.stored_fingerprint(repo) == fp:
        typer.echo("Source listing unchanged since the last build. Nothing to do.")
        return

    reg = sources.make_registry()
    runs = sources.open_runs(urls, reg, workers=workers)
    kept, excluded = sources.select_runs(runs)
    sources.check_compatible(kept)
    grid = sources.load_grid(kept, reg)
    typer.echo(f"Kept {len(kept)} runs, excluded {len(excluded)} ({time.time() - t0:.0f}s)")
    if manifest:
        write_manifest(manifest, runs, excluded)

    fc = build.build_forecast(kept, excluded, grid)
    best = build.build_best(kept, excluded, grid)
    typer.echo(f"forecast {dict(fc.sizes)}; best {dict(best.sizes)}, {best.attrs['missing_hours']} missing hours")
    if repo is None:
        return

    if validate_only:
        snap = repo.lookup_branch("main")
    else:
        msg = f"{len(kept)} runs, {str(kept[0].frt)[:10]} to {str(kept[-1].frt)[:10]}; {len(excluded)} files excluded"
        snap = build.write(repo, fc, best, msg, fingerprint=fp)
        typer.echo(f"Committed {snap}: {msg}")

    zarr.config.set({"async.concurrency": config.READ_CONCURRENCY})
    validate.spot_check(target, kept)
    tag = f"data-through-{str(best.time.values[-1])[:10]}"
    if tag not in repo.list_tags():
        repo.create_tag(tag, snap)
        typer.echo(f"Tagged {tag}")
    typer.echo(f"Done in {time.time() - t0:.0f}s; main at {snap}")


class Format(StrEnum):
    text = "text"
    markdown = "markdown"


@app.command()
def status(
    ctx: typer.Context,
    fmt: Annotated[Format, typer.Option("--format", help="Output format.")] = Format.text,
) -> None:
    """Report what the store holds: snapshot, coverage, last update, newest tags."""
    target: store.StoreTarget = ctx.obj
    repo = _open_repo(target)
    snap = repo.lookup_branch("main")
    attrs = store.root_attrs(repo)
    F = store.open_readonly(target, config.FORECAST_GROUP, chunks=None)
    B = store.open_readonly(target, config.BEST_GROUP, chunks=None)
    frt = F.forecast_reference_time.values
    tags = sorted(t for t in repo.list_tags() if t.startswith("data-through-"))
    rows = [
        ("Store", target.describe()),
        ("main", snap),
        ("Last update (UTC)", attrs.get(config.LAST_UPDATE_ATTR, "?")),
        ("/forecast runs", f"{len(frt)}: {str(frt[0])[:10]} .. {str(frt[-1])[:10]}"),
        ("/best hours", f"{B.sizes['time']}: {str(B.time.values[0])[:16]} .. {str(B.time.values[-1])[:16]}"),
        ("/best missing hours", str(B.attrs.get("missing_hours", "?"))),
        ("Excluded source files", str(len(F.attrs.get("excluded_source_files", [])))),
        ("Newest tag", tags[-1] if tags else "none"),
    ]
    if fmt is Format.markdown:
        typer.echo("| | |\n|---|---|")
        for k, v in rows:
            typer.echo(f"| {k} | `{v}` |")
    else:
        for k, v in rows:
            typer.echo(f"{k:24} {v}")


@app.command()
def expire(
    ctx: typer.Context,
    days: Annotated[int, typer.Option(help="Retention window in days.")] = maintenance.DEFAULT_RETENTION_DAYS,
    dry_run: Annotated[bool, typer.Option(help="Report what would be collected without deleting.")] = False,
) -> None:
    """Expire snapshots older than --days and garbage-collect (tags are kept)."""
    repo = _open_repo(ctx.obj)
    typer.echo(str(maintenance.expire(repo, days=days, dry_run=dry_run)))


def main() -> None:
    app()
