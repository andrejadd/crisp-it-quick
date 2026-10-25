"""Rendering and atomically writing the markdown transcript file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

NO_SPEECH = "_No speech detected._"


@dataclass(frozen=True)
class TranscriptDoc:
    recorded_at: datetime
    source_file: str
    audio_file: str
    duration_s: float | None
    stt_provider: str
    stt_model: str
    cleanup_model: str | None
    created_at: datetime
    raw_text: str
    cleaned_text: str | None


def render(doc: TranscriptDoc) -> str:
    front = {
        "recorded_at": doc.recorded_at.isoformat(),
        "source_file": doc.source_file,
        "audio_file": doc.audio_file,
        "duration_s": round(doc.duration_s, 1) if doc.duration_s is not None else None,
        "transcription_provider": doc.stt_provider,
        "transcription_model": doc.stt_model,
        "cleanup_model": doc.cleanup_model if doc.cleaned_text else "none",
        "created_at": doc.created_at.isoformat(timespec="seconds"),
    }
    header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip()
    title = f"# Recording {doc.recorded_at:%Y-%m-%d %H:%M:%S}"
    raw = doc.raw_text.strip()
    parts = [f"---\n{header}\n---", title]
    if not raw:
        parts.append(NO_SPEECH)
    elif doc.cleaned_text:
        parts += [doc.cleaned_text.strip(), "## Raw transcript", raw]
    else:
        parts.append(raw)
    return "\n\n".join(parts) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
