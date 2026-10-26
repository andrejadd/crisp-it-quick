# Spec Delta

## Purpose

Keeps the state database consistent with the audio and transcript files actually present in the archive directory, so that files the user deletes by hand are recorded as deleted and are neither re-ingested nor reprocessed.

## ADDED Requirements

### Requirement: Deleted file detection
An archive sync SHALL check, for every recording in the state database, whether its audio file exists at its archive path, and, for recordings with status `done`, whether its transcript file exists. A missing file SHALL be recorded as deleted together with the time the sync detected it. The recording record itself SHALL be kept.

#### Scenario: Audio file deleted
- **WHEN** the user deletes `2026-10/2026_10_25_time143012_audio.wav` and a sync runs
- **THEN** that recording's audio is marked deleted with the detection time, and its record remains in the database

#### Scenario: Transcript deleted
- **WHEN** the user deletes the `_text.md` file of a recording with status `done` and a sync runs
- **THEN** that recording's transcript is marked deleted with the detection time

#### Scenario: Transcript not yet written
- **WHEN** a recording with status `copied` or `transcribing` has no transcript file yet
- **THEN** its transcript is not marked deleted

#### Scenario: Already marked
- **WHEN** a file already marked deleted is still missing at the next sync
- **THEN** its original detection time is kept

### Requirement: Restored files
If a file marked deleted is present at its archive path again during a sync, the sync SHALL clear its deleted mark.

#### Scenario: Audio put back
- **WHEN** the user restores a deleted audio file to its original archive path and a sync runs
- **THEN** that recording's audio is no longer marked deleted

### Requirement: Missing archive directory guard
If the archive directory does not exist or cannot be listed, the sync SHALL change nothing and SHALL log a warning, so that an unmounted or moved data directory is not mistaken for deletion of every file.

#### Scenario: Archive directory unavailable
- **WHEN** the archive directory is missing at sync time
- **THEN** no recording is marked deleted and a warning is logged

### Requirement: Sync triggers
The service SHALL run an archive sync at startup before resuming unfinished recordings, and periodically while running at the configured `sync.interval_s` (default 300 seconds; `0` disables periodic sync). A `crisp sync` command SHALL run a single sync and exit, and SHALL be safe to run while the service is running.

#### Scenario: Startup sync
- **WHEN** the service starts after the user deleted files while it was stopped
- **THEN** those files are marked deleted before any recording is resumed

#### Scenario: Periodic sync
- **WHEN** the user deletes a file while the service runs with `sync.interval_s: 300`
- **THEN** the deletion is recorded within about 300 seconds

#### Scenario: Manual sync
- **WHEN** the user runs `crisp sync`
- **THEN** a sync runs once, a summary of newly deleted and restored files is printed to the log, and the command exits with status 0

### Requirement: Sync logging
Each newly detected deletion and each restoration SHALL be logged at INFO level with the file's archive path. A sync that finds no changes SHALL log at DEBUG level only.

#### Scenario: Deletion logged
- **WHEN** a sync detects a newly deleted transcript
- **THEN** an INFO log line names the transcript's archive path

### Requirement: No processing without audio
A recording whose audio is marked deleted SHALL NOT be transcribed or resumed. If processing finds the audio file missing, the recording's audio SHALL be marked deleted and processing SHALL stop without counting a failed attempt.

#### Scenario: Pending recording deleted before transcription
- **WHEN** a recording with status `copied` has its audio deleted and the service restarts
- **THEN** it is not resumed and no transcription is attempted

#### Scenario: Audio missing when processing starts
- **WHEN** a queued recording's audio file is missing at the time it is processed
- **THEN** its audio is marked deleted, its status and attempt count are unchanged, and no error is logged

### Requirement: No regeneration of deleted transcripts
The service SHALL NOT recreate a transcript file that is marked deleted.

#### Scenario: Transcript stays deleted
- **WHEN** a `done` recording's transcript is marked deleted and the service restarts
- **THEN** no transcript file is written for it

### Requirement: Deleted recordings are not re-ingested
A recording whose files are marked deleted SHALL still count as already archived when the recorder is connected, so it is not copied again.

#### Scenario: Recorder still holds a deleted recording
- **WHEN** the user deleted a recording's audio and transcript, and the recorder still holds the original WAV
- **THEN** connecting the recorder does not copy that recording again
