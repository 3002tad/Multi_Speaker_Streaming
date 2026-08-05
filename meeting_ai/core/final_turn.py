"""Safe helpers for a deferred full-turn ASR replay.

The streaming decoder remains the source of the low-latency draft.  A replay
can occasionally retain a word that was pruned while an utterance was still
arriving, but it is not a second acoustic model and must never freely rewrite
the transcript.  This module contains only deterministic policy so it can be
unit-tested without loading ASR models.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re


_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


@dataclass(frozen=True)
class RedecodeDecision:
    """The evidence behind selecting a final-turn replay candidate."""

    text: str
    selected_redecode: bool
    reason: str
    overlap: float
    word_ratio: float


def choose_redecode_transcript(
    streaming_text: str,
    redecode_text: str,
    *,
    minimum_overlap: float = 0.72,
    minimum_word_gain: int = 1,
    maximum_word_ratio: float = 1.35,
) -> RedecodeDecision:
    """Accept only a compatible replay that restores at least one word.

    Word overlap is deliberately based on multiset recall of the streaming
    draft.  A replay that replaces most words, becomes much longer, or merely
    produces a different same-length hypothesis stays diagnostic-only.  This
    makes the feature safe before a stronger offline ASR is plugged in.
    """
    streaming = streaming_text.strip()
    replayed = redecode_text.strip()
    base = _tokens(streaming)
    candidate = _tokens(replayed)
    if not candidate:
        return RedecodeDecision(streaming, False, "empty_redecode", 0.0, 0.0)
    if not base:
        return RedecodeDecision(replayed, True, "streaming_empty", 1.0, 1.0)

    retained = sum((Counter(base) & Counter(candidate)).values())
    overlap = retained / len(base)
    ratio = len(candidate) / len(base)
    gained_words = len(candidate) - len(base)
    if replayed.casefold() == streaming.casefold():
        return RedecodeDecision(streaming, False, "same_text", overlap, ratio)
    if overlap < max(0.0, min(1.0, minimum_overlap)):
        return RedecodeDecision(streaming, False, "low_overlap", overlap, ratio)
    if ratio > max(1.0, maximum_word_ratio):
        return RedecodeDecision(streaming, False, "too_long", overlap, ratio)
    if gained_words < max(1, int(minimum_word_gain)):
        return RedecodeDecision(streaming, False, "no_word_gain", overlap, ratio)
    return RedecodeDecision(replayed, True, "compatible_word_gain", overlap, ratio)
