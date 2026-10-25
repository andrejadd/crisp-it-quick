"""OpenRouter adapters: audio transcription and chat-based transcript cleanup."""

from __future__ import annotations

import asyncio
import base64

import httpx

from crisp.providers.base import ProviderError, TranscriptChunk
from crisp.providers.http import Sleep, post_json


def make_client(api_key: str, timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Title": "crisp-it-quick",
        },
        timeout=httpx.Timeout(timeout_s, connect=15.0),
    )


class OpenRouterTranscriber:
    name = "openrouter"

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, retries: int,
                 sleep: Sleep = asyncio.sleep):
        self._client = client
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self.model = model
        self._retries = retries
        self._sleep = sleep

    async def transcribe(self, audio: bytes, fmt: str, language: str | None) -> TranscriptChunk:
        payload: dict = {
            "model": self.model,
            "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": fmt},
        }
        if language:
            payload["language"] = language
        data = await post_json(self._client, self._url, payload, retries=self._retries,
                               sleep=self._sleep, what=f"transcription ({self.model})")
        text = data.get("text")
        if not isinstance(text, str):
            raise ProviderError(f"transcription ({self.model}): response has no text", "transient")
        return TranscriptChunk(text=text.strip())


class OpenRouterRefiner:
    name = "openrouter"

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, retries: int,
                 sleep: Sleep = asyncio.sleep):
        self._client = client
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._retries = retries
        self._sleep = sleep

    async def refine(self, text: str, prompt: str) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        }
        data = await post_json(self._client, self._url, payload, retries=self._retries,
                               sleep=self._sleep, what=f"cleanup ({self.model})")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"cleanup ({self.model}): unexpected response shape",
                                "transient") from e
        if not isinstance(content, str):
            raise ProviderError(f"cleanup ({self.model}): empty response", "transient")
        return content.strip()
