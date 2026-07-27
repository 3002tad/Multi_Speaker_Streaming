"""Fast deterministic corrections for recurring meeting-domain ASR errors."""

from __future__ import annotations

import re


_MEETING_TERM_PATTERNS = (
    (r"\bmột năm chấm hai\b", "mục 5.2"),
    (r"\badolf\s+sor\w*\b", "Hadoop Storage"),
    (r"\bhpase\b", "HBase"),
    (r"\baptoris\b", "Architecture"),
    (r"\b(?:lồng|làm)\s+quét\b", "làm web"),
    (r"\bhệ\s+thống\s+tự\s+tin\b", "hệ thống tập tin"),
)


def normalize_meeting_terms(text: str) -> str:
    """Correct high-confidence recurring errors before optional LLM cleanup."""
    normalized = text
    for pattern, replacement in _MEETING_TERM_PATTERNS:
        normalized = re.sub(
            pattern,
            replacement,
            normalized,
            flags=re.IGNORECASE,
        )

    # The Vietnamese Zipformer often decodes the acronym pair in this exact
    # context as "HD và HPASE". Avoid globally rewriting every standalone HD.
    normalized = re.sub(
        r"\b(?:HDX|HD|HAI\s+D\s+S|H\s+D\s+S?)\s+và\s+HBase\b",
        "HDFS và HBase",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b(?:rồi\s+)?H\s+D(?:\s+S)?\s+là\b",
        "HDFS là",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized
