from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from crisp.output import TranscriptDoc, render, write_atomic

GOLDEN = Path(__file__).parent / "golden"


def doc(raw="hallo das ist ein test zweiter absatz", cleaned=None, cleanup_model=None):
    return TranscriptDoc(
        recorded_at=datetime(2026, 10, 25, 14, 30, 12, tzinfo=ZoneInfo("Europe/Berlin")),
        source_file="REC005.WAV",
        audio_file="2026_10_25_time143012_audio.wav",
        duration_s=63.42,
        stt_provider="openrouter",
        stt_model="mistralai/voxtral-small-transcribe-3-0",
        cleanup_model=cleanup_model,
        created_at=datetime(2026, 10, 25, 15, 0, 0, tzinfo=UTC),
        raw_text=raw,
        cleaned_text=cleaned,
    )


@pytest.mark.parametrize(
    "golden,d",
    [
        ("cleaned.md", doc(cleaned="Hallo, das ist ein Test.\n\nZweiter Absatz.",
                           cleanup_model="deepseek/deepseek-v4-flash")),
        ("raw_only.md", doc()),
        # cleanup failed: a model was attempted but there is no cleaned text
        ("raw_only.md", doc(cleanup_model="deepseek/deepseek-v4-flash")),
        ("no_speech.md", doc(raw="  ")),
    ],
)
def test_render_matches_golden(golden, d):
    assert render(d) == (GOLDEN / golden).read_text()


def test_write_atomic(tmp_path):
    p = tmp_path / "2026-10" / "x_text.md"
    write_atomic(p, "content\n")
    assert p.read_text() == "content\n"
    assert [f.name for f in p.parent.iterdir()] == ["x_text.md"]
