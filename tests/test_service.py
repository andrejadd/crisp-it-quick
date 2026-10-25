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
        "25_090000_audio.wav", "25_090000_text.md", "25_090500_audio.wav", "25_090500_text.md",
    ]
    assert "hallo welt" in (month / "25_090000_text.md").read_text()
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
