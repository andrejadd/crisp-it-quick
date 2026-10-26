# Tasks

## 1. Database

- [x] 1.1 Add migration 2 to `src/crisp/db.py` adding nullable `audio_deleted_at` and `text_deleted_at` columns to `recordings`, and the matching fields on `Recording`. Verify with unit tests in `tests/test_db.py`: a fresh DB reaches version 2, and a version-1 DB with rows migrates to version 2 with both columns `NULL` and rows intact.
- [x] 1.2 Add repository methods to mark and clear the audio/transcript deleted state, writing only when the state changes and keeping an existing detection time. Verify with unit tests: marking twice keeps the first timestamp, clearing resets to `NULL`, and each call reports whether it changed anything.
- [x] 1.3 Exclude recordings with `audio_deleted_at` set from `resumable_ids`. Verify with a unit test: a `copied` recording with deleted audio is not returned, an otherwise identical one is.

## 2. Sync logic (archive-sync)

- [x] 2.1 Add `src/crisp/sync.py` with `sync_archive(repo, archive_dir) -> SyncResult` (newly deleted / restored counts): check audio for every row and transcript only for `done` rows, mark or clear accordingly, log each change at INFO with its archive path and a no-change run at DEBUG. Verify with `tests/test_sync.py`: deleted audio, deleted transcript, missing transcript on a `copied` row not marked, restored file cleared, unchanged run performs no writes.
- [x] 2.2 Add the archive directory guard: when `archive_dir` is missing or cannot be listed, log a warning and change nothing. Verify with a test in `tests/test_sync.py` that removes the archive directory and asserts no row is marked.

## 3. Pipeline (archive-sync)

- [x] 3.1 In `Pipeline.process`, return early for recordings whose audio is marked deleted, and when a transcription is needed but the audio file is missing, mark the audio deleted and return without calling `start_attempt` or logging an error. Verify with tests in `tests/test_pipeline.py`: status and attempt count unchanged, audio marked deleted, transcriber not called.
- [x] 3.2 Add a test that a `done` recording with its transcript marked deleted is not rewritten by `process` or by service resume. Verify the test passes and the transcript file stays absent.

## 4. Configuration and CLI

- [x] 4.1 Add `SyncConfig` (`interval_s`, default 300, `>= 0`) as `settings.sync`, and document it in `config.example.yaml`. Verify with `tests/test_config.py`: the default applies, a negative value is rejected, and `config.example.yaml` still loads.
- [x] 4.2 Add a `require_secrets` parameter to `load_settings` and a `crisp sync` subcommand that migrates the database, runs one sync, logs the summary and exits 0. Verify with a CLI test: running `main(["--config", ..., "sync"])` without `OPENROUTER_API_KEY` marks a deleted file and returns 0.

## 5. Service wiring

- [x] 5.1 In `Service.run`, run the sync (via `asyncio.to_thread`) before `enqueue_resumable()`, and start a periodic sync task when `sync.interval_s > 0` that stops with the service and logs (but survives) sync errors. Verify with tests in `tests/test_service.py`: a recording whose audio was deleted while the service was stopped is not resumed; with a short interval, a file deleted while running gets marked.
- [x] 5.2 Document archive sync in `README.md`: deleting files by hand is safe, deleted recordings are not re-imported or reprocessed, restoring a file to its original path clears the mark, `crisp sync`, and `sync.interval_s`. Verify the README commands match the CLI's `--help` output.

## 6. Integration check

- [x] 6.1 Run the full test suite and `ruff check`. Verify both pass.
