"""HTTP calls with exponential backoff for transient failures."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from crisp.providers.base import ProviderError

log = logging.getLogger("crisp.providers")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
NO_RETRY_AUTH = {401, 403}
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0

Sleep = Callable[[float], Awaitable[None]]


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def backoff_delay(attempt: int) -> float:
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)
    return delay * (0.5 + random.random() / 2)


def _body_snippet(resp: httpx.Response) -> str:
    try:
        return resp.text[:300]
    except Exception:
        return "<unreadable body>"


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    retries: int,
    sleep: Sleep = asyncio.sleep,
    what: str = "request",
) -> dict:
    """POST ``payload``; retry network errors, timeouts, 408/429/5xx; fail fast on 4xx."""
    attempt = 0
    while True:
        try:
            resp = await client.post(url, json=payload)
        except httpx.TransportError as e:  # includes timeouts and connection errors
            err = ProviderError(f"{what}: {type(e).__name__}: {e}", "transient")
            wait = None
        else:
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError as e:
                    raise ProviderError(f"{what}: response is not JSON", "transient") from e
            status = resp.status_code
            msg = f"{what}: HTTP {status}: {_body_snippet(resp)}"
            if status in NO_RETRY_AUTH:
                raise ProviderError(msg, "auth", status)
            if status not in RETRYABLE_STATUS and status < 500:
                raise ProviderError(msg, "invalid", status)
            err = ProviderError(msg, "transient", status)
            wait = retry_after_seconds(resp.headers.get("Retry-After"))
        if attempt >= retries:
            raise err
        delay = wait if wait is not None else backoff_delay(attempt)
        delay = min(delay, BACKOFF_CAP_S * 5)
        log.warning("%s; retrying in %.1fs (%d/%d)", err, delay, attempt + 1, retries)
        await sleep(delay)
        attempt += 1
