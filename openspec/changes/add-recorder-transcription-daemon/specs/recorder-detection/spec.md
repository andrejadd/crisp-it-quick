# Spec Delta

## Purpose

Detects when the handheld voice recorder is connected over USB and makes its filesystem available to the service read-only, so new recordings can be ingested without manual steps.

## ADDED Requirements

### Requirement: Recorder identification
The service SHALL identify the recorder by its USB identity. By default it SHALL match the vendor "Actions Semiconductor" together with a product name containing "D-Wave" or "AK1025". When a USB `vendor_id:product_id` pair is configured, that pair SHALL take precedence over name matching.

#### Scenario: Recorder matched by name
- **WHEN** a USB storage device is connected whose vendor name contains "Actions Semiconductor" and whose product name contains "AK1025"
- **THEN** the service treats it as the recorder

#### Scenario: Recorder matched by configured IDs
- **WHEN** `vendor_id:product_id` is configured and a device with exactly those IDs is connected
- **THEN** the service treats it as the recorder regardless of its name strings

#### Scenario: Unrelated device ignored
- **WHEN** a USB storage device that matches neither the configured IDs nor the name rules is connected
- **THEN** the service does not mount or scan it and only logs it at debug level

### Requirement: Hotplug detection
The service SHALL react to the recorder being connected while the service is running, without user interaction, and start ingestion within 15 seconds of the device's storage becoming available.

#### Scenario: Recorder plugged in while running
- **WHEN** the service is running and the recorder is connected
- **THEN** the service detects it and starts an ingest run for it

#### Scenario: Recorder unplugged
- **WHEN** the recorder is disconnected
- **THEN** the service stops any ingest run in progress for it, releases any mount it created, and logs the disconnection without crashing

### Requirement: Detection at startup and fallback polling
The service SHALL check for an already-connected recorder at startup. It SHALL also poll for the recorder at a configurable interval, so a missed hotplug event never prevents ingestion.

#### Scenario: Recorder connected before service start
- **WHEN** the service starts while the recorder is already connected
- **THEN** the service detects it and starts an ingest run

#### Scenario: Missed hotplug event
- **WHEN** a hotplug event for the recorder was not received but the recorder is connected
- **THEN** the next poll detects it and starts an ingest run

### Requirement: Read-only access to recorder storage
The service SHALL access the recorder's filesystem read-only. If the recorder's storage is not mounted for the service, the service SHALL mount it read-only. If it is already mounted, the service SHALL use the existing mount.

#### Scenario: Recorder not mounted
- **WHEN** the recorder is detected and its storage is not mounted for the service
- **THEN** the service mounts it read-only and proceeds with ingestion

#### Scenario: Recorder already mounted
- **WHEN** the recorder is detected and its storage is already mounted
- **THEN** the service uses the existing mount without mounting it again

#### Scenario: Mount fails
- **WHEN** mounting the recorder's storage fails
- **THEN** the service logs the error with the device identity, does not ingest, and retries on the next detection or poll

### Requirement: Recordings are never altered on the device
The service MUST NOT write, rename, or delete any file on the recorder.

#### Scenario: After a successful ingest
- **WHEN** an ingest run has copied all new recordings
- **THEN** every file in the recorder's `RECORD` folder is still present with unchanged content
