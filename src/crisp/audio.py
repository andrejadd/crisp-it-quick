"""Audio normalization (ffmpeg) and chunk planning for transcription."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
SILENCE_NOISE = "-35dB"
SILENCE_MIN_S = 0.3
CUT_SEARCH_WINDOW_S = 5.0
MIN_TAIL_S = 5.0


class DecodeError(Exception):
    """The recording could not be decoded; retrying will not help."""


@dataclass(frozen=True)
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", *args], capture_output=True, text=True
    )


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as e:
        raise DecodeError(f"cannot read duration of {path.name}: {result.stderr.strip()}") from e


def normalize(src: Path, dst: Path) -> None:
    """Convert to 16 kHz mono FLAC. ``src`` is only read."""
    result = _ffmpeg(
        ["-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-c:a", "flac", str(dst)]
    )
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise DecodeError(f"cannot decode {src.name}: {result.stderr.strip()[-500:]}")


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silences(stderr: str) -> list[tuple[float, float]]:
    starts = [float(m) for m in _SIL_START.findall(stderr)]
    ends = [float(m) for m in _SIL_END.findall(stderr)]
    return [(max(0.0, s), e) for s, e in zip(starts, ends, strict=False) if e > s]


def detect_silences(path: Path) -> list[tuple[float, float]]:
    result = _ffmpeg(
        ["-i", str(path), "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN_S}",
         "-f", "null", "-"]
    )
    return parse_silences(result.stderr)


def plan_segments(
    duration: float,
    chunk_s: float,
    overlap_s: float,
    silences: list[tuple[float, float]] = (),
    window_s: float = CUT_SEARCH_WINDOW_S,
) -> list[Segment]:
    """Split ``[0, duration]`` into consecutive segments of about ``chunk_s``.

    Each cut moves to the middle of the nearest silence within ``window_s`` of the target so
    words are not split; every segment after the first starts ``overlap_s`` before its cut.
    """
    if duration <= chunk_s:
        return [Segment(0.0, duration)]
    mids = [(s + e) / 2 for s, e in silences]
    cuts: list[float] = []
    target = chunk_s
    while duration - target > MIN_TAIL_S:
        near = [m for m in mids if abs(m - target) <= window_s and (not cuts or m > cuts[-1])]
        cut = min(near, key=lambda m: abs(m - target)) if near else target
        cuts.append(cut)
        target = cut + chunk_s
    bounds = [0.0, *cuts, duration]
    return [
        Segment(max(0.0, bounds[i] - (overlap_s if i else 0.0)), bounds[i + 1])
        for i in range(len(bounds) - 1)
    ]


def extract_segment(src: Path, seg: Segment, dst: Path) -> bytes:
    result = _ffmpeg(
        ["-v", "error", "-y", "-ss", f"{seg.start:.3f}", "-t", f"{seg.duration:.3f}",
         "-i", str(src), "-c:a", "flac", str(dst)]
    )
    if result.returncode != 0:
        raise DecodeError(f"cannot cut segment {seg}: {result.stderr.strip()[-500:]}")
    return dst.read_bytes()
