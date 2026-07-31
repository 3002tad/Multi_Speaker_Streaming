"""Ground-truth helpers for automated ASR regression checks."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class TranscriptTruth:
    voice: str
    transcript: str
    start_seconds: float | None = None
    end_seconds: float | None = None


def load_transcript_truth(path: Path) -> list[TranscriptTruth]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, skipinitialspace=True)
        header = next(reader, None)
        normalized_header = {
            (value or "").strip(): index
            for index, value in enumerate(header or [])
        }
        required = {"voice", "transcript"}
        if not required.issubset(normalized_header):
            raise ValueError("truth.csv must contain voice and transcript columns")
        has_time_range = {
            "start_seconds",
            "end_seconds",
        }.issubset(normalized_header)
        rows = []
        for fields in reader:
            if not fields or not any(item.strip() for item in fields):
                continue
            voice = fields[normalized_header["voice"]].strip()
            if has_time_range:
                # Joining the middle fields also accepts an unquoted comma
                # in the transcript (the current local truth.csv uses one).
                start_value = fields[-2]
                end_value = fields[-1]
                transcript_fields = fields[1:-2]
                start_seconds = _optional_float(start_value)
                end_seconds = _optional_float(end_value)
            else:
                transcript_fields = fields[1:]
                start_seconds = None
                end_seconds = None
            rows.append(
                TranscriptTruth(
                    voice=voice,
                    transcript=",".join(transcript_fields).strip(),
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
    if not rows or any(not row.voice or not row.transcript for row in rows):
        raise ValueError("truth.csv contains an empty voice or transcript")
    return rows


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError("truth.csv time offsets cannot be negative")
    return parsed


def normalize_transcript(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def _edit_distance(reference: Iterable[str], hypothesis: Iterable[str]) -> int:
    reference_items = list(reference)
    previous = list(range(len(reference_items) + 1))
    for row, hypothesis_item in enumerate(hypothesis, start=1):
        current = [row]
        for column, reference_item in enumerate(reference_items, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1]
                    + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = normalize_transcript(reference)
    if not reference_words:
        return 0.0 if not normalize_transcript(hypothesis) else 1.0
    return _edit_distance(
        reference_words, normalize_transcript(hypothesis)
    ) / len(reference_words)


def word_error_breakdown(reference: str, hypothesis: str) -> dict[str, int]:
    """Return substitution/deletion/insertion counts for ASR diagnosis."""
    reference_words = normalize_transcript(reference)
    hypothesis_words = normalize_transcript(hypothesis)
    # Each cell stores (cost, substitutions, deletions, insertions).  Keeping
    # the operation counts makes ties deterministic and useful for decoder A/B.
    rows = len(reference_words)
    columns = len(hypothesis_words)
    table: list[list[tuple[int, int, int, int]]] = [
        [(0, 0, 0, column) for column in range(columns + 1)]
    ]
    for row in range(1, rows + 1):
        table.append(
            [(row, 0, row, 0)]
            + [(0, 0, 0, 0) for _ in range(columns)]
        )
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            if reference_words[row - 1] == hypothesis_words[column - 1]:
                diagonal = table[row - 1][column - 1]
                candidates = [diagonal]
            else:
                previous = table[row - 1][column - 1]
                candidates = [
                    (
                        previous[0] + 1,
                        previous[1] + 1,
                        previous[2],
                        previous[3],
                    ),
                ]
            deletion = table[row - 1][column]
            candidates.append(
                (deletion[0] + 1, deletion[1], deletion[2] + 1, deletion[3])
            )
            insertion = table[row][column - 1]
            candidates.append(
                (insertion[0] + 1, insertion[1], insertion[2], insertion[3] + 1)
            )
            table[row][column] = min(candidates)
    result = table[rows][columns]
    return {
        "substitutions": result[1],
        "deletions": result[2],
        "insertions": result[3],
    }


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference_chars = list(" ".join(normalize_transcript(reference)))
    hypothesis_chars = list(" ".join(normalize_transcript(hypothesis)))
    if not reference_chars:
        return 0.0 if not hypothesis_chars else 1.0
    return _edit_distance(reference_chars, hypothesis_chars) / len(
        reference_chars
    )
