import asyncio
import subprocess
from pathlib import Path

import pytest

from crisp.config import DeviceConfig
from crisp.device import MountError, MountManager, Volume, Watcher, find_mount, matches_recorder

RECORDER = Volume(
    devnode="/dev/sdb1", kernel_name="sdb1", major=8, minor=17, fs_type="vfat",
    vendor_strings=("Actions Semiconductor Co., Ltd", "Generic"),
    model_strings=("D-Wave 2GB MP4 Player / AK1025 MP3/MP4 Player", "USB DISK"),
    vendor_id="10d6", product_id="1101",
)
STICK = Volume(
    devnode="/dev/sdc1", kernel_name="sdc1", major=8, minor=33, fs_type="vfat",
    vendor_strings=("SanDisk Corp.",), model_strings=("Cruzer Blade",),
    vendor_id="0781", product_id="5567",
)


# -- matcher ---------------------------------------------------------------------

def test_match_by_name():
    assert matches_recorder(RECORDER, DeviceConfig())


def test_name_match_needs_vendor_and_model():
    vol = Volume(**{**RECORDER.__dict__, "vendor_strings": ("Other Inc",)})
    assert not matches_recorder(vol, DeviceConfig())


def test_match_by_configured_ids_ignores_names():
    cfg = DeviceConfig(usb_id="0781:5567")
    assert matches_recorder(STICK, cfg)
    assert not matches_recorder(RECORDER, cfg)


def test_unrelated_device_ignored():
    assert not matches_recorder(STICK, DeviceConfig())


# -- watcher ---------------------------------------------------------------------

class FakeSource:
    def __init__(self, volumes=()):
        self.volumes = list(volumes)
        self.on_event = None

    def list_volumes(self):
        return list(self.volumes)

    def start_monitor(self, on_event):
        self.on_event = on_event
        return True

    def stop_monitor(self):
        pass


def make_watcher(source, **cfg):
    events = []

    async def added(v):
        events.append(("added", v.devnode))

    async def removed(v):
        events.append(("removed", v.devnode))

    cfg.setdefault("debounce_s", 0)
    return Watcher(source, DeviceConfig(**cfg), added, removed), events


async def test_detects_recorder_present_at_startup():
    w, events = make_watcher(FakeSource([RECORDER, STICK]))
    await w.reconcile()
    assert events == [("added", "/dev/sdb1")]
    await w.reconcile()
    assert events == [("added", "/dev/sdb1")]  # no duplicate


async def test_removal_event():
    src = FakeSource([RECORDER])
    w, events = make_watcher(src)
    await w.reconcile()
    src.volumes = []
    await w.reconcile()
    assert events == [("added", "/dev/sdb1"), ("removed", "/dev/sdb1")]


async def test_poll_catches_missed_event():
    src = FakeSource([])
    w, events = make_watcher(src, poll_interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.02)
    src.volumes = [RECORDER]  # plugged in, but no hotplug notification delivered
    await asyncio.sleep(0.15)
    stop.set()
    await task
    assert events == [("added", "/dev/sdb1")]


async def test_hotplug_event_triggers_immediate_reconcile():
    src = FakeSource([])
    w, events = make_watcher(src, poll_interval_s=60)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.02)
    src.volumes = [RECORDER]
    src.on_event()
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert events == [("added", "/dev/sdb1")]


async def test_debounce_waits_until_stable():
    src = FakeSource([RECORDER])
    w, events = make_watcher(src, debounce_s=0.1, poll_interval_s=60)
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    await asyncio.sleep(0.03)
    assert events == []
    await asyncio.sleep(0.15)
    stop.set()
    await task
    assert events == [("added", "/dev/sdb1")]


async def test_flapping_device_within_debounce_is_not_added():
    src = FakeSource([RECORDER])
    w, events = make_watcher(src, debounce_s=0.1)
    await w.reconcile()
    src.volumes = []
    await w.reconcile()
    await asyncio.sleep(0.12)
    src.volumes = [RECORDER]
    await w.reconcile()
    assert events == []


async def test_forget_allows_retry():
    w, events = make_watcher(FakeSource([RECORDER]))
    await w.reconcile()
    w.forget("/dev/sdb1")
    await w.reconcile()
    assert events == [("added", "/dev/sdb1"), ("added", "/dev/sdb1")]


# -- mounting ----------------------------------------------------------------------

MOUNTINFO = (
    "22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n"
    "98 22 8:17 / /media/andrej/D\\040WAVE rw,nosuid shared:50 - vfat /dev/sdb1 rw\n"
)


def test_find_mount():
    assert find_mount(8, 17, MOUNTINFO) == Path("/media/andrej/D WAVE")
    assert find_mount(8, 33, MOUNTINFO) is None


class Recorder:
    def __init__(self, rc=0, stderr=""):
        self.calls = []
        self.rc, self.stderr = rc, stderr

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", self.stderr)


def manager(tmp_path, mountinfo="", runner=None, **cfg):
    info = tmp_path / "mountinfo"
    info.write_text(mountinfo)
    cfg.setdefault("mount_root", tmp_path / "mnt")
    return MountManager(DeviceConfig(**cfg), runner=runner or Recorder(), mountinfo_path=info)


def test_already_mounted_is_reused(tmp_path):
    runner = Recorder()
    m = manager(tmp_path, MOUNTINFO, runner)
    assert m.ensure_mounted(RECORDER) == Path("/media/andrej/D WAVE")
    assert runner.calls == []
    m.release(RECORDER)
    assert runner.calls == []  # never unmount what we did not mount


def test_mounts_read_only_when_needed(tmp_path):
    runner = Recorder()
    m = manager(tmp_path, "", runner)
    path = m.ensure_mounted(RECORDER)
    assert path == tmp_path / "mnt" / "sdb1"
    cmd = runner.calls[0]
    assert cmd[0] == "mount" and cmd[-2:] == ["/dev/sdb1", str(path)]
    opts = cmd[cmd.index("-o") + 1].split(",")
    assert {"ro", "noexec", "nosuid", "nodev", "tz=UTC"} <= set(opts)
    m.release(RECORDER)
    assert runner.calls[-1] == ["umount", "-l", str(path)]


def test_mount_failure_raises(tmp_path):
    m = manager(tmp_path, "", Recorder(rc=32, stderr="wrong fs type"))
    with pytest.raises(MountError, match="wrong fs type"):
        m.ensure_mounted(RECORDER)
    assert m.owned == {}


def test_mount_path_override(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    runner = Recorder()
    m = manager(tmp_path, "", runner, mount_path_override=host)
    assert m.ensure_mounted(RECORDER) == host
    assert runner.calls == []
