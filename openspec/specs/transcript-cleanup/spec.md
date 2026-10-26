# transcript-cleanup Specification

## Purpose
Optionally improves the readability of a raw transcript, fixing punctuation, capitalization and paragraphing with an LLM, while preserving the spoken content.

## Requirements
### Requirement: Optional cleanup step
The service SHALL run cleanup on every raw transcript when cleanup is enabled in configuration, and skip it when cleanup is disabled. The provider and model SHALL be configurable. v1 SHALL support OpenRouter chat models.

#### Scenario: Cleanup enabled
- **WHEN** cleanup is enabled with an OpenRouter model configured
- **THEN** each raw transcript is sent to that model, and the result is stored as the cleaned transcript

#### Scenario: Cleanup disabled
- **WHEN** cleanup is disabled
- **THEN** no cleanup request is made, and the transcript output contains only the raw transcript

### Requirement: Content-preserving instructions
The cleanup request SHALL tell the model to fix punctuation, capitalization and paragraph breaks only, keeping the original language and wording, and to return only the cleaned text. The instruction text SHALL be overridable in configuration.

#### Scenario: Default instructions
- **WHEN** no custom cleanup prompt is configured
- **THEN** the built-in prompt tells the model not to summarize, translate, add or drop content

#### Scenario: Custom instructions
- **WHEN** a custom cleanup prompt is configured
- **THEN** that prompt is used instead of the built-in one

### Requirement: Cleanup failure does not block output
If cleanup fails after its retries, the service SHALL still produce the transcript output with the raw transcript, and SHALL record the cleanup failure.

#### Scenario: Cleanup model unavailable
- **WHEN** the cleanup request still fails after retries
- **THEN** the transcript file is written with the raw transcript, the recording is marked `done`, and the cleanup error is logged and stored on the record

#### Scenario: Implausible cleanup output
- **WHEN** the cleaned text is less than half or more than twice the length of the raw transcript
- **THEN** the cleaned text is discarded as unreliable, and the output uses the raw transcript
