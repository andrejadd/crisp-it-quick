"""Provider registry: maps config provider names to adapter factories."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from crisp.config import Settings
from crisp.providers.base import ProviderError, TextRefiner, Transcriber, TranscriptChunk
from crisp.providers.openrouter import OpenRouterRefiner, OpenRouterTranscriber, make_client

__all__ = [
    "ProviderError",
    "TextRefiner",
    "Transcriber",
    "TranscriptChunk",
    "build_clients",
    "build_refiner",
    "build_transcriber",
]

TranscriberFactory = Callable[[Settings, httpx.AsyncClient], Transcriber]
RefinerFactory = Callable[[Settings, httpx.AsyncClient], TextRefiner]

TRANSCRIBERS: dict[str, TranscriberFactory] = {
    "openrouter": lambda s, c: OpenRouterTranscriber(
        c, s.openrouter.base_url, s.transcription.model, s.openrouter.request_retries
    ),
}
REFINERS: dict[str, RefinerFactory] = {
    "openrouter": lambda s, c: OpenRouterRefiner(
        c, s.openrouter.base_url, s.cleanup.model, s.openrouter.request_retries
    ),
}


def build_clients(settings: Settings) -> dict[str, httpx.AsyncClient]:
    clients = {}
    if settings.uses_openrouter():
        key = settings.openrouter_api_key.get_secret_value()
        clients["openrouter"] = make_client(key, settings.openrouter.timeout_s)
    return clients


def build_transcriber(settings: Settings, clients: dict[str, httpx.AsyncClient]) -> Transcriber:
    provider = settings.transcription.provider
    return TRANSCRIBERS[provider](settings, clients[provider])


def build_refiner(settings: Settings, clients: dict[str, httpx.AsyncClient]) -> TextRefiner | None:
    if not settings.cleanup.enabled:
        return None
    provider = settings.cleanup.provider
    return REFINERS[provider](settings, clients[provider])
