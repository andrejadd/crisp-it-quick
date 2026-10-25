import os
from pathlib import Path

from crisp.db import NewRecording, Status
from crisp.pipeline import Pipeline
from crisp.providers import ProviderError, TranscriptChunk
from tests.conftest import make_audio
from tests.test_cleanup import FakeRefiner


class FakeTranscriber:
    name, model = "fake", "fake/stt"

    def __init__(self, fail_on: int | None = None, kind="transient", text="hallo welt"):
        self.calls = 0
        self.fail_on, self.kind, self.text = fail_on, kind, text

    async def transcribe(self, audio, fmt, language):
        self.calls += 1
        assert fmt == "flac" and audio[:4] == b"fLaC"
        if self.fail_on is not None and self.calls == self.fail_on:
            raise ProviderError("provider down", self.kind, 503)
        return TranscriptChunk(text=f"{self.text} {self.calls}")


def add_recording(settings, repo, seconds=5, codec="pcm_s16le", name="25_143012"):
    archive = settings.paths.archive_dir
    audio_rel = f"2026-10/{name}_audio.wav"
    (archive / "2026-10").mkdir(parents=True, exist_ok=True)
    make_audio(archive / audio_rel, seconds, codec=codec)
    return repo.insert_copied(NewRecording(
        sha256=name, source_name="REC001.WAV", source_size=1, source_mtime=1.0,
        recorded_at="2026-10-25T14:30:12+01:00", audio_path=audio_rel,
        text_path=f"2026-10/{name}_text.md",
    ))


def text_file(settings, repo, rid) -> Path:
    return settings.paths.archive_dir / repo.get(rid).text_path


async def test_happy_path_with_cleanup(settings, repo):
    rid = add_recording(settings, repo)
    p = Pipeline(settings, repo, FakeTranscriber(), FakeRefiner())
    await p.process(rid)
    rec = repo.get(rid)
    assert rec.status == Status.DONE and rec.attempts == 1
    assert rec.stt_model == "fake/stt" and rec.cleanup_model == "fake/model"
    assert 4.5 < rec.duration_s < 5.5
    md = text_file(settings, repo, rid).read_text()
    assert "Hallo welt 1" in md and "## Raw transcript\n\nhallo welt 1" in md
    assert "transcription_model: fake/stt" in md


async def test_cleanup_disabled(settings, repo):
    rid = add_recording(settings, repo)
    await Pipeline(settings, repo, FakeTranscriber(), None).process(rid)
    md = text_file(settings, repo, rid).read_text()
    assert "cleanup_model: none" in md and "## Raw transcript" not in md
    assert repo.get(rid).status == Status.DONE


async def test_cleanup_failure_still_writes_raw(settings, repo):
    rid = add_recording(settings, repo)
    await Pipeline(settings, repo, FakeTranscriber(), FakeRefiner(fail=True)).process(rid)
    rec = repo.get(rid)
    assert rec.status == Status.DONE and "down" in rec.cleanup_error
    assert "hallo welt 1" in text_file(settings, repo, rid).read_text()


async def test_chunked_recording_joined_in_order(settings, repo):
    settings.transcription.chunk_seconds = 20
    rid = add_recording(settings, repo, seconds=50)
    t = FakeTranscriber()
    await Pipeline(settings, repo, t, None).process(rid)
    assert t.calls == 3
    assert repo.get_texts(rid)[0] == "hallo welt 1 hallo welt 2 hallo welt 3"


async def test_chunk_failure_marks_failed_without_output(settings, repo):
    settings.transcription.chunk_seconds = 20
    rid = add_recording(settings, repo, seconds=50)
    await Pipeline(settings, repo, FakeTranscriber(fail_on=2), None).process(rid)
    rec = repo.get(rid)
    assert rec.status == Status.FAILED and "provider down" in rec.last_error
    assert rec.attempts == 1
    assert not text_file(settings, repo, rid).exists()
    assert repo.get_texts(rid) == (None, None)


async def test_auth_error_marked(settings, repo):
    rid = add_recording(settings, repo)
    await Pipeline(settings, repo, FakeTranscriber(fail_on=1, kind="auth"), None).process(rid)
    assert repo.get(rid).last_error.startswith("auth:")


async def test_undecodable_is_permanent(settings, repo):
    rid = add_recording(settings, repo)
    (settings.paths.archive_dir / repo.get(rid).audio_path).write_bytes(b"garbage")
    await Pipeline(settings, repo, FakeTranscriber(), None).process(rid)
    rec = repo.get(rid)
    assert rec.status == Status.FAILED
    assert rec.attempts >= settings.transcription.max_attempts
    assert rid not in repo.resumable_ids(settings.transcription.max_attempts)


async def test_adpcm_recording(settings, repo):
    rid = add_recording(settings, repo, codec="adpcm_ima_wav")
    audio = settings.paths.archive_dir / repo.get(rid).audio_path
    before = audio.read_bytes()
    await Pipeline(settings, repo, FakeTranscriber(), None).process(rid)
    assert repo.get(rid).status == Status.DONE
    assert audio.read_bytes() == before


async def test_retry_limit(settings, repo):
    settings.transcription.max_attempts = 2
    rid = add_recording(settings, repo)
    t = FakeTranscriber(fail_on=1)
    p = Pipeline(settings, repo, t, None)
    await p.process(rid)
    t.calls, t.fail_on = 0, 1
    await p.process(rid)
    assert repo.get(rid).attempts == 2
    t.fail_on = None
    await p.process(rid)  # limit reached: not attempted again
    assert repo.get(rid).status == Status.FAILED and t.calls == 1


async def test_resume_after_transcription_skips_paid_call(settings, repo):
    rid = add_recording(settings, repo)
    repo.start_attempt(rid)
    repo.store_raw(rid, "schon transkribiert", duration_s=5, provider="fake", model="fake/stt")
    t = FakeTranscriber()
    await Pipeline(settings, repo, t, None).process(rid)
    assert t.calls == 0
    assert "schon transkribiert" in text_file(settings, repo, rid).read_text()


async def test_done_transcript_never_rewritten(settings, repo):
    rid = add_recording(settings, repo)
    p = Pipeline(settings, repo, FakeTranscriber(), None)
    await p.process(rid)
    path = text_file(settings, repo, rid)
    path.write_text("my edits\n")
    os.utime(path, (1, 1))
    await p.process(rid)  # e.g. re-enqueued after restart
    assert path.read_text() == "my edits\n"
    assert rid not in repo.resumable_ids(3)
