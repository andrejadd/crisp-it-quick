import hashlib

import pytest

from crisp import audio
from crisp.audio import DecodeError, Segment, parse_silences, plan_segments
from tests.conftest import make_audio


def test_single_segment_when_short():
    assert plan_segments(90, 120, 1) == [Segment(0, 90)]


def test_25_minutes_with_10_minute_chunks():
    segs = plan_segments(25 * 60, 600, 1)
    assert [(s.start, s.end) for s in segs] == [(0, 600), (599, 1200), (1199, 1500)]


def test_cut_moves_to_nearby_silence():
    segs = plan_segments(300, 120, 0, silences=[(117.0, 118.0), (240.0, 241.0)])
    assert [(s.start, s.end) for s in segs] == [(0, 117.5), (117.5, 240.5), (240.5, 300)]


def test_silence_outside_window_ignored():
    segs = plan_segments(300, 120, 0, silences=[(100.0, 101.0)])
    assert segs[0].end == 120


def test_tiny_tail_is_merged():
    segs = plan_segments(122, 120, 1)
    assert segs == [Segment(0, 122)]


def test_parse_silences():
    stderr = (
        "[silencedetect @ 0x1] silence_start: 8.99\n"
        "[silencedetect @ 0x1] silence_end: 10.01 | silence_duration: 1.02\n"
    )
    assert parse_silences(stderr) == [(8.99, 10.01)]


@pytest.mark.parametrize("codec", ["pcm_s16le", "adpcm_ima_wav"])
def test_normalize_and_chunk_real_audio(tmp_path, codec):
    src = make_audio(tmp_path / "rec.wav", 45, codec=codec, tone_gaps=True)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    flac = tmp_path / "n.flac"
    audio.normalize(src, flac)
    duration = audio.probe_duration(flac)
    assert 44.5 < duration < 45.5
    silences = audio.detect_silences(flac)
    assert silences, "expected the 1 s gaps to be detected"
    segs = plan_segments(duration, 20, 1, silences)
    assert len(segs) == 3
    # cuts snapped to the gaps around 19.5 s and 39.5 s
    assert abs(segs[0].end - 19.5) < 0.6 and abs(segs[1].end - 39.5) < 0.6
    data = audio.extract_segment(flac, segs[1], tmp_path / "s.flac")
    assert data[:4] == b"fLaC"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest  # original untouched


def test_undecodable_file(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFF....not really audio")
    with pytest.raises(DecodeError):
        audio.normalize(bad, tmp_path / "out.flac")
