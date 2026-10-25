# Spec Delta

## Purpose

Converts each archived recording into raw transcript text through a configurable speech-to-text provider, reliably and without losing work across failures or restarts.

## ADDED Requirements

### Requirement: Configurable transcription provider and model
The service SHALL transcribe through the provider and model named in configuration. v1 SHALL support the provider `openrouter` with any model available on OpenRouter's audio transcription endpoint. The transcription language SHALL be configurable and default to German (`de`).

#### Scenario: Model selected in config
- **WHEN** config sets provider `openrouter` and model `mistralai/voxtral-small-transcribe-3-0`
- **THEN** recordings are transcribed with that model, and the model name is recorded with the transcript

#### Scenario: Unknown provider
- **WHEN** config names a provider that is not supported
- **THEN** the service refuses to start and reports which providers are supported

### Requirement: Audio normalization before transcription
The service SHALL convert each recording to 16 kHz mono in a format the provider accepts before sending it. The archived original SHALL remain unchanged.

#### Scenario: Compressed WAV from the recorder
- **WHEN** a recording is an IMA-ADPCM-encoded WAV file
- **THEN** it is transcribed successfully, and the archived file is byte-identical to the device copy

#### Scenario: Unreadable audio
- **WHEN** a recording cannot be decoded
- **THEN** it is marked `failed` with a decode error and not retried automatically

### Requirement: Long recordings are chunked
The service SHALL split recordings longer than the configured chunk length into consecutive segments, transcribe each one, and join the results in order. The chunk length SHALL be configurable.

#### Scenario: Recording longer than one chunk
- **WHEN** a 25-minute recording is transcribed with a 10-minute chunk length
- **THEN** three segments are transcribed and their texts are joined in recording order into one transcript

#### Scenario: One chunk fails permanently
- **WHEN** any chunk still fails after all retries
- **THEN** the whole recording is marked `failed` and no partial transcript is written as final

### Requirement: Retry with backoff
The service SHALL retry transient provider errors (network errors, timeouts, HTTP 429 and 5xx) with exponential backoff, up to a configurable number of attempts. Authentication and validation errors (HTTP 400, 401, 403) SHALL NOT be retried.

#### Scenario: Rate limited
- **WHEN** the provider responds with HTTP 429
- **THEN** the request is retried after a backoff delay

#### Scenario: Invalid API key
- **WHEN** the provider responds with HTTP 401
- **THEN** the recording is marked `failed` with an authentication error, and the error is logged prominently

### Requirement: Work survives restarts
At startup, the service SHALL resume every recording whose status is `copied` or `transcribing`. Recordings in `failed` SHALL be retried only while their attempt count is below the configured maximum.

#### Scenario: Restart during transcription
- **WHEN** the service stops while a recording is in `transcribing`
- **THEN** after restart the recording is transcribed again from the start

#### Scenario: Retry limit reached
- **WHEN** a failed recording's attempt count has reached the configured maximum
- **THEN** it is not retried automatically and stays `failed`

### Requirement: Transcription runs independently of the device
Transcription SHALL work from the archived copy only and SHALL NOT require the recorder to still be connected.

#### Scenario: Recorder unplugged after copy
- **WHEN** the recorder is disconnected after recordings were archived
- **THEN** their transcription continues and completes
