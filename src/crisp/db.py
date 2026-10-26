"""SQLite state database: schema migrations and recording repository."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    COPIED = "copied"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    CLEANED = "cleaned"
    DONE = "done"
    FAILED = "failed"


# Numbered migrations; index + 1 is the schema version they produce. Append only.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE recordings (
        id            INTEGER PRIMARY KEY,
        sha256        TEXT    NOT NULL UNIQUE,
        source_name   TEXT    NOT NULL,
        source_size   INTEGER NOT NULL,
        source_mtime  REAL    NOT NULL,
        recorded_at   TEXT    NOT NULL,
        audio_path    TEXT    NOT NULL UNIQUE,
        text_path     TEXT    NOT NULL UNIQUE,
        duration_s    REAL,
        status        TEXT    NOT NULL,
        attempts      INTEGER NOT NULL DEFAULT 0,
        last_error    TEXT,
        stt_provider  TEXT,
        stt_model     TEXT,
        cleanup_model TEXT,
        cleanup_error TEXT,
        created_at    TEXT    NOT NULL,
        updated_at    TEXT    NOT NULL
    );
    CREATE INDEX ix_recordings_fast_key ON recordings (source_name, source_size, source_mtime);
    CREATE INDEX ix_recordings_status ON recordings (status);
    CREATE TABLE transcripts (
        recording_id INTEGER PRIMARY KEY REFERENCES recordings (id) ON DELETE CASCADE,
        raw_text     TEXT,
        cleaned_text TEXT
    );
    """,
    """
    ALTER TABLE recordings ADD COLUMN audio_deleted_at TEXT;
    ALTER TABLE recordings ADD COLUMN text_deleted_at TEXT;
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Recording:
    id: int
    sha256: str
    source_name: str
    source_size: int
    source_mtime: float
    recorded_at: str
    audio_path: str
    text_path: str
    duration_s: float | None
    status: Status
    attempts: int
    last_error: str | None
    stt_provider: str | None
    stt_model: str | None
    cleanup_model: str | None
    cleanup_error: str | None
    created_at: str
    updated_at: str
    audio_deleted_at: str | None = None
    text_deleted_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Recording:
        data = dict(row)
        data["status"] = Status(data["status"])
        return cls(**data)


@dataclass(frozen=True)
class NewRecording:
    sha256: str
    source_name: str
    source_size: int
    source_mtime: float
    recorded_at: str
    audio_path: str
    text_path: str


class Repository:
    """Thread-safe access to the state database (one shared connection, serialized)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- schema -----------------------------------------------------------

    def schema_version(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return row[0] or 0

    def migrate(self) -> int:
        """Apply pending migrations; returns the resulting schema version."""
        with self._lock:
            current = self.schema_version()
            for version in range(current + 1, SCHEMA_VERSION + 1):
                script = MIGRATIONS[version - 1]
                self._conn.executescript(
                    f"BEGIN;\n{script}\nINSERT INTO schema_version (version) VALUES ({version});\n"
                    "COMMIT;"
                )
            return self.schema_version()

    # -- lookups ----------------------------------------------------------

    def get(self, recording_id: int) -> Recording | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return Recording.from_row(row) if row else None

    def find_by_sha(self, sha256: str) -> Recording | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recordings WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return Recording.from_row(row) if row else None

    def find_by_fast_key(self, source_name: str, size: int, mtime: float) -> Recording | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM recordings WHERE source_name = ? AND source_size = ? "
                "AND source_mtime = ?",
                (source_name, size, mtime),
            ).fetchone()
        return Recording.from_row(row) if row else None

    def path_taken(self, audio_path: str, text_path: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM recordings WHERE audio_path = ? OR text_path = ?",
                (audio_path, text_path),
            ).fetchone()
        return row is not None

    def all(self) -> list[Recording]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM recordings ORDER BY id").fetchall()
        return [Recording.from_row(r) for r in rows]

    def resumable_ids(self, max_attempts: int) -> list[int]:
        """Recordings to (re)process at startup: unfinished ones, and failed below the limit."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM recordings WHERE audio_deleted_at IS NULL AND "
                "(status IN (?, ?, ?, ?) OR (status = ? AND attempts < ?)) "
                "ORDER BY recorded_at, id",
                (
                    Status.COPIED,
                    Status.TRANSCRIBING,
                    Status.TRANSCRIBED,
                    Status.CLEANED,
                    Status.FAILED,
                    max_attempts,
                ),
            ).fetchall()
        return [r[0] for r in rows]

    # -- writes -----------------------------------------------------------

    def insert_copied(self, rec: NewRecording) -> int:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO recordings (sha256, source_name, source_size, source_mtime, "
                "recorded_at, audio_path, text_path, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.sha256,
                    rec.source_name,
                    rec.source_size,
                    rec.source_mtime,
                    rec.recorded_at,
                    rec.audio_path,
                    rec.text_path,
                    Status.COPIED,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def _update(self, recording_id: int, **fields: object) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE recordings SET {cols} WHERE id = ?", (*fields.values(), recording_id)
            )

    def start_attempt(self, recording_id: int) -> None:
        """Mark a recording as being transcribed and count the attempt."""
        with self._lock:
            self._conn.execute(
                "UPDATE recordings SET status = ?, attempts = attempts + 1, last_error = NULL, "
                "updated_at = ? WHERE id = ?",
                (Status.TRANSCRIBING, _now(), recording_id),
            )

    def set_status(self, recording_id: int, status: Status, **fields: object) -> None:
        self._update(recording_id, status=str(status), **fields)

    def mark_failed(self, recording_id: int, error: str, *, permanent: bool = False,
                    max_attempts: int | None = None) -> None:
        """Mark failed; ``permanent`` exhausts the attempt budget so it is not retried."""
        if permanent and max_attempts is not None:
            with self._lock:
                self._conn.execute(
                    "UPDATE recordings SET status = ?, last_error = ?, "
                    "attempts = MAX(attempts, ?), updated_at = ? WHERE id = ?",
                    (Status.FAILED, error, max_attempts, _now(), recording_id),
                )
        else:
            self._update(recording_id, status=str(Status.FAILED), last_error=error)

    def store_raw(self, recording_id: int, raw_text: str, *, duration_s: float,
                  provider: str, model: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT INTO transcripts (recording_id, raw_text) VALUES (?, ?) "
                    "ON CONFLICT (recording_id) DO UPDATE SET raw_text = excluded.raw_text, "
                    "cleaned_text = NULL",
                    (recording_id, raw_text),
                )
                self._conn.execute(
                    "UPDATE recordings SET status = ?, duration_s = ?, stt_provider = ?, "
                    "stt_model = ?, updated_at = ? WHERE id = ?",
                    (Status.TRANSCRIBED, duration_s, provider, model, _now(), recording_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def store_cleaned(self, recording_id: int, cleaned_text: str | None, *,
                      model: str | None, error: str | None) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "UPDATE transcripts SET cleaned_text = ? WHERE recording_id = ?",
                    (cleaned_text, recording_id),
                )
                self._conn.execute(
                    "UPDATE recordings SET status = ?, cleanup_model = ?, cleanup_error = ?, "
                    "updated_at = ? WHERE id = ?",
                    (Status.CLEANED, model, error, _now(), recording_id),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def set_deleted(self, recording_id: int, kind: str, deleted: bool) -> bool:
        """Mark (keeping an existing time) or clear a deleted file; returns whether it changed."""
        if kind not in ("audio", "text"):
            raise ValueError(f"unknown file kind {kind!r}")
        col = f"{kind}_deleted_at"
        with self._lock:
            if deleted:
                now = _now()
                cur = self._conn.execute(
                    f"UPDATE recordings SET {col} = ?, updated_at = ? "
                    f"WHERE id = ? AND {col} IS NULL",
                    (now, now, recording_id),
                )
            else:
                cur = self._conn.execute(
                    f"UPDATE recordings SET {col} = NULL, updated_at = ? "
                    f"WHERE id = ? AND {col} IS NOT NULL",
                    (_now(), recording_id),
                )
            return cur.rowcount > 0

    def get_texts(self, recording_id: int) -> tuple[str | None, str | None]:
        with self._lock:
            row = self._conn.execute(
                "SELECT raw_text, cleaned_text FROM transcripts WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return (row[0], row[1]) if row else (None, None)
