"""Recorder detection (udev hotplug + polling) and read-only mounting."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from crisp.config import DeviceConfig

log = logging.getLogger("crisp.device")


@dataclass(frozen=True)
class Volume:
    """A block device (disk or partition) carrying a filesystem, plus its USB identity."""

    devnode: str
    kernel_name: str
    major: int
    minor: int
    fs_type: str | None
    vendor_strings: tuple[str, ...] = ()
    model_strings: tuple[str, ...] = ()
    vendor_id: str | None = None
    product_id: str | None = None

    @property
    def label(self) -> str:
        vendor = next((s for s in self.vendor_strings if s), "?")
        model = next((s for s in self.model_strings if s), "?")
        ids = f"{self.vendor_id or '????'}:{self.product_id or '????'}"
        return f"{self.devnode} [{ids}] {vendor} / {model}"


def matches_recorder(vol: Volume, cfg: DeviceConfig) -> bool:
    """Configured vid:pid wins; otherwise vendor AND model name substrings must match."""
    if cfg.usb_id:
        want_vid, want_pid = cfg.usb_id.split(":")
        return (vol.vendor_id or "").lower() == want_vid and (
            vol.product_id or ""
        ).lower() == want_pid

    def any_contains(haystack: Iterable[str], needles: Iterable[str]) -> bool:
        hay = [h.lower() for h in haystack if h]
        return any(n.lower() in h for n in needles for h in hay)

    return any_contains(vol.vendor_strings, cfg.vendor_match) and any_contains(
        vol.model_strings, cfg.model_match
    )


# -- device sources -----------------------------------------------------------


class DeviceSource(Protocol):
    def list_volumes(self) -> list[Volume]: ...

    def start_monitor(self, on_event: Callable[[], None]) -> bool:
        """Start delivering hotplug notifications; returns False if unavailable."""
        ...

    def stop_monitor(self) -> None: ...


def _clean(s: str | None) -> str:
    return (s or "").replace("_", " ").strip()


class UdevSource:
    """Lists USB block devices with filesystems via pyudev and watches for hotplug."""

    def __init__(self) -> None:
        import pyudev

        self._pyudev = pyudev
        self._context = pyudev.Context()
        self._observer = None

    def _to_volume(self, dev) -> Volume | None:
        props = dev.properties
        if props.get("ID_BUS") != "usb" or not props.get("ID_FS_TYPE"):
            return None
        # usb.ids database names first: they are the most descriptive (used for log labels).
        vendors = [props.get("ID_VENDOR_FROM_DATABASE")]
        models = [props.get("ID_MODEL_FROM_DATABASE")]
        vid, pid = props.get("ID_VENDOR_ID"), props.get("ID_MODEL_ID")
        usb = dev.find_parent("usb", "usb_device")
        if usb is not None:
            uprops = usb.properties
            vendors += [uprops.get("ID_VENDOR_FROM_DATABASE"), _attr(usb, "manufacturer")]
            models += [uprops.get("ID_MODEL_FROM_DATABASE"), _attr(usb, "product")]
            vid = _attr(usb, "idVendor") or vid
            pid = _attr(usb, "idProduct") or pid
        vendors.append(_clean(props.get("ID_VENDOR")))
        models.append(_clean(props.get("ID_MODEL")))
        num = dev.device_number
        return Volume(
            devnode=dev.device_node,
            kernel_name=dev.sys_name,
            major=os.major(num),
            minor=os.minor(num),
            fs_type=props.get("ID_FS_TYPE"),
            vendor_strings=tuple(v for v in vendors if v),
            model_strings=tuple(m for m in models if m),
            vendor_id=(vid or None) and vid.lower(),
            product_id=(pid or None) and pid.lower(),
        )

    def list_volumes(self) -> list[Volume]:
        vols = []
        for dev in self._context.list_devices(subsystem="block"):
            if dev.device_node is None:
                continue
            vol = self._to_volume(dev)
            if vol is not None:
                vols.append(vol)
        return vols

    def start_monitor(self, on_event: Callable[[], None]) -> bool:
        try:
            monitor = self._pyudev.Monitor.from_netlink(self._context)
            monitor.filter_by("block")
            self._observer = self._pyudev.MonitorObserver(
                monitor, callback=lambda _dev: on_event(), name="udev-monitor"
            )
            self._observer.start()
            return True
        except Exception as e:  # netlink unavailable (e.g. no host networking)
            log.warning("udev hotplug monitor unavailable (%s); relying on polling only", e)
            return False

    def stop_monitor(self) -> None:
        if self._observer is not None:
            self._observer.send_stop()
            self._observer = None


def _attr(dev, name: str) -> str | None:
    try:
        value = dev.attributes.asstring(name)
    except (KeyError, UnicodeDecodeError):
        return None
    return value.strip() or None


# -- watcher --------------------------------------------------------------------


@dataclass
class Watcher:
    """Reconciles the set of connected recorder volumes.

    Hotplug events only trigger an immediate reconcile; a periodic poll does the same, so a
    missed event never prevents detection. A volume must still be present ``debounce_s``
    after first being seen before ``on_added`` fires.
    """

    source: DeviceSource
    cfg: DeviceConfig
    on_added: Callable[[Volume], Awaitable[None]]
    on_removed: Callable[[Volume], Awaitable[None]]
    present: dict[str, Volume] = field(default_factory=dict)
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _pending: dict[str, float] = field(default_factory=dict)

    def forget(self, devnode: str) -> None:
        """Drop a volume from the present set so the next reconcile re-adds it (retry)."""
        self.present.pop(devnode, None)

    def notify(self) -> None:
        self._wake.set()

    async def reconcile(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            volumes = await asyncio.to_thread(self.source.list_volumes)
        except Exception:
            log.exception("listing block devices failed")
            return
        current = {}
        for v in volumes:
            if matches_recorder(v, self.cfg):
                current[v.devnode] = v
            else:
                log.debug("ignoring USB volume %s", v.label)

        for devnode in list(self.present):
            if devnode not in current:
                vol = self.present.pop(devnode)
                log.info("recorder removed: %s", vol.label)
                await self.on_removed(vol)

        now = loop.time()
        for devnode in list(self._pending):
            if devnode not in current:
                del self._pending[devnode]
        for devnode, vol in current.items():
            if devnode in self.present:
                continue
            first_seen = self._pending.setdefault(devnode, now)
            if now - first_seen >= self.cfg.debounce_s:
                del self._pending[devnode]
                self.present[devnode] = vol
                log.info("recorder detected: %s", vol.label)
                await self.on_added(vol)
            else:
                # Re-check once the debounce window has elapsed.
                loop.call_later(self.cfg.debounce_s - (now - first_seen) + 0.05, self._wake.set)

    async def run(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        self.source.start_monitor(lambda: loop.call_soon_threadsafe(self._wake.set))
        try:
            while not stop.is_set():
                await self.reconcile()
                self._wake.clear()
                waiters = [asyncio.ensure_future(self._wake.wait()),
                           asyncio.ensure_future(stop.wait())]
                await asyncio.wait(
                    waiters, timeout=self.cfg.poll_interval_s, return_when=asyncio.FIRST_COMPLETED
                )
                for w in waiters:
                    w.cancel()
        finally:
            self.source.stop_monitor()


# -- mounting -------------------------------------------------------------------


class MountError(Exception):
    pass


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _unescape(path: str) -> str:
    # mountinfo escapes space, tab, newline and backslash as octal.
    for code, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        path = path.replace(code, ch)
    return path


def find_mount(major: int, minor: int, mountinfo: str) -> Path | None:
    """Return the first mount point of device ``major:minor`` in a mountinfo text."""
    want = f"{major}:{minor}"
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) > 4 and fields[2] == want:
            return Path(_unescape(fields[4]))
    return None


@dataclass
class MountManager:
    cfg: DeviceConfig
    runner: Runner = _run
    mountinfo_path: Path = Path("/proc/self/mountinfo")
    owned: dict[str, Path] = field(default_factory=dict)

    def ensure_mounted(self, vol: Volume) -> Path:
        """Return a path where the volume's filesystem is readable, mounting it if needed."""
        if self.cfg.mount_path_override is not None:
            path = self.cfg.mount_path_override
            if not path.is_dir():
                raise MountError(f"mount_path_override {path} does not exist")
            return path

        existing = find_mount(vol.major, vol.minor, self.mountinfo_path.read_text())
        if existing is not None:
            log.info("recorder %s already mounted at %s", vol.devnode, existing)
            return existing

        target = self.cfg.mount_root / vol.kernel_name
        target.mkdir(parents=True, exist_ok=True)
        opts = "ro,noexec,nosuid,nodev"
        if vol.fs_type in ("vfat", "msdos"):
            # FAT stores local wall-clock time; tz=UTC makes the kernel report it unshifted so
            # ingest can interpret it in the configured timezone.
            opts += ",tz=UTC"
        cmd = ["mount", "-o", opts]
        if vol.fs_type:
            cmd += ["-t", vol.fs_type]
        cmd += [vol.devnode, str(target)]
        result = self.runner(cmd)
        if result.returncode != 0:
            raise MountError(
                f"mounting {vol.devnode} at {target} failed: {(result.stderr or '').strip()}"
            )
        self.owned[vol.devnode] = target
        log.info("mounted %s read-only at %s", vol.devnode, target)
        return target

    def release(self, vol: Volume) -> None:
        """Unmount only a mount this service created."""
        target = self.owned.pop(vol.devnode, None)
        if target is None:
            return
        result = self.runner(["umount", "-l", str(target)])
        if result.returncode != 0:
            log.warning("unmounting %s failed: %s", target, (result.stderr or "").strip())
        else:
            log.info("unmounted %s", target)
