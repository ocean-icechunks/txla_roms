"""Snapshot expiry + garbage collection.

Manual only (``maintenance.yml`` is dispatch-only and dry-run by default). Garbage collection
is the one destructive operation in this pipeline: check it on a throwaway copy of the store
before running it for real against production.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import icechunk

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 35


def retention_cutoff(now: datetime, days: int = DEFAULT_RETENTION_DAYS) -> datetime:
    return now - timedelta(days=days)


def expire(repo: icechunk.Repository, days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> Any:
    """Expire snapshots older than ``days`` and garbage-collect.

    ``expire_snapshots`` has no dry-run mode, so a dry run skips it and reports what
    ``garbage_collect(..., dry_run=True)`` would delete. Tags are not deleted
    (``delete_expired_tags=False``), so every ``data-through-*`` tag keeps resolving.
    """
    cutoff = retention_cutoff(datetime.now(tz=UTC), days=days)
    log.info("Retention cutoff: %s (dry_run=%s)", cutoff.isoformat(), dry_run)
    if dry_run:
        summary = repo.garbage_collect(cutoff, dry_run=True)
        log.warning("Dry run, would garbage collect: %s", summary)
        return summary
    main_before = repo.lookup_branch("main")
    expired = repo.expire_snapshots(older_than=cutoff)
    log.info("Expired %d snapshots", len(expired))
    if repo.lookup_branch("main") != main_before:
        msg = "main moved during expiry; not garbage collecting"
        raise RuntimeError(msg)
    summary = repo.garbage_collect(cutoff)
    log.info("Garbage collection summary: %s", summary)
    return summary
