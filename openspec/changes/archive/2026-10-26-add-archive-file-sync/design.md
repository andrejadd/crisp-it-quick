# Design

## Context

The `recordings` table stores each recording's `audio_path` and `text_path` relative to `paths.archive_dir`, plus a pipeline `status`. Nothing reads these paths back except the pipeline (to transcribe and to write the transcript). Ingest de-duplicates by fast key (name/size/mtime) and SHA-256, both answered from the database alone, so a recording stays "known" as long as its row exists. `Repository` is one shared, lock-serialized SQLite connection in WAL mode, so a second process (the CLI) can safely write concurrently. See proposal.md for motivation and specs/archive-sync/spec.md for required behavior.

## Goals / Non-Goals

**Goals:**
- Cheap, idempotent reconciliation that can run often.
- Per-file deletion state, since audio and transcript are deleted independently.
- Never misread an unavailable data directory as mass deletion.

**Non-Goals:**
- Real-time detection (inotify). A periodic poll is enough for a home archive.
- Deleting rows or transcript text from the database, or a "forget"/re-import command. The stored raw/cleaned text is kept, which leaves room for a later "regenerate transcript" feature.
- Detecting files the user added, renamed or moved within the archive. A moved file is reported as deleted.
- Changing files: the sync only reads the filesystem.

## Decisions

**Two nullable timestamp columns instead of a `deleted` status.** Migration 2 adds `audio_deleted_at TEXT` and `text_deleted_at TEXT` to `recordings`. Deletion is orthogonal to pipeline progress (a `done` recording can lose its audio, its transcript, or both), and keeping `status` intact preserves the history of how far processing got. A single `deleted` status was rejected because it loses which file went missing and would need special handling in every status check. Timestamps (not booleans) answer "when did I delete this" at no extra cost.

**Stat each recorded path rather than walk the archive.** The sync iterates the database rows and calls `exists()` on `archive_dir / audio_path` and, for `done` rows, `archive_dir / text_path`. This is O(rows), needs no path parsing, and naturally ignores unrelated files and `.part`/`.tmp` leftovers. Walking the directory tree and diffing sets was considered; it only pays off for detecting added files, which is a non-goal.

**Guard before touching anything.** The sync first checks that `archive_dir` is a directory and can be listed (`os.scandir`). If not, it logs a warning and returns without writes. This covers an unmounted volume or a typo'd `data_dir`. A finer heuristic (e.g. "refuse if more than N% vanished") was rejected as surprising; the directory check covers the realistic failure.

**Writes only on state change.** The repository exposes one method that sets or clears a deleted column only when the value actually changes (`... WHERE audio_deleted_at IS NULL` for marking, `IS NOT NULL` for clearing), so existing detection times are preserved and an unchanged sync performs no writes. The sync returns counts (newly deleted, restored) for logging and the CLI.

**New module `crisp/sync.py`** with a synchronous `sync_archive(repo, archive_dir) -> SyncResult`, mirroring `ingest_volume`'s style. The service calls it with `asyncio.to_thread`.

**Service wiring.** `Service.run` calls the sync once before `enqueue_resumable()`, then starts a periodic task (`await asyncio.wait_for(stop.wait(), interval)` loop) when `sync.interval_s > 0`. A failing sync is logged and does not stop the service.

**Resume and pipeline.** `resumable_ids` gains `AND audio_deleted_at IS NULL`. `Pipeline.process` returns early when `audio_deleted_at` is set. When a transcription is needed (`raw is None`) and the audio file is missing, it marks the audio deleted and returns before `start_attempt`, so no attempt is counted and no decode error is logged. Recordings already `transcribed`/`cleaned` whose audio was deleted are also not resumed, keeping the rule simple: deleted audio means "the user is done with this recording".

**Transcripts are never rewritten when deleted.** Transcript deletion is only detected for `done` rows, and `done` rows are never reprocessed, so no extra check is needed in the writer. A unit test pins this behavior.

**CLI `crisp sync`.** Loads settings, opens the repository, runs `migrate()` (so it works against an older database), runs the sync, logs the summary, exits 0 (2 on config error, as today). It does not need the OpenRouter key, so `load_settings` gets a `require_secrets: bool = True` parameter; `sync` passes `False`.

**Config.** New section `SyncConfig` with `interval_s: float = Field(default=300, ge=0)`, exposed as `settings.sync`.

**Archive names stay reserved.** `path_taken` keeps checking all rows, including ones marked deleted, so a new recording never reuses a deleted recording's file names. This keeps "restored file" unambiguous.

## Risks / Trade-offs

- [User moves files to another folder to "archive" them] → They are reported deleted; restoring them to the original path clears the mark. Documented in README.
- [Sync runs while the data directory is being restored from backup] → Files appear deleted, then are cleared on the next sync once restored. Harmless, since a deleted mark only suppresses processing.
- [Periodic stat of many files on slow storage] → Default 300s interval; set `sync.interval_s: 0` to rely on startup and `crisp sync` only.
- [User deletes a recording and later wants it re-imported] → Not supported by this change (rows are kept on purpose to prevent re-ingest). Workaround: restore the audio file to its archive path.

## Migration Plan

Migration 2 (`ALTER TABLE recordings ADD COLUMN ...` twice) runs automatically on service start or `crisp sync`; existing rows get `NULL` (not deleted) and the first sync fills in reality. Rollback: older code ignores the extra columns, but would again retry recordings whose audio was deleted.
