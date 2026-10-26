"""Wires detection, ingest and the transcription worker into one asyncio service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from crisp.config import Settings
from crisp.db import Repository
from crisp.device import DeviceSource, MountError, MountManager, UdevSource, Volume, Watcher
from crisp.ingest import IngestCancelled, ingest_volume
from crisp.pipeline import Pipeline, run_worker
from crisp.providers import (
    TextRefiner,
    Transcriber,
    build_clients,
    build_refiner,
    build_transcriber,
)
from crisp.sync import sync_archive

log = logging.getLogger("crisp.service")


def make_chown(settings: Settings):
    uid, gid = settings.paths.owner_uid, settings.paths.owner_gid
    if uid is None and gid is None:
        return lambda _p: None

    def chown(path: Path) -> None:
        try:
            os.chown(path, -1 if uid is None else uid, -1 if gid is None else gid)
        except OSError as e:
            log.debug("chown %s failed: %s", path, e)

    return chown


@dataclass
class _IngestJob:
    task: asyncio.Task
    cancel: threading.Event


@dataclass
class Service:
    settings: Settings
    repo: Repository
    pipeline: Pipeline
    source: DeviceSource
    mounter: MountManager
    queue: asyncio.Queue[int] = field(default_factory=asyncio.Queue)
    jobs: dict[str, _IngestJob] = field(default_factory=dict)
    watcher: Watcher | None = None

    def __post_init__(self) -> None:
        self.watcher = Watcher(self.source, self.settings.device, self._on_added,
                               self._on_removed)
        self._chown = make_chown(self.settings)

    def enqueue_resumable(self) -> int:
        ids = self.repo.resumable_ids(self.settings.transcription.max_attempts)
        for rec_id in ids:
            self.queue.put_nowait(rec_id)
        if ids:
            log.info("resuming %d unfinished recording(s)", len(ids))
        return len(ids)

    async def _on_added(self, vol: Volume) -> None:
        cancel = threading.Event()
        task = asyncio.create_task(self._ingest(vol, cancel), name=f"ingest {vol.devnode}")
        self.jobs[vol.devnode] = _IngestJob(task, cancel)

    async def _on_removed(self, vol: Volume) -> None:
        job = self.jobs.pop(vol.devnode, None)
        if job is not None and not job.task.done():
            job.cancel.set()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await job.task
        await asyncio.to_thread(self.mounter.release, vol)

    async def _ingest(self, vol: Volume, cancel: threading.Event) -> None:
        loop = asyncio.get_running_loop()
        try:
            mount = await asyncio.to_thread(self.mounter.ensure_mounted, vol)
        except (MountError, OSError) as e:
            log.error("cannot access recorder %s: %s (will retry)", vol.label, e)
            self.watcher.forget(vol.devnode)
            return
        try:
            await asyncio.to_thread(
                ingest_volume,
                mount,
                record_folder=self.settings.ingest.record_folder,
                archive_dir=self.settings.paths.archive_dir,
                tz=self.settings.tz,
                repo=self.repo,
                on_archived=lambda rec_id: loop.call_soon_threadsafe(self.queue.put_nowait, rec_id),
                cancel=cancel,
                chown=self._chown,
            )
        except IngestCancelled:
            log.warning("ingest from %s interrupted (recorder removed)", vol.devnode)
        except Exception:
            log.exception("ingest from %s failed", vol.devnode)
        finally:
            if not cancel.is_set():
                await asyncio.to_thread(self.mounter.release, vol)

    async def sync(self) -> None:
        try:
            await asyncio.to_thread(sync_archive, self.repo, self.settings.paths.archive_dir)
        except Exception:
            log.exception("archive sync failed")

    async def _sync_periodically(self, stop: asyncio.Event) -> None:
        interval = self.settings.sync.interval_s
        if interval <= 0:
            await stop.wait()
            return
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), interval)
            if not stop.is_set():
                await self.sync()

    async def run(self, stop: asyncio.Event) -> None:
        await self.sync()
        self.enqueue_resumable()
        worker = asyncio.create_task(run_worker(self.queue, self.pipeline), name="worker")
        watcher = asyncio.create_task(self.watcher.run(stop), name="watcher")
        syncer = asyncio.create_task(self._sync_periodically(stop), name="sync")
        stopper = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                {worker, watcher, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                if t is not stopper and t.exception():
                    raise t.exception()
        finally:
            log.info("shutting down")
            for job in self.jobs.values():
                job.cancel.set()
            for t in (worker, watcher, syncer, stopper, *(j.task for j in self.jobs.values())):
                t.cancel()
            await asyncio.gather(worker, watcher, syncer, stopper,
                                 *(j.task for j in self.jobs.values()), return_exceptions=True)
            for devnode in list(self.mounter.owned):
                await asyncio.to_thread(self.mounter.release, _volume_stub(devnode))


def _volume_stub(devnode: str) -> Volume:
    return Volume(devnode=devnode, kernel_name="", major=0, minor=0, fs_type=None)


async def run_service(
    settings: Settings,
    stop: asyncio.Event,
    *,
    source: DeviceSource | None = None,
    mounter: MountManager | None = None,
    transcriber: Transcriber | None = None,
    refiner: TextRefiner | None = None,
) -> None:
    settings.paths.archive_dir.mkdir(parents=True, exist_ok=True)
    chown = make_chown(settings)
    repo = Repository(settings.paths.db_path)
    version = repo.migrate()
    db = settings.paths.db_path
    for path in (settings.paths.data_dir, settings.paths.archive_dir, db,
                 db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        if path.exists():
            chown(path)
    log.info("state database %s at schema version %d", settings.paths.db_path, version)
    clients = build_clients(settings) if transcriber is None else {}
    try:
        transcriber = transcriber or build_transcriber(settings, clients)
        if refiner is None and clients:
            refiner = build_refiner(settings, clients)
        pipeline = Pipeline(settings, repo, transcriber, refiner, chown=make_chown(settings))
        service = Service(
            settings=settings,
            repo=repo,
            pipeline=pipeline,
            source=source or UdevSource(),
            mounter=mounter or MountManager(settings.device),
        )
        await service.run(stop)
    finally:
        for client in clients.values():
            await client.aclose()
        repo.close()
