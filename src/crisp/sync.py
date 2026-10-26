"""Reconciling the state database with the files actually present in the archive."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from crisp.db import Repository, Status

log = logging.getLogger("crisp.sync")


@dataclass
class SyncResult:
    deleted: int = 0
    restored: int = 0
    skipped: bool = False  # archive directory unavailable; nothing was checked


def sync_archive(repo: Repository, archive_dir: Path) -> SyncResult:
    """Mark audio/transcript files the user deleted by hand, and unmark restored ones."""
    result = SyncResult()
    try:
        with os.scandir(archive_dir):
            pass
    except OSError as e:
        log.warning("archive directory %s unavailable, skipping sync: %s", archive_dir, e)
        result.skipped = True
        return result

    for rec in repo.all():
        # A transcript only has to exist once the recording is done.
        checks = [("audio", rec.audio_path, True),
                  ("text", rec.text_path, rec.status == Status.DONE)]
        for kind, rel, required in checks:
            if (archive_dir / rel).exists():
                if repo.set_deleted(rec.id, kind, False):
                    result.restored += 1
                    log.info("file restored: %s", rel)
            elif required and repo.set_deleted(rec.id, kind, True):
                result.deleted += 1
                log.info("file deleted: %s", rel)

    if result.deleted or result.restored:
        log.info("archive sync: %d newly deleted, %d restored", result.deleted,
                 result.restored)
    else:
        log.debug("archive sync: no changes")
    return result
