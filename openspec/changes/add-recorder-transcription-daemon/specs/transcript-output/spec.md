# Spec Delta

## Purpose

Defines the markdown transcript file produced for each recording: where it lives, how it is named, and what it contains, so transcripts form a browsable, dated archive next to their audio.

## ADDED Requirements

### Requirement: Transcript location and name
The service SHALL write each transcript to `<DD>_<HHMMSS>_text.md`, in the same `data/audio_to_text/<YYYY-MM>/` folder as its archived audio and with the same timestamp and collision suffix.

#### Scenario: Transcript next to audio
- **WHEN** `data/audio_to_text/2026-10/25_143012_audio.wav` is transcribed
- **THEN** the transcript is written to `data/audio_to_text/2026-10/25_143012_text.md`

### Requirement: Transcript content structure
The transcript file SHALL begin with YAML front matter containing: recording datetime, source file name, archived audio file name, duration in seconds, transcription provider and model, cleanup model (or none), and the time the transcript was created. A body heading with the recording date and time SHALL follow. Then comes the cleaned transcript, if any, and finally a `## Raw transcript` section with the raw text.

#### Scenario: Cleanup succeeded
- **WHEN** a recording was transcribed and cleaned
- **THEN** the file contains front matter, a heading, the cleaned text, and a `## Raw transcript` section with the raw text

#### Scenario: No cleaned text
- **WHEN** cleanup is disabled, or cleanup failed
- **THEN** the file contains front matter, a heading and the raw text, with no `## Raw transcript` section, and the front matter cleanup field is `none`

#### Scenario: Empty transcription
- **WHEN** the provider returns no text, for example for a silent recording
- **THEN** a transcript file is still written, with the body stating that no speech was detected

### Requirement: Atomic and final writes
The service SHALL write transcript files atomically, so a partial file is never visible. Once a recording is marked `done`, the service SHALL NOT overwrite its transcript file.

#### Scenario: Write completes
- **WHEN** a transcript file is written
- **THEN** the file appears with its complete contents, and the recording is marked `done`

#### Scenario: User edited a transcript
- **WHEN** a user edits a `_text.md` file of a `done` recording and the service restarts
- **THEN** the edited file is left unchanged
