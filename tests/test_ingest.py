import hashlib
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from crisp import ingest
from crisp.ingest import (
    CopyError,
    IngestCancelled,
    archive_names,
    choose_names,
    discover,
    find_record_dir,
    ingest_volume,
    recorded_at,
    safe_copy,
)

TZ = ZoneInfo("Europe/Berlin")


def wall_epoch(y, mo, d, h, mi, s) -> float:
    """Epoch value a tz=UTC FAT mount reports for this wall-clock time."""
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC).timestamp()


def put(path: Path, content: bytes, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def snapshot(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


# -- discovery ----------------------------------------------------------------------

def test_discover_wav_any_case(tmp_path):
    rec = tmp_path / "RECORD"
    put(rec / "REC001.WAV", b"a")
    put(rec / "rec002.wav", b"b")
    put(rec / "notes.txt", b"c")
    (rec / "sub.wav").mkdir()
    assert [p.name for p in discover(rec)] == ["REC001.WAV", "rec002.wav"]


def test_record_folder_case_insensitive(tmp_path):
    (tmp_path / "Record").mkdir()
    assert find_record_dir(tmp_path, "RECORD") == tmp_path / "Record"


def test_missing_record_folder(tmp_path, repo, caplog):
    result = ingest_volume(tmp_path, record_folder="RECORD", archive_dir=tmp_path / "a",
                           tz=TZ, repo=repo)
    assert result.archived == []
    assert "no RECORD folder" in caplog.text


# -- naming ----------------------------------------------------------------------

def test_spec_naming_example():
    when = recorded_at(wall_epoch(2026, 10, 25, 14, 30, 12), TZ)
    names = archive_names(when, ".WAV")
    assert str(names.audio) == "2026-10/25_143012_audio.wav"
    assert str(names.text) == "2026-10/25_143012_text.md"
    assert when.isoformat() == "2026-10-25T14:30:12+01:00"


def test_dst_wall_clock_kept():
    # 2026-03-29 02:30 does not exist in Berlin (clocks jump 02:00 -> 03:00): keep the
    # recorder's wall-clock reading for the name rather than shifting it.
    when = recorded_at(wall_epoch(2026, 3, 29, 2, 30, 0), TZ)
    assert str(archive_names(when, "wav").audio) == "2026-03/29_023000_audio.wav"
    summer = recorded_at(wall_epoch(2026, 7, 1, 9, 0, 0), TZ)
    assert summer.utcoffset().total_seconds() == 7200


def test_collision_suffix_shared(tmp_path):
    when = recorded_at(wall_epoch(2026, 10, 25, 14, 30, 12), TZ)
    put(tmp_path / "2026-10" / "25_143012_audio.wav", b"x")
    names = choose_names(when, "wav", tmp_path, lambda a, t: False)
    assert str(names.audio) == "2026-10/25_143012-2_audio.wav"
    assert str(names.text) == "2026-10/25_143012-2_text.md"
    # DB reservations count as taken too
    names = choose_names(when, "wav", tmp_path, lambda a, t: a.endswith("-2_audio.wav"))
    assert str(names.audio) == "2026-10/25_143012-3_audio.wav"


# -- safe copy ---------------------------------------------------------------------

def test_safe_copy_ok(tmp_path):
    src = put(tmp_path / "src.wav", b"hello" * 1000)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    dst = tmp_path / "out" / "a.wav"
    safe_copy(src, dst, sha)
    assert dst.read_bytes() == src.read_bytes()
    assert not list((tmp_path / "out").glob("*.part"))


def test_safe_copy_mismatch_discarded(tmp_path):
    src = put(tmp_path / "src.wav", b"hello")
    dst = tmp_path / "out" / "a.wav"
    with pytest.raises(CopyError):
        safe_copy(src, dst, "0" * 64)
    assert list((tmp_path / "out").iterdir()) == []


def test_safe_copy_interrupted_leaves_nothing(tmp_path, monkeypatch):
    src = put(tmp_path / "src.wav", b"x" * (3 * ingest.CHUNK))
    dst = tmp_path / "out" / "a.wav"
    real_open = Path.open
    reads = {"n": 0}

    class Flaky:
        def __init__(self, f):
            self.f = f

        def read(self, n):
            reads["n"] += 1
            if reads["n"] == 2:
                raise OSError(5, "Input/output error")  # device unplugged
            return self.f.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

    def fake_open(self, mode="r", *a, **kw):
        f = real_open(self, mode, *a, **kw)
        return Flaky(f) if self == src else f

    monkeypatch.setattr(Path, "open", fake_open)
    with pytest.raises(OSError):
        safe_copy(src, dst, "0" * 64)
    assert list((tmp_path / "out").iterdir()) == []


# -- full ingest run -----------------------------------------------------------------

def run(mount, archive, repo, **kw):
    return ingest_volume(mount, record_folder="RECORD", archive_dir=archive, tz=TZ, repo=repo, **kw)


def test_ingest_run_end_to_end(tmp_path, repo):
    mount, archive = tmp_path / "dev", tmp_path / "archive"
    t = wall_epoch(2026, 10, 25, 14, 30, 12)
    put(mount / "RECORD" / "REC001.WAV", b"one", t)
    put(mount / "RECORD" / "REC002.WAV", b"two", t)  # same second -> collision suffix
    put(mount / "RECORD" / "REC003.WAV", b"three", t + 60)

    queued = []
    first = run(mount, archive, repo, on_archived=queued.append)
    assert len(first.archived) == 3 and queued == first.archived
    assert sorted(p.name for p in (archive / "2026-10").iterdir()) == [
        "25_143012-2_audio.wav", "25_143012_audio.wav", "25_143112_audio.wav",
    ]
    assert (archive / "2026-10" / "25_143112_audio.wav").read_bytes() == b"three"
    assert all(r.status == "copied" for r in repo.all())

    second = run(mount, archive, repo)
    assert second.archived == [] and second.skipped == 3

    # Recorder deleted its files and reused a name for a new recording.
    for p in (mount / "RECORD").iterdir():
        p.unlink()
    put(mount / "RECORD" / "REC001.WAV", b"brand new", t + 3600)
    third = run(mount, archive, repo)
    assert len(third.archived) == 1
    assert (archive / "2026-10" / "25_153012_audio.wav").read_bytes() == b"brand new"

    # Content already archived under another name/mtime is not copied again.
    put(mount / "RECORD" / "COPY.WAV", b"one", t + 7200)
    assert run(mount, archive, repo).archived == []


def test_ingest_never_modifies_device(tmp_path, repo):
    mount, archive = tmp_path / "dev", tmp_path / "archive"
    for i in range(3):
        put(mount / "RECORD" / f"REC00{i}.WAV", bytes([i]) * 100, wall_epoch(2026, 1, 2, 3, 4, i))
    before = snapshot(mount / "RECORD")
    run(mount, archive, repo)
    assert snapshot(mount / "RECORD") == before


def test_ingest_cancelled(tmp_path, repo):
    mount, archive = tmp_path / "dev", tmp_path / "archive"
    put(mount / "RECORD" / "REC001.WAV", b"one", 1000)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(IngestCancelled):
        run(mount, archive, repo, cancel=cancel)
    assert repo.all() == []


# -- start time from the device file name ----------------------------------------------

def test_start_time_from_name():
    when = ingest.start_from_name("20261025143012.WAV", TZ)
    assert when.isoformat() == "2026-10-25T14:30:12+01:00"


@pytest.mark.parametrize("name", ["REC005.WAV", "20261332143012.WAV", "202610251430.WAV",
                                  "202610251430120.WAV", "19991025143012.WAV"])
def test_unparseable_names_fall_back(name):
    assert ingest.start_from_name(name, TZ) is None
    mtime = wall_epoch(2026, 10, 25, 14, 30, 12)
    assert ingest.recording_time(name, mtime, TZ).isoformat() == "2026-10-25T14:30:12+01:00"


def test_ingest_names_by_recording_start(tmp_path, repo):
    mount, archive = tmp_path / "dev", tmp_path / "archive"
    # Real recorder: name = start (15:43:32), mtime = end (15:48:36).
    put(mount / "RECORD" / "20261012154332.WAV", b"x", wall_epoch(2026, 10, 12, 15, 48, 36))
    run(mount, archive, repo)
    rec = repo.all()[0]
    assert rec.audio_path == "2026-10/12_154332_audio.wav"
    assert rec.recorded_at == "2026-10-12T15:43:32+02:00"
