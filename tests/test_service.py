import asyncio
import os
import sqlite3
import time
from datetime import UTC, datetime

import pytest

from crisp.db import Repository, Status
from crisp.device import MountManager
from crisp.service import run_service
from tests.conftest import make_audio
from tests.test_device import RECORDER, FakeSource
from tests.test_pipeline import FakeTranscriber


def wall(h, m, s):
    return datetime(2026, 10, 25, h, m, s, tzinfo=UTC).timestamp()


@pytest.fixture
def device_dir(tmp_path):
    rec = tmp_path / "device" / "RECORD"
    rec.mkdir(parents=True)
    for i, t in enumerate([wall(9, 0, 0), wall(9, 5, 0)]):
        p = make_audio(rec / f"REC00{i + 1}.WAV", 3 + i)
        os.utime(p, (t, t))
    return tmp_path / "device"


@pytest.fixture
def svc_settings(settings, device_dir):
    settings.device.mount_path_override = device_dir
    return settings


async def wait_for(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


def statuses(settings):
    r = Repository(settings.paths.db_path)
    try:
        return [rec.status for rec in r.all()]
    except sqlite3.OperationalError:  # service has not created the schema yet
        return []
    finally:
        r.close()


async def start(settings, source, transcriber):
    stop = asyncio.Event()
    task = asyncio.create_task(run_service(
        settings, stop, source=source, mounter=MountManager(settings.device),
        transcriber=transcriber,
    ))
    return stop, task


async def test_end_to_end(svc_settings, device_dir):
    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE] * 2)
    t0 = time.monotonic()
    stop.set()
    await asyncio.wait_for(task, 10)
    assert time.monotonic() - t0 < 10

    month = svc_settings.paths.archive_dir / "2026-10"
    assert sorted(p.name for p in month.iterdir()) == [
        "2026_10_25_time090000_audio.wav", "2026_10_25_time090000_text.md",
        "2026_10_25_time090500_audio.wav", "2026_10_25_time090500_text.md",
    ]
    assert "hallo welt" in (month / "2026_10_25_time090000_text.md").read_text()
    assert sorted(p.name for p in (device_dir / "RECORD").iterdir()) == ["REC001.WAV", "REC002.WAV"]

    # Re-plug: nothing new is archived.
    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await asyncio.sleep(0.5)
    stop.set()
    await task
    assert statuses(svc_settings) == [Status.DONE] * 2
    assert len(list(month.iterdir())) == 4


class BlockingTranscriber(FakeTranscriber):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def transcribe(self, audio, fmt, language):
        await self.release.wait()
        return await super().transcribe(audio, fmt, language)


async def test_restart_during_transcription_resumes(svc_settings):
    stop, task = await start(svc_settings, FakeSource([RECORDER]), BlockingTranscriber())
    await wait_for(lambda: Status.TRANSCRIBING in statuses(svc_settings))
    stop.set()  # "killed" while transcribing
    await asyncio.wait_for(task, 10)
    assert Status.TRANSCRIBING in statuses(svc_settings)

    # Restart without the recorder attached: work resumes from the state DB alone.
    stop, task = await start(svc_settings, FakeSource([]), FakeTranscriber())
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE] * 2)
    stop.set()
    await task


async def test_transcription_continues_after_unplug(svc_settings):
    source = FakeSource([RECORDER])
    transcriber = BlockingTranscriber()
    stop, task = await start(svc_settings, source, transcriber)
    await wait_for(lambda: len(statuses(svc_settings)) == 2)
    source.volumes = []  # recorder unplugged after archiving
    await asyncio.sleep(0.2)
    transcriber.release.set()
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE] * 2)
    stop.set()
    await task


async def test_mount_failure_retried_on_next_poll(svc_settings, tmp_path):
    svc_settings.device.mount_path_override = tmp_path / "not-there-yet"
    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await asyncio.sleep(0.2)
    assert statuses(svc_settings) == []
    (tmp_path / "not-there-yet" / "RECORD").mkdir(parents=True)
    make_audio(tmp_path / "not-there-yet" / "RECORD" / "REC009.WAV", 2)
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE])
    stop.set()
    await task


def recordings(settings):
    r = Repository(settings.paths.db_path)
    try:
        return r.all()
    finally:
        r.close()


async def test_audio_deleted_while_stopped_not_resumed(svc_settings):
    stop, task = await start(svc_settings, FakeSource([RECORDER]), BlockingTranscriber())
    await wait_for(lambda: len(statuses(svc_settings)) == 2)
    stop.set()
    await asyncio.wait_for(task, 10)
    gone = recordings(svc_settings)[1]
    (svc_settings.paths.archive_dir / gone.audio_path).unlink()

    transcriber = FakeTranscriber()
    stop, task = await start(svc_settings, FakeSource([]), transcriber)
    await wait_for(lambda: statuses(svc_settings)[0] == Status.DONE)
    await asyncio.sleep(0.2)
    stop.set()
    await task
    first, second = recordings(svc_settings)
    assert second.audio_deleted_at is not None and second.status != Status.DONE
    assert transcriber.calls == 1
    assert not (svc_settings.paths.archive_dir / second.text_path).exists()


async def test_deleted_transcript_not_rewritten_on_restart(svc_settings):
    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE] * 2)
    stop.set()
    await task
    text = svc_settings.paths.archive_dir / recordings(svc_settings)[0].text_path
    text.unlink()

    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await asyncio.sleep(0.5)
    stop.set()
    await task
    assert recordings(svc_settings)[0].text_deleted_at is not None
    assert not text.exists()
    assert len(recordings(svc_settings)) == 2  # not re-ingested from the recorder


async def test_periodic_sync_marks_file_deleted_while_running(svc_settings):
    svc_settings.sync.interval_s = 0.1
    stop, task = await start(svc_settings, FakeSource([RECORDER]), FakeTranscriber())
    await wait_for(lambda: statuses(svc_settings) == [Status.DONE] * 2)
    rec = recordings(svc_settings)[0]
    (svc_settings.paths.archive_dir / rec.text_path).unlink()
    await wait_for(lambda: recordings(svc_settings)[0].text_deleted_at is not None)
    stop.set()
    await task
