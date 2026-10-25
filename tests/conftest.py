from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crisp.config import Settings
from crisp.db import Repository

ROOT = Path(__file__).resolve().parents[1]


def make_audio(
    path: Path, seconds: float, codec: str = "pcm_s16le", tone_gaps: bool = False
) -> Path:
    """Generate a mono 16 kHz WAV: a 440 Hz tone, optionally with 1 s gaps every 10 s."""
    if tone_gaps:
        expr = "if(lt(mod(t\\,10)\\,9)\\,sin(2*PI*440*t)*0.5\\,0)"
        src = f"aevalsrc={expr}:s=16000:d={seconds}"
    else:
        src = f"sine=frequency=440:sample_rate=16000:duration={seconds}"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-ac", "1",
         "-c:a", codec, str(path)],
        check=True,
    )
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings.model_validate(
        {
            "paths": {"data_dir": str(tmp_path / "data")},
            "device": {"debounce_s": 0, "poll_interval_s": 0.05,
                       "mount_root": str(tmp_path / "mnt")},
            "transcription": {"chunk_seconds": 120},
        }
    )
    return s


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    r = Repository(tmp_path / "state.db")
    r.migrate()
    yield r
    r.close()
