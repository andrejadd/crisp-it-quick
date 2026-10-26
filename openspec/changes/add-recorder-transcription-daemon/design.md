# Design

## Context

The project is greenfield: there is no code yet. Motivation and scope are in `proposal.md`; behavior is in `specs/`. The constraints that shape this design:

- **Container, host device.** The daemon runs in a container but must see host USB hotplug events and mount a host block device.
- **Recorder hardware.** The recorder is a cheap MP3/MP4 player with a FAT filesystem, a `RECORD/` folder and `.WAV` files. Its exact USB `vid:pid` and WAV encoding (PCM or IMA-ADPCM) are not confirmed yet.
- **OpenRouter audio endpoint.** `POST https://openrouter.ai/api/v1/audio/transcriptions` takes JSON with `model` and `input_audio: {data: <base64>, format: "wav"|"mp3"|"flac"|…}`, and returns JSON with `text` and `usage`. It has a reported ~60 s upstream timeout and no audio URLs.
- **No local speech-to-text in v1.** Ollama can't serve Whisper-family models.

## Goals / Non-Goals

**Goals:**
- A single-process daemon, simple enough to read in one sitting.
- Ingest finishes quickly while the device is plugged in. Transcription runs afterwards, independently of the device.
- The provider interface makes adding local backends (Speaches, Ollama-hosted audio LLMs, Groq) a new adapter plus a config entry.
- Every failure state can be recovered from the SQLite state alone.

**Non-Goals:**
- Running several daemon instances, or running across machines.
- A web UI or API. The files on disk are the interface.
- Handling more than one recorder model. The matching rules are configurable, but only this device is tested.

## Decisions

### D1: Python 3.12, a single asyncio process
One process with three asyncio tasks: a **device watcher**, an **ingest runner** and a **transcription worker**, connected by an `asyncio.Queue` of recording IDs. Blocking work (hashing, copying, ffmpeg, pyudev) goes through `asyncio.to_thread`.
- *Alternatives:* separate containers for ingest and transcription. Rejected: more moving parts, and SQLite shared across containers adds nothing at this scale. A Celery/Redis queue was rejected for the same reason.

### D2: Device detection with pyudev and polling
- **Hotplug:** a `pyudev.Monitor` filtered on `subsystem=block`, `DEVTYPE=partition|disk`. Each event's parent USB device is matched on `ID_VENDOR*`/`ID_MODEL*` properties, or the configured `vid:pid` (spec: Recorder identification).
- **Startup and polling:** the same matcher runs over `pyudev.Context().list_devices(subsystem="block")` at startup and every `device.poll_interval_s` seconds (default 30).
- **Container access:** the container mounts the host's `/run/udev:ro` and `/dev`, and uses `network_mode: host`. udev event broadcasts use a netlink socket that is only visible from the host network namespace.
- **Debouncing:** detection is debounced. A device must be present and its partition readable for 2 s before an ingest run starts, because USB mass storage often emits several events.
- *Alternative:* polling `/sys/bus/usb` only. Kept as the fallback rather than the main path, for latency.

### D3: Mount in the container's own namespace, read-only
On detection the service checks `/proc/self/mountinfo` for the partition. If it isn't mounted, it runs `mount -o ro,noexec,nosuid,nodev <dev> /mnt/recorder`; the filesystem type comes from udev `ID_FS_TYPE`, usually `vfat`. The host may have auto-mounted the same partition at `/media/...`. That mount is in another mount namespace and does no harm: the kernel reuses the same superblock, and our view is read-only. On removal, or after the ingest run, the service lazily unmounts its own mount (`umount -l`), and only that one.

This needs the `CAP_SYS_ADMIN` capability plus access to the block device, so the compose service runs `privileged: true`.
- *Alternative:* relying on the host's auto-mount and bind-mounting `/media` into the container (`rslave`). Rejected as the main path: it depends on the desktop session and doesn't work headless. A configured `device.mount_path_override` covers users who prefer it.

### D4: Identity and dedupe
- **Primary identity:** the SHA-256 of the content (spec: New recordings identified by content).
- **Fast path:** the tuple `(source_name, size, mtime)` is indexed. A candidate matching an existing record is skipped without hashing, which makes re-plugging cheap when the recorder holds hundreds of files.
- **Copy:** stream the file to `<target>.part`, computing the hash while copying. Then `fsync`, compare the hash with a second read of the source, and finally `os.replace` to the final name.

The second read happens only for new files, so it is cheap. It catches FAT read errors on flaky USB connections.

### D5: Naming and time source
The archive timestamp is the recording start time, which the D-Wave recorder encodes in the file name (`YYYYMMDDHHMMSS.WAV`). The file's mtime is the recording *end*, so it is only a fallback for names that don't parse to a valid date. Both are read as wall-clock time in `timezone` from config (default `Europe/Berlin`). FAT stores local time without a zone, so the configured zone makes the interpretation explicit. The recorder clock may be wrong. The original device file name and mtime are stored in the DB and in the transcript front matter, so names can be corrected later.

If a target name is taken, the suffix goes before the role: `YYYY_MM_DD_timeHHMMSS-2_audio.wav` and `YYYY_MM_DD_timeHHMMSS-2_text.md`.

### D6: State model
Table `recordings`: `id, sha256 UNIQUE, source_name, source_size, source_mtime, recorded_at, audio_path, text_path, duration_s, status, attempts, last_error, stt_provider, stt_model, cleanup_model, cleanup_error, created_at, updated_at`. Table `schema_version`.

Status flow: `copied → transcribing → transcribed → cleaned → done`, or `failed` from any step. If cleanup is disabled or fails, the flow goes from `transcribed` directly to `done`.

The raw and cleaned text are stored in a side table `transcripts(recording_id, raw_text, cleaned_text)`. That way a restart after transcription doesn't repeat the paid speech-to-text call when only cleanup or the file write is left.

Migrations are plain numbered SQL scripts applied at startup. SQLite runs with `journal_mode=WAL`.

### D7: Provider interface
```
class Transcriber(Protocol):
    name: str
    async def transcribe(self, audio: bytes, fmt: str, language: str) -> TranscriptChunk
class TextRefiner(Protocol):
    async def refine(self, text: str, prompt: str) -> str
```
A registry maps config `provider` names to factories. v1 registers `openrouter` for both. A future `openai_compatible` transcriber (Speaches, Groq, Mistral direct) would differ from it only in its multipart request body.

### D8: Audio preparation and chunking
ffmpeg converts the archived file to 16 kHz mono FLAC (`-ac 1 -ar 16000 -c:a flac`). FLAC is lossless, about half the size of PCM, accepted by OpenRouter, and decodes ADPCM input transparently.

Chunks are cut every `transcription.chunk_seconds` seconds (default **120**, set conservatively against the 60 s upstream timeout), each with **1 s of overlap**. To avoid splitting words, the cut point moves to the quietest point within ±5 s of the target: ffmpeg's `silencedetect` runs once per file. Chunk texts are joined with a single space or newline. Overlap duplication is accepted in v1 as minor; the cleanup step usually smooths it out.

Chunks are transcribed sequentially, so ordering is trivial and rate limits are friendly.

### D9: HTTP client and retries
`httpx.AsyncClient` with a 120 s timeout. Retries use `tenacity`-style exponential backoff (base 2 s, cap 60 s, with jitter) for network errors, 408/429/5xx and timeouts. `Retry-After` is honored. 400/401/403 fail immediately (spec: Retry with backoff).

`max_attempts` per recording (default 3) counts whole-pipeline attempts and is stored in `attempts`.

### D10: Cleanup prompt and guard
The default system prompt is in German and asks for punctuation, capitalization and paragraphs only: no summarizing, no translation, output only the text. `temperature=0`.

Long transcripts are cleaned in pieces of about 6,000 characters, split on sentence boundaries, so the output-token limit is never reached. The pieces are concatenated afterwards. The length-ratio guard from the spec (0.5×–2×) is applied per piece.

### D11: Configuration loading
`config.yaml` is validated with pydantic models, giving clear error paths for the spec's "Invalid configuration" scenario. Secrets come from `os.environ` via pydantic `SecretStr`, so they are redacted in `repr` and logs. The path is `/app/config.yaml` inside the container, bind-mounted read-only from the project root.

Example sections: `device`, `ingest`, `paths`, `transcription`, `cleanup`, `logging`.

### D12: Compose layout
```yaml
services:
  crisp:
    build: .
    restart: unless-stopped
    privileged: true
    network_mode: host
    env_file: .env
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data
      - /run/udev:/run/udev:ro
      - /dev:/dev
```
The image is `python:3.12-slim` plus `ffmpeg`, `util-linux` (mount), `dosfstools` and `libudev1`. The process runs as root inside the container because mounting requires it. `data/` files are written with the uid/gid from config (`PUID`/`PGID`, default 1000), so they stay user-owned on the host.

## Risks / Trade-offs

- **Privileged container:** broad host access → keep the image minimal, mount the device only read-only with `noexec,nosuid,nodev`, and document the trade-off in the README. A finer-grained `cap_add: SYS_ADMIN` plus `devices` cgroup rules is a possible later hardening.
- **Wrong recorder clock** produces misleading file names → the original name and mtime are kept in the DB and front matter. Renaming tools can come later.
- **OpenRouter upstream timeout or size limits** on long files → conservative chunk length (configurable). A failed chunk fails the recording rather than producing a silently partial transcript.
- **Words duplicated at chunk overlaps** → small and mostly hidden by cleanup. A smarter overlap merge can be added if it turns out to be noticeable.
- **Privacy:** audio goes to OpenRouter and its upstream providers → stated in the README. A local provider is the planned mitigation, out of v1.
- **The cleanup LLM could alter meaning** → strict prompt, `temperature=0`, length guard, and the raw transcript always kept in the file.
- **udev events not visible without host networking** → polling fallback (D2) guarantees eventual detection even if event delivery fails.

## Migration Plan

This is a new deployment with nothing to migrate:
1. Copy `config.example.yaml` → `config.yaml` and `.env.example` → `.env`, then set `OPENROUTER_API_KEY`.
2. Run `docker compose up -d --build`.
3. Plug in the recorder and watch `docker compose logs -f`.

Rollback is `docker compose down`. `data/` stays intact.

## Open Questions

- ~~**Exact USB `vid:pid` and WAV encoding.**~~ Resolved on the first real connection: `10d6:1101`, with a single vfat partition. udev reports the block device as "USB DISK / FlashDisk"; the usb.ids names on the parent USB device match. Recordings are PCM s16le, 16 kHz, stereo. File names encode the recording *start* (`YYYYMMDDHHMMSS.WAV`), while the mtime is the recording *end*.
- **Typical recording length.** It decides whether the 120 s default chunk can safely be raised. Tune after the first real runs; it's only a config change.
- **Which transcription model wins for German** (whisper-large-v3 vs Voxtral Mini/Small Transcribe 3). Pick by trying them on real recordings; switching is a config change. A `compare` command is planned for later.
- ~~**Exact OpenRouter slug for the cleanup model**~~ Default `deepseek/deepseek-v4-flash-0731` (the GA release).
