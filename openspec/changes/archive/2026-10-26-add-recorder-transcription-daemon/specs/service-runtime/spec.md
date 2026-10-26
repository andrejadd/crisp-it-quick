# Spec Delta

## Purpose

Defines how the transcription daemon is deployed, configured and observed: docker compose, a non-secret config file plus a secret env file, persistent state and logs.

## ADDED Requirements

### Requirement: Docker compose deployment
The service SHALL run as a docker compose service that restarts automatically unless stopped, and that persists all data under a host-mounted `data/` directory.

#### Scenario: Start with compose
- **WHEN** the user runs `docker compose up -d` with a valid `config.yaml` and `.env`
- **THEN** the daemon starts, waits for the recorder, and keeps running in the background

#### Scenario: Host reboot
- **WHEN** the host restarts
- **THEN** the service comes back up automatically, and pending work resumes from the state database

### Requirement: Non-secret configuration in config.yaml
The service SHALL read all non-secret settings from `config.yaml`. These cover device matching, the recordings folder name, polling interval, timezone, data directory, transcription provider/model/language/chunk length/retries, and cleanup enablement/model/prompt. A documented example file SHALL be provided.

#### Scenario: Invalid configuration
- **WHEN** `config.yaml` is missing a required field or contains an invalid value
- **THEN** the service exits at startup with a message naming the offending field

#### Scenario: Defaults applied
- **WHEN** an optional setting is omitted
- **THEN** its documented default is used

### Requirement: Secrets only in .env
The service SHALL read secrets, including `OPENROUTER_API_KEY`, only from the environment, which is populated from `.env`. Secrets SHALL NOT appear in `config.yaml`, in logs, or in transcript files. An example `.env.example` without real values SHALL be provided, and `.env` SHALL be excluded from version control.

#### Scenario: Missing API key
- **WHEN** a configured provider needs `OPENROUTER_API_KEY` and it is not set
- **THEN** the service exits at startup with a message naming the missing variable

#### Scenario: Logging requests
- **WHEN** the service logs a provider request or error
- **THEN** the API key does not appear in the log output

### Requirement: Persistent state database
The service SHALL keep its state in a SQLite database inside the data directory. The database SHALL be created automatically on first start and migrated automatically when its schema version is older than the service expects.

#### Scenario: First start
- **WHEN** the service starts and no database exists
- **THEN** it creates the database with the current schema

#### Scenario: Data preserved across upgrades
- **WHEN** the service is upgraded and restarted
- **THEN** existing recording records are kept, and already-processed recordings are not processed again

### Requirement: Operational logging
The service SHALL write logs to stdout, with a configurable level. Logs SHALL cover device detection and removal, each archived recording, each transcription start/finish/failure with duration, and startup configuration with secrets redacted.

#### Scenario: Following progress
- **WHEN** the user runs `docker compose logs -f` while the recorder is connected
- **THEN** they see the recorder detected, each new recording archived, and each transcript written
