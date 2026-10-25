import sqlite3

import pytest

from crisp.db import SCHEMA_VERSION, NewRecording, Repository, Status


def new(i: int = 1, **kw) -> NewRecording:
    base = dict(
        sha256=f"sha{i}", source_name=f"REC{i:03}.WAV", source_size=100 + i,
        source_mtime=1000.0 + i,
        recorded_at="2026-10-25T14:30:12+01:00", audio_path=f"2026-10/25_14301{i}_audio.wav",
        text_path=f"2026-10/25_14301{i}_text.md",
    )
    base.update(kw)
    return NewRecording(**base)


def test_fresh_db_at_current_version(tmp_path):
    r = Repository(tmp_path / "s.db")
    assert r.migrate() == SCHEMA_VERSION
    r.close()


def test_migrate_is_idempotent_and_preserves_rows(tmp_path):
    r = Repository(tmp_path / "s.db")
    r.migrate()
    rid = r.insert_copied(new())
    r.close()
    r = Repository(tmp_path / "s.db")
    assert r.migrate() == SCHEMA_VERSION
    assert r.get(rid).sha256 == "sha1"
    r.close()


def test_lookups(repo):
    rid = repo.insert_copied(new())
    assert repo.find_by_sha("sha1").id == rid
    assert repo.find_by_sha("other") is None
    assert repo.find_by_fast_key("REC001.WAV", 101, 1001.0).id == rid
    assert repo.find_by_fast_key("REC001.WAV", 101, 1002.0) is None
    assert repo.path_taken("2026-10/25_143011_audio.wav", "x")
    assert not repo.path_taken("a", "b")


def test_duplicate_sha_rejected(repo):
    repo.insert_copied(new())
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_copied(new(2, sha256="sha1"))


def test_status_flow_and_texts(repo):
    rid = repo.insert_copied(new())
    assert repo.get(rid).status == Status.COPIED
    repo.start_attempt(rid)
    rec = repo.get(rid)
    assert (rec.status, rec.attempts) == (Status.TRANSCRIBING, 1)
    repo.store_raw(rid, "hallo welt", duration_s=3.5, provider="openrouter", model="m")
    rec = repo.get(rid)
    assert (rec.status, rec.duration_s, rec.stt_model) == (Status.TRANSCRIBED, 3.5, "m")
    repo.store_cleaned(rid, "Hallo Welt.", model="c", error=None)
    assert repo.get_texts(rid) == ("hallo welt", "Hallo Welt.")
    assert repo.get(rid).status == Status.CLEANED
    repo.set_status(rid, Status.DONE)
    assert repo.get(rid).status == Status.DONE


def test_resumable_respects_retry_limit(repo):
    copied = repo.insert_copied(new(1))
    done = repo.insert_copied(new(2))
    repo.set_status(done, Status.DONE)
    retry = repo.insert_copied(new(3))
    repo.start_attempt(retry)
    repo.mark_failed(retry, "boom")
    exhausted = repo.insert_copied(new(4))
    for _ in range(3):
        repo.start_attempt(exhausted)
    repo.mark_failed(exhausted, "boom")
    permanent = repo.insert_copied(new(5))
    repo.mark_failed(permanent, "decode", permanent=True, max_attempts=3)
    interrupted = repo.insert_copied(new(6))
    repo.start_attempt(interrupted)

    ids = repo.resumable_ids(max_attempts=3)
    assert set(ids) == {copied, retry, interrupted}
