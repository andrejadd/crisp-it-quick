import logging
import shutil

from crisp.db import NewRecording, Status
from crisp.sync import sync_archive


def add(repo, archive, i=1, status=Status.DONE, text=True):
    audio_rel = f"2026-10/2026_10_25_time14301{i}_audio.wav"
    text_rel = f"2026-10/2026_10_25_time14301{i}_text.md"
    (archive / "2026-10").mkdir(parents=True, exist_ok=True)
    (archive / audio_rel).write_bytes(b"RIFF")
    if text:
        (archive / text_rel).write_text("# Recording\n")
    rid = repo.insert_copied(NewRecording(
        sha256=f"sha{i}", source_name=f"REC00{i}.WAV", source_size=1, source_mtime=float(i),
        recorded_at="2026-10-25T14:30:12+01:00", audio_path=audio_rel, text_path=text_rel,
    ))
    repo.set_status(rid, status)
    return rid


def test_deleted_audio_marked_and_row_kept(repo, tmp_path):
    archive = tmp_path / "archive"
    rid = add(repo, archive)
    (archive / repo.get(rid).audio_path).unlink()
    result = sync_archive(repo, archive)
    rec = repo.get(rid)
    assert (result.deleted, result.restored) == (1, 0)
    assert rec.audio_deleted_at is not None and rec.text_deleted_at is None
    assert repo.find_by_sha("sha1").id == rid


def test_deleted_transcript_marked(repo, tmp_path, caplog):
    archive = tmp_path / "archive"
    rid = add(repo, archive)
    text_rel = repo.get(rid).text_path
    (archive / text_rel).unlink()
    with caplog.at_level(logging.INFO, logger="crisp.sync"):
        sync_archive(repo, archive)
    assert repo.get(rid).text_deleted_at is not None
    assert repo.get(rid).audio_deleted_at is None
    assert any(text_rel in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)


def test_missing_transcript_of_unfinished_recording_not_marked(repo, tmp_path):
    archive = tmp_path / "archive"
    rid = add(repo, archive, status=Status.COPIED, text=False)
    assert sync_archive(repo, archive).deleted == 0
    assert repo.get(rid).text_deleted_at is None


def test_restored_file_cleared_and_detection_time_kept(repo, tmp_path):
    archive = tmp_path / "archive"
    rid = add(repo, archive)
    audio = archive / repo.get(rid).audio_path
    audio.rename(audio.with_suffix(".bak"))
    sync_archive(repo, archive)
    first = repo.get(rid).audio_deleted_at
    assert sync_archive(repo, archive).deleted == 0
    assert repo.get(rid).audio_deleted_at == first
    audio.with_suffix(".bak").rename(audio)
    result = sync_archive(repo, archive)
    assert (result.deleted, result.restored) == (0, 1)
    assert repo.get(rid).audio_deleted_at is None


def test_unchanged_run_writes_nothing(repo, tmp_path, caplog):
    archive = tmp_path / "archive"
    rid = add(repo, archive)
    before = repo.get(rid).updated_at
    with caplog.at_level(logging.DEBUG, logger="crisp.sync"):
        result = sync_archive(repo, archive)
    assert (result.deleted, result.restored) == (0, 0)
    assert repo.get(rid).updated_at == before
    assert all(r.levelno == logging.DEBUG for r in caplog.records)


def test_missing_archive_dir_changes_nothing(repo, tmp_path, caplog):
    archive = tmp_path / "archive"
    rid = add(repo, archive)
    shutil.rmtree(archive)
    with caplog.at_level(logging.WARNING, logger="crisp.sync"):
        result = sync_archive(repo, archive)
    assert result.skipped and result.deleted == 0
    rec = repo.get(rid)
    assert rec.audio_deleted_at is None and rec.text_deleted_at is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
