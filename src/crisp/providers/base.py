"""Provider interfaces and the error type shared by all adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ErrorKind = Literal["auth", "invalid", "transient"]


class ProviderError(Exception):
    """A provider request failed. ``kind`` tells whether retrying can help."""

    def __init__(self, message: str, kind: ErrorKind, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class TranscriptChunk:
    text: str


class Transcriber(Protocol):
    name: str
    model: str

    async def transcribe(self, audio: bytes, fmt: str, language: str | None) -> TranscriptChunk: ...


class TextRefiner(Protocol):
    name: str
    model: str

    async def refine(self, text: str, prompt: str) -> str: ...
