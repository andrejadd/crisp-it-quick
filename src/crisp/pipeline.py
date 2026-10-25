"""Per-recording processing: transcription -> optional cleanup -> markdown output."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from crisp import audio
from crisp.cleanup import clean_transcript
from crisp.config import Settings
from crisp.db import Recording, Repository, Status
from crisp.output import TranscriptDoc, render, write_atomic
from crisp.providers import ProviderError, TextRefiner, Transcriber

log = logging.getLogger("crisp.pipeline")


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        transcriber: Transcriber,
        refiner: TextRefiner | None,
        chown: Callable[[Path], None] = lambda _p: None,
    ):
        self.settings = settings
        self.repo = repo
        self.transcriber = transcriber
        self.refiner = refiner
        self.archive_dir = settings.paths.archive_dir
        self.chown = chown

    async def process(self, recording_id: int) -> None:
        rec = self.repo.get(recording_id)
        if rec is None or rec.status == Status.DONE:
            return
        max_attempts = self.settings.transcription.max_attempts
        if rec.status == Status.FAILED and rec.attempts >= max_attempts:
            return
        try:
            raw, cleaned = self.repo.get_texts(rec.id)
            if raw is None:
                raw = await self._transcribe(rec)
            rec = self.repo.get(rec.id)
            if rec.status != Status.CLEANED:
                cleaned = await self._cleanup(rec, raw)
            self._write_output(self.repo.get(rec.id), raw, cleaned)
        except audio.DecodeError as e:
            log.error("recording %s cannot be decoded, not retrying: %s", rec.audio_path, e)
            self.repo.mark_failed(rec.id, str(e), permanent=True, max_attempts=max_attempts)
        except ProviderError as e:
            level = logging.CRITICAL if e.kind == "auth" else logging.ERROR
            log.log(level, "transcribing %s failed (%s error): %s", rec.audio_path, e.kind, e)
            self.repo.mark_failed(rec.id, f"{e.kind}: {e}")
        except Exception as e:
            log.exception("processing %s failed", rec.audio_path)
            self.repo.mark_failed(rec.id, f"{type(e).__name__}: {e}")

    async def _transcribe(self, rec: Recording) -> str:
        cfg = self.settings.transcription
        self.repo.start_attempt(rec.id)
        src = self.archive_dir / rec.audio_path
        log.info("transcribing %s with %s/%s", rec.audio_path, self.transcriber.name,
                 self.transcriber.model)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="crisp-") as tmp:
            flac = Path(tmp) / "audio.flac"
            await asyncio.to_thread(audio.normalize, src, flac)
            duration = await asyncio.to_thread(audio.probe_duration, flac)
            silences = []
            if duration > cfg.chunk_seconds:
                silences = await asyncio.to_thread(audio.detect_silences, flac)
            segments = audio.plan_segments(duration, cfg.chunk_seconds, cfg.chunk_overlap_s,
                                           silences)
            texts = []
            for i, seg in enumerate(segments, 1):
                data = await asyncio.to_thread(
                    audio.extract_segment, flac, seg, Path(tmp) / f"seg{i}.flac"
                )
                chunk = await self.transcriber.transcribe(data, "flac", cfg.language)
                log.debug("chunk %d/%d (%.0f-%.0fs): %d chars", i, len(segments), seg.start,
                          seg.end, len(chunk.text))
                if chunk.text:
                    texts.append(chunk.text)
        raw = " ".join(texts).strip()
        self.repo.store_raw(rec.id, raw, duration_s=duration, provider=self.transcriber.name,
                            model=self.transcriber.model)
        log.info("transcribed %s: %.0fs audio, %d chunk(s), %d chars in %.1fs", rec.audio_path,
                 duration, len(segments), len(raw), time.monotonic() - started)
        return raw

    async def _cleanup(self, rec: Recording, raw: str) -> str | None:
        if self.refiner is None:
            self.repo.store_cleaned(rec.id, None, model=None, error=None)
            return None
        cfg = self.settings.cleanup
        result = await clean_transcript(self.refiner, raw, prompt=cfg.prompt,
                                        max_chars=cfg.max_chunk_chars)
        model = self.refiner.model if result.text else None
        self.repo.store_cleaned(rec.id, result.text, model=model, error=result.error)
        return result.text

    def _write_output(self, rec: Recording, raw: str, cleaned: str | None) -> None:
        doc = TranscriptDoc(
            recorded_at=datetime.fromisoformat(rec.recorded_at),
            source_file=rec.source_name,
            audio_file=Path(rec.audio_path).name,
            duration_s=rec.duration_s,
            stt_provider=rec.stt_provider or self.transcriber.name,
            stt_model=rec.stt_model or self.transcriber.model,
            cleanup_model=rec.cleanup_model,
            created_at=datetime.now(UTC),
            raw_text=raw,
            cleaned_text=cleaned,
        )
        path = self.archive_dir / rec.text_path
        write_atomic(path, render(doc))
        self.chown(path)
        self.repo.set_status(rec.id, Status.DONE)
        log.info("transcript written: %s", rec.text_path)


async def run_worker(queue: asyncio.Queue[int], pipeline: Pipeline) -> None:
    while True:
        recording_id = await queue.get()
        try:
            await pipeline.process(recording_id)
        finally:
            queue.task_done()
