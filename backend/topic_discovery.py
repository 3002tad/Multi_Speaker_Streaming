"""Evidence-gated topic discovery from finalized raw ASR turns.

This module does not call an LLM itself.  It owns the deterministic parts of
the workflow:

* buffer one observation per room-wide turn;
* decide when a fixed bootstrap/refresh window is ready;
* prepare bounded raw evidence and repeated n-gram hints;
* validate a structured model response against the original raw transcript.

Only validated terms become :class:`GlossaryEntry` objects.  A topic label is
diagnostic metadata and never becomes a decoder hotword by itself.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Iterable

from backend.adaptive_dictionary import GlossaryEntry


_WORD_RE = re.compile(r"[^\W_]+(?:[-.][^\W_]+)*", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_STOPWORDS = {
    "ai",
    "anh",
    "bây",
    "bây giờ",
    "bị",
    "biết",
    "các",
    "cái",
    "chị",
    "cho",
    "chúng",
    "chúng ta",
    "có",
    "còn",
    "cũng",
    "của",
    "dạ",
    "do",
    "đang",
    "đây",
    "đó",
    "được",
    "em",
    "gì",
    "hay",
    "họ",
    "không",
    "là",
    "làm",
    "lại",
    "mà",
    "mình",
    "một",
    "mọi",
    "này",
    "nên",
    "nó",
    "như",
    "nhưng",
    "những",
    "nói",
    "rồi",
    "sẽ",
    "ta",
    "thì",
    "theo",
    "trong",
    "tôi",
    "và",
    "về",
    "với",
    "ở",
}
_GENERIC_TERMS = {
    "công việc",
    "cuộc họp",
    "dữ liệu",
    "giải pháp",
    "hệ thống",
    "kế hoạch",
    "nội dung",
    "phần mềm",
    "thông tin",
    "thời gian",
    "thực hiện",
    "triển khai",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalise_text(value: str) -> str:
    words = _WORD_RE.findall(value.casefold())
    return " ".join(words)


def _clean_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE_RE.sub(" ", value).strip()[:limit]


@dataclass(frozen=True)
class TopicObservation:
    turn_id: str
    raw_text: str
    speaker: str
    timestamp: float


@dataclass(frozen=True)
class TopicSnapshot:
    version: int
    topic: str
    topic_confidence: float
    created_at: str
    evidence_turn_ids: tuple[str, ...]
    entries: tuple[GlossaryEntry, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "topic": self.topic,
            "topic_confidence": self.topic_confidence,
            "created_at": self.created_at,
            "evidence_turn_ids": list(self.evidence_turn_ids),
            "entries": [entry.to_json() for entry in self.entries],
        }


class TopicDiscoveryWindow:
    """Accumulate raw turns and validate periodic topic-model proposals."""

    def __init__(
        self,
        *,
        state_path: Path,
        bootstrap_seconds: float = 90.0,
        refresh_seconds: float = 60.0,
        minimum_turns: int = 6,
        minimum_evidence_turns: int = 2,
        minimum_topic_confidence: float = 0.65,
        minimum_term_confidence: float = 0.88,
        term_ttl_hours: float = 0.25,
        maximum_terms: int = 24,
        maximum_context_chars: int = 6000,
        maximum_window_seconds: float = 180.0,
    ) -> None:
        self.state_path = state_path
        self.bootstrap_seconds = max(0.0, bootstrap_seconds)
        self.refresh_seconds = max(1.0, refresh_seconds)
        self.minimum_turns = max(1, minimum_turns)
        self.minimum_evidence_turns = max(1, minimum_evidence_turns)
        self.minimum_topic_confidence = min(
            1.0, max(0.0, minimum_topic_confidence)
        )
        self.minimum_term_confidence = min(
            1.0, max(0.0, minimum_term_confidence)
        )
        self.term_ttl_hours = max(0.05, term_ttl_hours)
        self.maximum_terms = max(1, maximum_terms)
        self.maximum_context_chars = max(500, maximum_context_chars)
        self.maximum_window_seconds = max(30.0, maximum_window_seconds)
        self._observations: OrderedDict[str, TopicObservation] = OrderedDict()
        self._participants: set[str] = set()
        self._started_at = 0.0
        self._last_analysis_at: float | None = None
        self._version = 0
        self._snapshot: TopicSnapshot | None = None

    @property
    def snapshot(self) -> TopicSnapshot | None:
        return self._snapshot

    @property
    def participant_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._participants, key=str.casefold))

    def reset(
        self,
        *,
        started_at: float,
        participant_names: Iterable[str] = (),
    ) -> None:
        self._observations.clear()
        participants: dict[str, str] = {}
        for raw_name in participant_names:
            name = _clean_text(raw_name, limit=80)
            if name:
                participants.setdefault(name.casefold(), name)
        self._participants = set(participants.values())
        self._started_at = max(0.0, float(started_at))
        self._last_analysis_at = None
        self._version = 0
        self._snapshot = None
        self._write_state()

    def add_participant(self, display_name: str) -> bool:
        name = _clean_text(display_name, limit=80)
        if not name or any(
            existing.casefold() == name.casefold()
            for existing in self._participants
        ):
            return False
        self._participants.add(name)
        self._write_state()
        return True

    def observe(
        self,
        *,
        turn_id: str,
        raw_text: str,
        speaker: str,
        timestamp: float,
    ) -> bool:
        """Store one raw winner per global turn and report analysis readiness."""
        turn_id = _clean_text(turn_id, limit=120)
        raw_text = _clean_text(raw_text, limit=1200)
        speaker = _clean_text(speaker, limit=80)
        if not turn_id or not raw_text:
            return False
        observation = TopicObservation(
            turn_id=turn_id,
            raw_text=raw_text,
            speaker=speaker,
            timestamp=float(timestamp),
        )
        # A stronger/late mic candidate can replace the same room-wide turn.
        self._observations[turn_id] = observation
        self._observations.move_to_end(turn_id)
        oldest_timestamp = float(timestamp) - self.maximum_window_seconds
        for old_turn_id, old_observation in tuple(
            self._observations.items()
        ):
            if old_observation.timestamp >= oldest_timestamp:
                continue
            self._observations.pop(old_turn_id, None)
        while len(self._observations) > 80:
            self._observations.popitem(last=False)
        return self.ready(timestamp=float(timestamp))

    def ready(self, *, timestamp: float) -> bool:
        if len(self._observations) < self.minimum_turns:
            return False
        if timestamp - self._started_at < self.bootstrap_seconds:
            return False
        if self._last_analysis_at is None:
            return True
        return timestamp - self._last_analysis_at >= self.refresh_seconds

    def mark_analysis_attempt(self, *, timestamp: float) -> None:
        self._last_analysis_at = float(timestamp)

    def _candidate_hints(self) -> tuple[str, ...]:
        occurrences: Counter[str] = Counter()
        turns: dict[str, set[str]] = defaultdict(set)
        speakers: dict[str, set[str]] = defaultdict(set)
        surfaces: dict[str, str] = {}
        for observation in self._observations.values():
            tokens = _WORD_RE.findall(observation.raw_text)
            for size in (1, 2, 3):
                for start in range(0, len(tokens) - size + 1):
                    surface = " ".join(tokens[start : start + size])
                    key = surface.casefold()
                    if len(key) < 3 or len(key) > 60:
                        continue
                    if all(token.casefold() in _STOPWORDS for token in tokens[start : start + size]):
                        continue
                    occurrences[key] += 1
                    turns[key].add(observation.turn_id)
                    if observation.speaker:
                        speakers[key].add(observation.speaker.casefold())
                    surfaces.setdefault(key, surface)
        ranked = sorted(
            (
                key
                for key in occurrences
                if len(turns[key]) >= self.minimum_evidence_turns
            ),
            key=lambda key: (
                len(turns[key]),
                len(speakers[key]),
                occurrences[key],
                len(key.split()),
            ),
            reverse=True,
        )
        return tuple(surfaces[key] for key in ranked[:40])

    def analysis_payload(self) -> dict[str, Any]:
        observations = list(self._observations.values())
        selected: list[dict[str, Any]] = []
        used_chars = 0
        for item in reversed(observations):
            row = asdict(item)
            row_size = len(item.raw_text) + len(item.turn_id) + len(item.speaker)
            if selected and used_chars + row_size > self.maximum_context_chars:
                break
            selected.append(row)
            used_chars += row_size
        selected.reverse()
        return {
            "session_started_at": self._started_at,
            "participants": list(self.participant_names),
            "turns": selected,
            "repeated_phrase_hints": list(self._candidate_hints()),
        }

    def accept_model_response(
        self,
        payload: Any,
        *,
        now: datetime | None = None,
    ) -> TopicSnapshot:
        """Validate a model response and return a fail-closed snapshot."""
        if not isinstance(payload, dict):
            raise ValueError("topic response must be a JSON object")
        topic = _clean_text(payload.get("topic"), limit=180)
        try:
            topic_confidence = float(payload.get("topic_confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid topic confidence") from exc
        topic_confidence = min(1.0, max(0.0, topic_confidence))
        if not topic or topic_confidence < self.minimum_topic_confidence:
            topic = ""

        observations = dict(self._observations)
        participant_keys = {
            _normalise_text(name) for name in self._participants
        }
        now = now or _utc_now()
        expires_at = (now + timedelta(hours=self.term_ttl_hours)).isoformat()
        entries: list[GlossaryEntry] = []
        seen: set[str] = set()
        raw_terms = payload.get("terms", [])
        if not isinstance(raw_terms, list):
            raw_terms = []
        for raw in raw_terms[: self.maximum_terms * 2]:
            if not isinstance(raw, dict):
                continue
            canonical = _clean_text(raw.get("canonical"), limit=80)
            if not canonical or len(canonical.split()) > 5:
                continue
            key = canonical.casefold()
            normalised_canonical = _normalise_text(canonical)
            if (
                key in seen
                or key in _STOPWORDS
                or normalised_canonical in _GENERIC_TERMS
                or normalised_canonical in participant_keys
            ):
                continue
            try:
                model_confidence = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if model_confidence < self.minimum_term_confidence:
                continue
            raw_aliases = raw.get("observed_variants", [])
            if not isinstance(raw_aliases, list):
                continue
            aliases = [
                _clean_text(alias, limit=80)
                for alias in raw_aliases
                if isinstance(alias, str)
                and len("".join(_WORD_RE.findall(alias))) >= 3
            ]
            aliases = list(dict.fromkeys(alias for alias in aliases if alias))
            if not aliases:
                continue
            raw_evidence_ids = raw.get("evidence_turn_ids", [])
            if not isinstance(raw_evidence_ids, list):
                continue
            evidence_ids = [
                str(item)
                for item in raw_evidence_ids
                if str(item) in observations
            ]
            valid_evidence: list[str] = []
            for turn_id in dict.fromkeys(evidence_ids):
                observed_text = _normalise_text(observations[turn_id].raw_text)
                if any(
                    _normalise_text(alias) in observed_text
                    for alias in aliases
                    if _normalise_text(alias)
                ):
                    valid_evidence.append(turn_id)
            if len(valid_evidence) < self.minimum_evidence_turns:
                continue
            # Evidence count caps model confidence.  Two independent turns can
            # activate a hotword; further turns make it more stable.
            evidence_cap = min(0.98, 0.84 + 0.04 * len(valid_evidence))
            confidence = round(
                min(1.0, model_confidence, evidence_cap), 4
            )
            if confidence < self.minimum_term_confidence:
                continue
            seen.add(key)
            entries.append(
                GlossaryEntry(
                    canonical=canonical,
                    aliases=tuple(
                        alias
                        for alias in aliases
                        if alias.casefold() != canonical.casefold()
                    ),
                    source="topic_discovery",
                    confidence=confidence,
                    last_seen=now.isoformat(),
                    expires_at=expires_at,
                )
            )
            if len(entries) >= self.maximum_terms:
                break

        self._version += 1
        snapshot = TopicSnapshot(
            version=self._version,
            topic=topic,
            topic_confidence=topic_confidence if topic else 0.0,
            created_at=now.isoformat(),
            evidence_turn_ids=tuple(observations),
            entries=tuple(entries),
        )
        self._snapshot = snapshot
        self._write_state()
        return snapshot

    def status(self) -> dict[str, Any]:
        return {
            "started_at": self._started_at,
            "turn_count": len(self._observations),
            "participant_names": list(self.participant_names),
            "last_analysis_at": self._last_analysis_at,
            "ready": self.ready(
                timestamp=max(
                    (item.timestamp for item in self._observations.values()),
                    default=self._started_at,
                )
            ),
            "snapshot": self._snapshot.to_json() if self._snapshot else None,
        }

    def _write_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "started_at": self._started_at,
                    "participant_names": list(self.participant_names),
                    "last_analysis_at": self._last_analysis_at,
                    "snapshot": (
                        self._snapshot.to_json() if self._snapshot else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)
