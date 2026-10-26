# Proposal

## Why

Recordings from a handheld voice recorder (Actions Semiconductor D-Wave / AK1025, USB-C) are currently copied off and transcribed by hand, if at all. A background service that notices when the recorder is plugged in, pulls off new recordings and turns them into German markdown transcripts removes that manual step and builds a searchable, dated archive of spoken notes.

## What Changes

- New long-running daemon, shipped as a docker compose service, that watches USB hotplug events and detects the recorder.
- Mounts the recorder read-only when it is not already mounted. If it is already mounted, the existing mount is used. Files on the device are never modified or deleted.
- Scans the device's `RECORD/` folder for `.WAV` files not seen before and copies them into `data/audio_to_text/<YYYY-MM>/<YYYY>_<MM>_<DD>_time<HHMMSS>_audio.wav`.
- SQLite database that tracks every recording: identity (content hash), source path, timestamps, pipeline status, attempts and errors.
- Transcribes each new recording through a pluggable provider interface. v1 ships a single **OpenRouter** provider (`/api/v1/audio/transcriptions`). The model is set in config, e.g. `openai/whisper-large-v3`, `mistralai/voxtral-mini-transcribe-3-0` or `mistralai/voxtral-small-transcribe-3-0`.
- Optional cleanup step: an OpenRouter chat model (configurable, e.g. a DeepSeek flash model) fixes punctuation and paragraphing of the raw transcript without changing its content.
- Writes `data/audio_to_text/<YYYY-MM>/<YYYY>_<MM>_<DD>_time<HHMMSS>_text.md` with metadata, the cleaned transcript and the raw transcript.
- Configuration split: `config.yaml` for settings that aren't secret, `.env` for secrets (`OPENROUTER_API_KEY`).

### Out of scope for v1

- **Ollama as a transcription provider.** Ollama serves LLMs and cannot run Whisper-family speech-to-text models. The provider interface is designed so a local backend can be added later as a new adapter.
- **Local Whisper server** (e.g. Speaches running whisper-large-v3 or a converted CrisperWhisper), Groq and other providers. Each one is a future adapter.
- A `compare` command that runs one recording through several models side by side.
- Speaker diarization, summaries, and any UI beyond the files on disk.

## Capabilities

### New Capabilities
- `recorder-detection`: detects the recorder on USB hotplug, at startup and through periodic polling, and makes its filesystem available read-only (mounting it if needed).
- `recording-ingest`: finds new recordings on the device, copies and verifies them into the local archive under the dated naming scheme, and records them in the state database.
- `transcription`: turns an archived recording into raw text through a pluggable provider (OpenRouter in v1), including audio normalization, chunking, retries and recovery.
- `transcript-cleanup`: optional LLM pass that improves punctuation and paragraphing of a raw transcript without changing its content.
- `transcript-output`: the markdown transcript file written next to each archived recording: its location, name and structure.
- `service-runtime`: how the daemon is deployed and configured (docker compose, `config.yaml`, `.env`), its state database lifecycle and its logging.

### Modified Capabilities
<!-- None: this is the first change in the project. -->

## Impact

- **New code**: Python 3.12 package (daemon, detection, ingest, transcription, cleanup, output, DB), `Dockerfile`, `docker-compose.yml`, `config.example.yaml`, `.env.example`, tests and a README.
- **Dependencies**: `pyudev`, `httpx`, `pydantic` / `pyyaml`, `ffmpeg` (system package in the image), SQLite (stdlib).
- **Host requirements**: Docker with compose. The container runs privileged with access to `/dev` and `/run/udev` so it can see hotplug events and mount the device.
- **External services**: OpenRouter, which needs an API key. Audio is sent to OpenRouter and its upstream providers (OpenAI / Mistral).
- **Data**: `data/` holds audio copies, transcripts and `state.db`. It must be excluded from git.
