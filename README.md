# crisp-it-quick

A background service that notices when your USB voice recorder is plugged in, copies new recordings off it, and turns them into German markdown transcripts.

```
plug in recorder ──► detect (udev + polling) ──► mount read-only ──► copy new RECORD/*.WAV
                                                                         │
     data/audio_to_text/2026-10/2026_10_25_time143012_text.md ◄── cleanup (LLM) ◄── transcribe (OpenRouter)
```

- **Recorder:** Actions Semiconductor D-Wave / AK1025, matched by name or by a configured USB id.
- **Device files are never touched.** The recorder is mounted read-only and recordings stay on it.
- **Content-based dedupe:** every file's SHA-256 hash is stored in SQLite (`data/state.db`). Re-plugging never copies or transcribes a recording twice.
- **Transcription** goes through [OpenRouter](https://openrouter.ai) with any model on its audio transcription endpoint. Long recordings are split into chunks, cut at silences.
- **Optional cleanup:** an LLM pass fixes punctuation, capitalization and paragraphs. The raw transcript is always kept.

## Requirements

- Linux host with Docker and docker compose
- An OpenRouter API key

## Setup

```bash
cp config.example.yaml config.yaml   # non-secret settings, documented inline
cp .env.example .env                 # then put your key in it:
#   OPENROUTER_API_KEY=sk-or-...
docker compose up -d --build
docker compose logs -f
```

Plug in the recorder. The log shows it being detected, then each recording archived and each transcript written:

```
INFO crisp.device: recorder detected: /dev/sdb1 [10d6:....] Actions Semiconductor Co., Ltd / D-Wave ...
INFO crisp.ingest: archived REC005.WAV -> 2026-10/2026_10_25_time143012_audio.wav
INFO crisp.pipeline: transcript written: 2026-10/2026_10_25_time143012_text.md
```

To check the configuration without starting the service, run `docker compose run --rm crisp check-config`.

## Data layout

```
data/
├── state.db                          # SQLite: every recording, its status, attempts, errors
└── audio_to_text/
    └── 2026-10/
        ├── 2026_10_25_time143012_audio.wav       # byte-identical copy of the device file
        ├── 2026_10_25_time143012_text.md         # transcript
        ├── 2026_10_25_time143012-2_audio.wav     # second recording in the same second
        └── 2026_10_25_time143012-2_text.md
```

The timestamp comes from the file's modification time on the recorder, read as wall-clock time in `ingest.timezone`. **Set the recorder's clock correctly.** The original file name and time are also kept in the transcript's front matter.

A transcript looks like this:

```markdown
---
recorded_at: '2026-10-25T14:30:12+01:00'
source_file: REC005.WAV
audio_file: 2026_10_25_time143012_audio.wav
duration_s: 63.4
transcription_provider: openrouter
transcription_model: openai/whisper-large-v3
cleanup_model: deepseek/deepseek-v4-flash-0731
created_at: '2026-10-25T15:00:00+00:00'
---

# Recording 2026-10-25 14:30:12

Cleaned transcript …

## Raw transcript

raw transcript …
```

Once written, a transcript is never overwritten, so you can edit it freely.

### Deleting files by hand

You can delete `_audio.wav` and `_text.md` files from the archive at any time. The service checks the archive at startup and every `sync.interval_s` seconds (default 300; `0` turns the periodic check off), and records each missing file in the state database (`audio_deleted_at`, `text_deleted_at`). To check right away, run `docker compose run --rm crisp sync`.

- A recording whose audio you deleted is not transcribed, and a deleted transcript is not written again.
- Deleted recordings stay known, so they are **not copied again** from the recorder, even if it still holds the original WAV.
- To undo a deletion, put the file back at its original path; the next check clears the mark. A file moved to another folder counts as deleted.
- If the whole `audio_to_text` folder is missing (e.g. a drive is not mounted), the check changes nothing and logs a warning.

To list deleted files:

```bash
sqlite3 data/state.db "select audio_path, audio_deleted_at, text_deleted_at from recordings where audio_deleted_at is not null or text_deleted_at is not null"
```

## Choosing models

Set them in `config.yaml`, then run `docker compose restart`:

```yaml
transcription:
  model: openai/whisper-large-v3     # or mistralai/voxtral-mini-transcribe-3-0,
                                     #    mistralai/voxtral-small-transcribe-3-0
cleanup:
  enabled: true
  model: deepseek/deepseek-v4-flash-0731
```

Try a few transcription models on your own recordings; German accuracy differs between them.

## Failures and retries

- **Network errors, timeouts, HTTP 429 and 5xx** are retried with backoff (`openrouter.request_retries`).
- **Bad keys (401/403) and invalid requests (400)** fail straight away, with a CRITICAL or ERROR log line.
- **A failed recording** is retried on the next start, up to `transcription.max_attempts` times in total. Recordings that can't be decoded are not retried.
- **Interrupted work resumes on restart.** If transcription already finished, the paid speech-to-text call is not repeated.
- **If cleanup fails**, the transcript is still written with the raw text.

To list recordings that gave up:

```bash
sqlite3 data/state.db "select audio_path, attempts, last_error from recordings where status='failed'"
```

## Security and privacy

- **Audio leaves your machine.** Recordings are sent to OpenRouter and the upstream provider of the chosen model (e.g. OpenAI, Mistral). Don't use this for recordings that must stay local; a local provider is planned.
- **The container runs `privileged` with host networking.** That's the price of seeing USB hotplug events and mounting the device from inside a container. The recorder itself is mounted `ro,noexec,nosuid,nodev`.
- **Secrets live only in `.env`.** It is git-ignored, and the key is redacted from all log output.

If you prefer your desktop's automount, mount the host path into the container and set `device.mount_path_override` to it.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest          # needs ffmpeg on PATH
.venv/bin/ruff check .
.venv/bin/crisp --config config.yaml run   # run on the host (mounting needs root)
```

The design and specs are in `openspec/changes/add-recorder-transcription-daemon/`.
