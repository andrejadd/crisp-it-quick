"""Optional LLM pass that fixes punctuation and paragraphs of a raw transcript."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from crisp.providers import ProviderError, TextRefiner

log = logging.getLogger("crisp.cleanup")

DEFAULT_PROMPT = """\
Du bekommst das Rohtranskript einer deutschen Sprachaufnahme.
Korrigiere ausschließlich Zeichensetzung, Groß- und Kleinschreibung und füge sinnvolle Absätze ein.
Regeln:
- Nicht zusammenfassen, nicht übersetzen, nichts hinzufügen und nichts weglassen.
- Wortlaut und Sprache unverändert beibehalten, auch Füllwörter und Wiederholungen.
- Gib ausschließlich den bereinigten Text zurück, ohne Einleitung, Kommentar oder Formatierung.
"""

MIN_RATIO = 0.5
MAX_RATIO = 2.0

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_pieces(text: str, max_chars: int) -> list[str]:
    """Split on sentence boundaries (falling back to whitespace) into pieces <= max_chars."""
    units = _SENTENCE_END.split(text.strip())
    pieces: list[str] = []
    current = ""
    for unit in units:
        # Unpunctuated raw text can be one huge "sentence": break it on whitespace.
        words = [unit] if len(unit) <= max_chars else unit.split()
        for w in words:
            candidate = f"{current} {w}" if current else w
            if len(candidate) <= max_chars or not current:
                current = candidate
            else:
                pieces.append(current)
                current = w
    if current:
        pieces.append(current)
    return pieces


def plausible(raw: str, cleaned: str) -> bool:
    if not raw:
        return True
    ratio = len(cleaned) / len(raw)
    return MIN_RATIO <= ratio <= MAX_RATIO


@dataclass(frozen=True)
class CleanupResult:
    text: str | None  # None when cleanup produced nothing usable
    error: str | None


async def clean_transcript(
    refiner: TextRefiner, raw: str, *, prompt: str | None, max_chars: int
) -> CleanupResult:
    """Never raises for provider failures: falls back to ``text=None`` with an error."""
    if not raw.strip():
        return CleanupResult(text=None, error=None)
    out: list[str] = []
    for i, piece in enumerate(split_pieces(raw, max_chars)):
        try:
            cleaned = await refiner.refine(piece, prompt or DEFAULT_PROMPT)
        except ProviderError as e:
            log.warning("cleanup failed, keeping raw transcript: %s", e)
            return CleanupResult(text=None, error=str(e))
        if not plausible(piece, cleaned):
            msg = (
                f"cleanup output for piece {i + 1} has implausible length "
                f"({len(cleaned)} vs {len(piece)} chars); keeping raw transcript"
            )
            log.warning(msg)
            return CleanupResult(text=None, error=msg)
        out.append(cleaned)
    return CleanupResult(text="\n\n".join(out), error=None)
