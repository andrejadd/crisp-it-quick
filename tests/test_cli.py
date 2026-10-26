from crisp.cli import main
from crisp.db import NewRecording, Repository


def test_sync_command_marks_deleted_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    data = tmp_path / "data"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"paths:\n  data_dir: {data}\n")
    (data / "audio_to_text").mkdir(parents=True)
    repo = Repository(data / "state.db")
    repo.migrate()
    rid = repo.insert_copied(NewRecording(
        sha256="sha1", source_name="REC001.WAV", source_size=1, source_mtime=1.0,
        recorded_at="2026-10-25T14:30:12+01:00", audio_path="2026-10/gone_audio.wav",
        text_path="2026-10/gone_text.md",
    ))
    repo.close()

    assert main(["--config", str(cfg), "sync"]) == 0

    repo = Repository(data / "state.db")
    assert repo.get(rid).audio_deleted_at is not None
    repo.close()
