# Tasks

## 1. Project scaffolding

- [x] 1.1 Create `pyproject.toml` (Python 3.12; deps: pyudev, httpx, pydantic, pyyaml; dev: pytest, pytest-asyncio, respx, ruff) and the package `src/crisp/` with `__main__.py`. Verify `pip install -e .[dev]` succeeds and `python -m crisp --help` runs.
- [x] 1.2 Add `Dockerfile` (python:3.12-slim + ffmpeg, util-linux, libudev1) and `docker-compose.yml` as in design D12. Verify `docker compose build` succeeds and `docker compose run --rm crisp ffmpeg -version` prints a version.
- [x] 1.3 Add `data/`, `.env` and `config.yaml` to `.gitignore`. Verify `git status` doesn't list them after creating them locally.

## 2. Configuration and logging (service-runtime)

- [x] 2.1 Implement pydantic config models for the sections `device`, `ingest`, `paths`, `transcription`, `cleanup` and `logging`, with defaults from design D11. Load `config.yaml` and read `OPENROUTER_API_KEY` from the environment as `SecretStr`. Verify with unit tests: valid config loads, a missing or invalid field names that field, defaults apply, a missing API key fails startup, and an unknown provider fails startup.
- [x] 2.2 Implement stdout logging with a configurable level and a startup config dump that redacts secrets. Verify with a unit test that the API key never appears in captured log output.
- [x] 2.3 Write `config.example.yaml` (commented, with every setting) and `.env.example`. Verify a test loads `config.example.yaml` successfully.

## 3. State database (service-runtime, recording-ingest)

- [x] 3.1 Implement the SQLite layer: WAL mode, the `schema_version` table, a numbered migration runner, and the `recordings` and `transcripts` tables from design D6. Verify with unit tests: a fresh DB is created at the current version, and re-running migrations is a no-op that preserves rows.
- [x] 3.2 Implement repository functions: lookup by sha256 and by `(source_name, size, mtime)`, insert a copied recording, status transitions with attempt counting, storing raw/cleaned text, and listing resumable records. Verify with unit tests for each function, including the `failed` retry limit.

## 4. Recorder detection and mounting (recorder-detection)

- [x] 4.1 Implement the device matcher: vendor/model name rules, plus a configured `vid:pid` that takes precedence. Verify with unit tests on fake udev property dicts: name match, ID match, unrelated device ignored.
- [x] 4.2 Implement the watcher: pyudev monitor, startup scan, periodic poll, and a 2 s debounce. It emits "recorder present"/"recorder removed" events. Verify with unit tests using an injected fake device source: startup detection, a poll catching a missed event, removal events.
- [x] 4.3 Implement mount handling: check `/proc/self/mountinfo`, mount `ro,noexec,nosuid,nodev` at `/mnt/recorder` when needed, use an existing mount as is, honor `mount_path_override`, and lazily unmount only the service's own mount. Verify with unit tests on a mocked mount command and mountinfo: the already-mounted path doesn't mount, a mount failure is logged and retried later.
- [x] 4.4 Manual hardware check: connect the recorder with the stack running. Verify detection appears in the logs within 15 s, and record the real `vid:pid` and the `ffprobe` WAV codec in `config.example.yaml` and design Open Questions.

## 5. Ingest (recording-ingest)

- [x] 5.1 Implement discovery of `*.wav` (case-insensitive) in the configured `RECORD` folder, with a warning when the folder is missing. Verify with unit tests on a temp directory: upper/lower case, other files ignored, missing folder.
- [x] 5.2 Implement archive naming: `<YYYY-MM>/<YYYY>_<MM>_<DD>_time<HHMMSS>_audio.<ext>` from the mtime in the configured timezone, with a `-N` collision suffix shared with the transcript name. Verify with unit tests: the spec example `2026_10_25_time143012_audio.wav`, collision `-2`, a DST edge case.
- [x] 5.3 Implement the safe copy: stream to `.part` while hashing, fsync, verify against a second read of the source, `os.replace`; discard on mismatch. Verify with unit tests: a successful copy matches the hash, a simulated mid-copy failure leaves no final file, and a mismatch is discarded.
- [x] 5.4 Implement the ingest run: skip by fast key, then by sha256; copy new files; insert `copied` records; enqueue each recording ID immediately; abort cleanly on device removal. Verify with an integration test on a fake mount directory: first run archives N files, a second run archives 0, a reused file name with new content is archived, and source files are unchanged (compared by hash before and after).

## 6. Transcription (transcription)

- [x] 6.1 Implement audio preparation: ffmpeg to 16 kHz mono FLAC, duration probe, and chunking with silence-aware cut points and 1 s overlap (design D8). Verify with unit tests on generated test audio (PCM WAV and IMA-ADPCM WAV made with ffmpeg in the test): chunk count for a 25-min-equivalent duration with a short chunk length, an undecodable file raises a decode error, and the archived original is byte-identical afterwards.
- [x] 6.2 Define the `Transcriber` protocol and provider registry (design D7). Implement the OpenRouter transcriber: base64 `input_audio`, model and language from config. Verify with respx-mocked tests: request shape, text extraction, model recorded.
- [x] 6.3 Implement retry/backoff: 408/429/5xx/network errors retried with `Retry-After` honored; 400/401/403 not retried. Verify with respx tests for 429 then success, repeated 503 leading to failure, and 401 failing immediately with an auth error.
- [x] 6.4 Implement the transcription step in the worker: status `transcribing`; chunks transcribed in order and joined; raw text stored and status `transcribed`; any chunk failing marks the recording `failed` with the error and increments `attempts`. Verify with a test where a mocked provider fails on chunk 2: the recording is `failed` and no transcript file exists.

## 7. Cleanup (transcript-cleanup)

- [x] 7.1 Define the `TextRefiner` protocol and implement the OpenRouter chat refiner with the default German content-preserving prompt, an optional prompt override, `temperature=0`, and splitting into ~6,000-character pieces on sentence boundaries. Verify with respx tests: the default prompt contains the no-summary/no-translation rules, the custom prompt is used when configured, and long input is split and reassembled in order.
- [x] 7.2 Implement the cleanup step: skipped when disabled; length guard (0.5×–2×) per piece; on failure keep the raw text, store `cleanup_error`, and continue. Verify with tests: disabled makes no HTTP call, implausible output falls back to raw, an error falls back to raw and the recording still reaches `done`.

## 8. Transcript output (transcript-output)

- [x] 8.1 Implement the markdown writer: YAML front matter (recorded_at, source file, audio file, duration_s, stt provider/model, cleanup model or `none`, created_at), date heading, cleaned text, `## Raw transcript` section only when cleaned text exists, and a "no speech detected" body for empty text. Write atomically and mark `done`. Verify with golden-file unit tests for the three spec scenarios.
- [x] 8.2 Ensure transcripts of `done` recordings are never rewritten. Verify with a test: edit the file, rerun the worker/restart path, and the file is unchanged.

## 9. Pipeline wiring and resilience (service-runtime, transcription)

- [x] 9.1 Wire the main loop: config → DB → watcher → ingest runner → transcription worker (asyncio tasks plus a queue). At startup, re-enqueue `copied`, `transcribing`, `transcribed` and `cleaned` records, and `failed` records below `max_attempts`. Shut down cleanly on SIGTERM. Verify with an integration test: kill during `transcribing`, restart, and the recording completes. Also check that `docker compose stop` exits within 10 s.
- [x] 9.2 Make sure transcription doesn't depend on the device. Verify with an integration test: ingest, simulate device removal, and transcription still completes.
- [x] 9.3 Write the README: purpose, requirements, setup (`config.yaml`, `.env`), `docker compose up -d`, following logs, data layout, privacy note (audio sent to OpenRouter), privileged-container trade-off, how to change models. Verify by following the README from a clean checkout to a running service.

## 10. End-to-end verification

- [x] 10.1 Run the full test suite and lint (`pytest`, `ruff check`). Verify everything is green.
- [x] 10.2 Real-device run: `docker compose up -d --build`, plug in the recorder, and confirm that new recordings appear as `_audio.wav` + `_text.md` pairs under `data/audio_to_text/<YYYY-MM>/`, that the recorder still holds every original file, and that re-plugging creates no duplicates.

## Workflow follow-up

- Try `openai/whisper-large-v3`, `mistralai/voxtral-mini-transcribe-3-0` and `mistralai/voxtral-small-transcribe-3-0` on real recordings and set the default model in `config.example.yaml`.
- Archive the change with `/opsx:archive` once implemented and verified.
