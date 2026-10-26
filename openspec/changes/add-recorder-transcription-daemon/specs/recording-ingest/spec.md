# Spec Delta

## Purpose

Finds recordings on the connected recorder that have not been archived yet, copies them into the local dated archive, and records each one in the state database so it is processed exactly once.

## ADDED Requirements

### Requirement: Recording discovery
The service SHALL treat every regular file with a `.wav` extension (case-insensitive) directly inside the recorder's `RECORD` folder as a candidate recording. The folder name SHALL be configurable and default to `RECORD`.

#### Scenario: WAV files found
- **WHEN** the `RECORD` folder contains `REC001.WAV` and `rec002.wav`
- **THEN** both are candidate recordings

#### Scenario: Other files ignored
- **WHEN** the `RECORD` folder contains `notes.txt`
- **THEN** that file is not a candidate and is not copied

#### Scenario: RECORD folder missing
- **WHEN** the recorder has no `RECORD` folder
- **THEN** the ingest run finishes without copying anything and logs a warning

### Requirement: New recordings identified by content
The service SHALL identify a recording by the SHA-256 hash of its content. A candidate whose hash is already in the state database SHALL NOT be copied again, even if its file name differs or a previous recording used the same name.

#### Scenario: Already archived recording
- **WHEN** a candidate's content hash already exists in the state database
- **THEN** it is skipped

#### Scenario: Recorder reuses a file name
- **WHEN** the recorder presents `REC001.WAV` with content different from an earlier archived `REC001.WAV`
- **THEN** it is treated as a new recording and archived

#### Scenario: Unchanged file skipped without re-hashing
- **WHEN** a candidate's source path, size and modification time match an archived recording
- **THEN** it is skipped without reading its full content

### Requirement: Archive naming
The service SHALL copy each new recording to `data/audio_to_text/<YYYY-MM>/<YYYY>_<MM>_<DD>_time<HHMMSS>_audio.<ext>`. The timestamp SHALL be the recording start time encoded in the device file name (`YYYYMMDDHHMMSS`). If the name encodes no valid time, the file's modification time SHALL be used. Either is interpreted in the configured timezone. `<ext>` SHALL be the source extension in lower case.

#### Scenario: Start time from file name
- **WHEN** a new recording `20261025143012.WAV` has a modification time of 2026-10-25 14:35:40 local time
- **THEN** it is archived as `data/audio_to_text/2026-10/2026_10_25_time143012_audio.wav`

#### Scenario: Fallback to modification time
- **WHEN** a new recording `REC005.WAV` has a modification time of 2026-10-25 14:30:12 local time
- **THEN** it is archived as `data/audio_to_text/2026-10/2026_10_25_time143012_audio.wav`

#### Scenario: Name collision
- **WHEN** the target name is already taken by a different recording
- **THEN** a numeric suffix is added (`2026_10_25_time143012-2_audio.wav`), and the same suffix is used for that recording's transcript

### Requirement: Safe copy
The service SHALL make a recording visible in the archive only once it is fully copied and its SHA-256 hash matches the source. Partial or mismatched copies SHALL be discarded.

#### Scenario: Recorder unplugged mid-copy
- **WHEN** the recorder is disconnected while a recording is being copied
- **THEN** no partial file remains under its final archive name, and the recording is copied again on the next connection

#### Scenario: Hash mismatch
- **WHEN** the copied file's hash differs from the source hash
- **THEN** the copy is discarded, the error is logged, and the recording is retried on the next ingest run

### Requirement: Recording state tracking
The service SHALL keep one state-database record per archived recording. Each record holds the content hash, source file name, source size and modification time, archive path, ingest time, pipeline status, attempt count and last error.

#### Scenario: Recording archived
- **WHEN** a recording is successfully copied
- **THEN** a record exists for it with status `copied` and its archive path

#### Scenario: Handoff to transcription
- **WHEN** a recording reaches status `copied`
- **THEN** it becomes eligible for transcription without waiting for the rest of the ingest run to finish
