# Proposal

## Why

Users prune the archive by hand (deleting `_audio.wav` or `_text.md` files under `data/audio_to_text/`). The state database never learns about this: it keeps pointing at files that no longer exist, a pending recording whose audio was deleted is retried until it exhausts its attempts with a confusing decode error, and there is no record that the deletion was intentional. The database should reflect what is actually on disk.

## What Changes

- Add an archive sync that compares every recording in the state database with the files in the archive directory and records, per recording, whether its audio file and its transcript file have been deleted (and when).
- Run the sync at service startup, periodically while the service runs (configurable interval), and on demand through a new `crisp sync` CLI command.
- If a file marked deleted reappears at its archive path, clear the deleted mark.
- Recordings whose audio has been deleted are not transcribed: they are excluded from resume at startup, and the pipeline skips (and marks) them instead of failing when the audio is missing.
- A deleted transcript of a finished recording is not regenerated.
- Deleted recordings stay in the database, so the content-hash check keeps them from being copied again when the recorder (which still holds the original) is next connected.
- Safety guard: if the archive directory itself is missing or unreadable, the sync does nothing rather than marking every recording deleted.
- New config section `sync` with `interval_s` (default 300, `0` disables the periodic run).
- Schema migration 2 adds `audio_deleted_at` and `text_deleted_at` columns to `recordings`.

## Capabilities

### New Capabilities
- `archive-sync`: Reconciling the state database with the files present in the archive directory, recording manual deletions, and how deleted files affect ingest and processing.

### Modified Capabilities
<!-- None: openspec/specs/ has no archived capabilities yet. The interaction with ingest
     de-duplication and pipeline resume is specified in archive-sync. -->

## Impact

- `src/crisp/db.py`: migration 2, new `Recording` fields, sync queries, resume query excludes recordings with deleted audio.
- New `src/crisp/sync.py`: the reconciliation logic.
- `src/crisp/pipeline.py`: check audio exists before transcribing.
- `src/crisp/service.py`: run sync at startup and periodically.
- `src/crisp/cli.py`: `sync` subcommand.
- `src/crisp/config.py`, `config.example.yaml`, `README.md`: `sync.interval_s`.
- Tests: `tests/test_db.py`, new `tests/test_sync.py`, `tests/test_pipeline.py`, `tests/test_service.py`.
- Existing databases are migrated in place on next start; no data is lost.
