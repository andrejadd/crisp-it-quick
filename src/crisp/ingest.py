"""Finding new recordings on the recorder and copying them into the dated archive."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from crisp.db import NewRecording, Repository

log = logging.getLogger("crisp.ingest")

CHUNK = 1024 * 1024


class IngestCancelled(Exception):
    """The ingest run was cancelled, typically because the recorder was unplugged."""


class CopyError(Exception):
    pass


def find_record_dir(mount: Path, folder: str) -> Path | None:
    """Locate the recordings folder, matching its name case-insensitively."""
    exact = mount / folder
    if exact.is_dir():
        return exact
    try:
        for entry in mount.iterdir():
            if entry.is_dir() and entry.name.lower() == folder.lower():
                return entry
    except OSError:
        return None
    return None


def discover(record_dir: Path) -> list[Path]:
    """Regular ``*.wav`` files (any case) directly inside ``record_dir``, sorted by name."""
    return sorted(
        (p for p in record_dir.iterdir() if p.is_file() and p.suffix.lower() == ".wav"),
        key=lambda p: p.name,
    )


def recorded_at(mtime: float, tz: ZoneInfo) -> datetime:
    """Interpret the device mtime as wall-clock time in ``tz``.

    The recorder (FAT) stores local wall-clock time without a zone and is mounted with
    ``tz=UTC``, so the epoch value carries that wall-clock reading as if it were UTC.
    """
    wall = datetime.fromtimestamp(mtime, UTC).replace(tzinfo=None)
    return wall.replace(tzinfo=tz)


_NAME_TIMESTAMP = re.compile(r"^(\d{14})(?!\d)")


def start_from_name(name: str, tz: ZoneInfo) -> datetime | None:
    """Recording start encoded in the device file name (``YYYYMMDDHHMMSS.WAV``), if any."""
    m = _NAME_TIMESTAMP.match(name)
    if not m:
        return None
    try:
        wall = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if not 2000 <= wall.year <= 2099:
        return None
    return wall.replace(tzinfo=tz)


def recording_time(name: str, mtime: float, tz: ZoneInfo) -> datetime:
    """Start time from the file name, falling back to the (end-of-recording) mtime."""
    return start_from_name(name, tz) or recorded_at(mtime, tz)


@dataclass(frozen=True)
class ArchiveNames:
    audio: Path  # relative to the archive dir
    text: Path


def archive_names(when: datetime, ext: str, suffix: int = 1) -> ArchiveNames:
    stem = f"{when:%Y_%m_%d}_time{when:%H%M%S}" + (f"-{suffix}" if suffix > 1 else "")
    month = Path(f"{when:%Y-%m}")
    ext = ext.lower().lstrip(".")
    return ArchiveNames(month / f"{stem}_audio.{ext}", month / f"{stem}_text.md")


def choose_names(
    when: datetime, ext: str, archive_dir: Path, taken: Callable[[str, str], bool]
) -> ArchiveNames:
    """First free name pair; a suffix applies to both audio and transcript."""
    suffix = 1
    while True:
        names = archive_names(when, ext, suffix)
        if not (
            (archive_dir / names.audio).exists()
            or (archive_dir / names.text).exists()
            or taken(str(names.audio), str(names.text))
        ):
            return names
        suffix += 1


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise IngestCancelled
            h.update(chunk)
    return h.hexdigest()


def safe_copy(
    src: Path, dst: Path, expected_sha: str, cancel: threading.Event | None = None
) -> None:
    """Copy via ``<dst>.part`` and only rename into place when the hash matches."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    h = hashlib.sha256()
    try:
        with src.open("rb") as fin, part.open("wb") as fout:
            while chunk := fin.read(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise IngestCancelled
                h.update(chunk)
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        if h.hexdigest() != expected_sha:
            raise CopyError(
                f"hash mismatch copying {src}: expected {expected_sha}, got {h.hexdigest()}"
            )
        os.replace(part, dst)
    finally:
        part.unlink(missing_ok=True)


@dataclass
class IngestResult:
    archived: list[int]
    skipped: int
    failed: int


def ingest_volume(
    mount: Path,
    *,
    record_folder: str,
    archive_dir: Path,
    tz: ZoneInfo,
    repo: Repository,
    on_archived: Callable[[int], None] = lambda _id: None,
    cancel: threading.Event | None = None,
    chown: Callable[[Path], None] = lambda _p: None,
) -> IngestResult:
    """Copy every not-yet-archived recording from ``mount``. Never writes to the device."""
    result = IngestResult(archived=[], skipped=0, failed=0)
    record_dir = find_record_dir(mount, record_folder)
    if record_dir is None:
        log.warning("no %s folder on recorder at %s; nothing to ingest", record_folder, mount)
        return result

    for src in discover(record_dir):
        if cancel is not None and cancel.is_set():
            raise IngestCancelled
        try:
            st = src.stat()
            if repo.find_by_fast_key(src.name, st.st_size, st.st_mtime):
                result.skipped += 1
                continue
            sha = sha256_file(src, cancel)
            if repo.find_by_sha(sha):
                log.debug("%s already archived (same content)", src.name)
                result.skipped += 1
                continue
            when = recording_time(src.name, st.st_mtime, tz)
            names = choose_names(when, src.suffix, archive_dir, repo.path_taken)
            dst = archive_dir / names.audio
            safe_copy(src, dst, sha, cancel)
            chown(dst.parent)
            chown(dst)
            rec_id = repo.insert_copied(
                NewRecording(
                    sha256=sha,
                    source_name=src.name,
                    source_size=st.st_size,
                    source_mtime=st.st_mtime,
                    recorded_at=when.isoformat(),
                    audio_path=str(names.audio),
                    text_path=str(names.text),
                )
            )
            log.info("archived %s -> %s", src.name, names.audio)
            result.archived.append(rec_id)
            on_archived(rec_id)
        except IngestCancelled:
            raise
        except (OSError, CopyError) as e:
            # Recorder unplugged mid-read surfaces as OSError; retry on next connection.
            result.failed += 1
            log.error("copying %s failed: %s", src.name, e)
            if cancel is not None and cancel.is_set():
                raise IngestCancelled from e
    log.info(
        "ingest finished: %d archived, %d already known, %d failed",
        len(result.archived), result.skipped, result.failed,
    )
    return result
