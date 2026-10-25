import base64

import httpx
import pytest
import respx

from crisp.providers import ProviderError
from crisp.providers.http import retry_after_seconds
from crisp.providers.openrouter import OpenRouterRefiner, OpenRouterTranscriber

BASE = "https://openrouter.test/api/v1"
STT = f"{BASE}/audio/transcriptions"
CHAT = f"{BASE}/chat/completions"


class Sleeps(list):
    async def __call__(self, s):
        self.append(s)


@pytest.fixture
def sleeps():
    return Sleeps()


@pytest.fixture
async def client():
    async with httpx.AsyncClient(headers={"Authorization": "Bearer k"}) as c:
        yield c


def stt(client, sleeps, retries=3, model="mistralai/voxtral-small-transcribe-3-0"):
    return OpenRouterTranscriber(client, BASE, model, retries, sleep=sleeps)


@respx.mock
async def test_transcription_request_shape(client, sleeps):
    route = respx.post(STT).mock(return_value=httpx.Response(200, json={"text": " Hallo Welt ",
                                                                         "usage": {}}))
    t = stt(client, sleeps)
    chunk = await t.transcribe(b"fLaCdata", "flac", "de")
    assert chunk.text == "Hallo Welt"
    body = route.calls.last.request
    payload = httpx.Response(200, content=body.content).json()
    assert payload["model"] == "mistralai/voxtral-small-transcribe-3-0"
    assert payload["language"] == "de"
    assert payload["input_audio"] == {"data": base64.b64encode(b"fLaCdata").decode(),
                                      "format": "flac"}
    assert body.headers["Authorization"] == "Bearer k"
    assert t.model == "mistralai/voxtral-small-transcribe-3-0"


@respx.mock
async def test_429_then_success_honors_retry_after(client, sleeps):
    respx.post(STT).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"text": "ok"}),
    ])
    assert (await stt(client, sleeps).transcribe(b"x", "flac", "de")).text == "ok"
    assert sleeps == [7.0]


@respx.mock
async def test_repeated_503_fails_after_retries(client, sleeps):
    route = respx.post(STT).mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError) as e:
        await stt(client, sleeps, retries=2).transcribe(b"x", "flac", "de")
    assert e.value.kind == "transient" and e.value.status == 503
    assert route.call_count == 3 and len(sleeps) == 2


@respx.mock
async def test_network_error_retried(client, sleeps):
    respx.post(STT).mock(side_effect=[httpx.ConnectError("down"),
                                      httpx.Response(200, json={"text": "ok"})])
    assert (await stt(client, sleeps).transcribe(b"x", "flac", "de")).text == "ok"
    assert len(sleeps) == 1


@respx.mock
@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (400, "invalid")])
async def test_client_errors_not_retried(client, sleeps, status, kind):
    route = respx.post(STT).mock(return_value=httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(ProviderError) as e:
        await stt(client, sleeps).transcribe(b"x", "flac", "de")
    assert e.value.kind == kind and route.call_count == 1 and sleeps == []


@respx.mock
async def test_refiner_request(client, sleeps):
    route = respx.post(CHAT).mock(return_value=httpx.Response(
        200, json={"choices": [{"message": {"content": " Hallo Welt. "}}]}))
    r = OpenRouterRefiner(client, BASE, "deepseek/x", 1, sleep=sleeps)
    assert await r.refine("hallo welt", "PROMPT") == "Hallo Welt."
    payload = httpx.Response(200, content=route.calls.last.request.content).json()
    assert payload["temperature"] == 0
    assert payload["messages"][0] == {"role": "system", "content": "PROMPT"}
    assert payload["messages"][1] == {"role": "user", "content": "hallo welt"}


def test_retry_after_parsing():
    assert retry_after_seconds("3") == 3.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("garbage") is None
