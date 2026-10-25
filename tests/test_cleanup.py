import pytest

from crisp.cleanup import DEFAULT_PROMPT, clean_transcript, plausible, split_pieces
from crisp.providers import ProviderError


class FakeRefiner:
    name, model = "fake", "fake/model"

    def __init__(self, fn=None, fail=False):
        self.calls = []
        self.fn = fn or (lambda t: t.capitalize())
        self.fail = fail

    async def refine(self, text, prompt):
        self.calls.append((text, prompt))
        if self.fail:
            raise ProviderError("down", "transient", 503)
        return self.fn(text)


def test_default_prompt_rules():
    p = DEFAULT_PROMPT.lower()
    assert "nicht zusammenfassen" in p and "nicht übersetzen" in p
    assert "nichts hinzufügen" in p and "nichts weglassen" in p


def test_split_on_sentences_and_reassemble():
    text = " ".join(f"Satz nummer {i}." for i in range(200))
    pieces = split_pieces(text, 500)
    assert len(pieces) > 1
    assert all(len(p) <= 500 for p in pieces)
    assert " ".join(pieces) == text
    assert all(p.endswith(".") for p in pieces)


def test_split_unpunctuated_text_on_words():
    text = " ".join(["wort"] * 1000)
    pieces = split_pieces(text, 600)
    assert all(len(p) <= 600 for p in pieces)
    assert " ".join(pieces) == text


async def test_long_input_split_and_ordered():
    text = " ".join(f"satz {i}." for i in range(400))
    r = FakeRefiner(fn=lambda t: t.upper())
    res = await clean_transcript(r, text, prompt=None, max_chars=500)
    assert len(r.calls) > 1
    assert res.text.replace("\n\n", " ") == text.upper()
    assert all(prompt == DEFAULT_PROMPT for _, prompt in r.calls)


async def test_custom_prompt_used():
    r = FakeRefiner()
    await clean_transcript(r, "hallo", prompt="MEIN PROMPT", max_chars=6000)
    assert r.calls[0][1] == "MEIN PROMPT"


async def test_failure_falls_back_to_raw():
    res = await clean_transcript(FakeRefiner(fail=True), "hallo", prompt=None, max_chars=6000)
    assert res.text is None and "down" in res.error


@pytest.mark.parametrize("out", ["x", "hallo welt " * 10])
async def test_implausible_output_discarded(out):
    res = await clean_transcript(FakeRefiner(fn=lambda t: out), "hallo welt", prompt=None,
                                 max_chars=6000)
    assert res.text is None and "implausible" in res.error


def test_plausible_bounds():
    assert plausible("a" * 100, "a" * 50) and plausible("a" * 100, "a" * 200)
    assert not plausible("a" * 100, "a" * 49) and not plausible("a" * 100, "a" * 201)
