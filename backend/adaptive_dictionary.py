"""Safe, persistent meeting glossary shared by ASR and phonetic recovery.

The dictionary is deliberately conservative: its entries are evidence-backed
terms, not corrections inferred from a raw ASR turn.  It produces two views:

* canonical-only hotwords for contextual Zipformer decoding;
* canonical terms plus trusted pronunciation aliases for phonetic recovery.

This separation prevents an alias such as a common ASR mistake from being
emitted by the decoder while still allowing it to be recognised by the final
phonetic gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Iterable


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class GlossaryEntry:
    """One canonical meeting term and the evidence required to use it."""

    canonical: str
    aliases: tuple[str, ...]
    source: str
    confidence: float
    last_seen: str
    expires_at: str | None = None

    @property
    def expires_at_datetime(self) -> datetime | None:
        return _parse_time(self.expires_at)

    def is_active(self, *, now: datetime | None = None) -> bool:
        now = now or _utc_now()
        expires_at = self.expires_at_datetime
        return bool(self.canonical.strip()) and (
            expires_at is None or expires_at > now
        )

    def is_trusted_for_hotword(
        self, minimum_confidence: float, *, now: datetime | None = None
    ) -> bool:
        return self.is_active(now=now) and self.confidence >= minimum_confidence

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["aliases"] = list(self.aliases)
        return payload


@dataclass(frozen=True)
class HotwordArtifacts:
    """Paths consumed by sherpa-onnx for BPE contextual decoding."""

    hotwords_path: Path
    bpe_vocab_path: Path
    phrase_count: int


class AdaptiveDictionary:
    """Merge static seed terms with TTL-bound persistent meeting terms.

    ``state_path`` stores only dynamic, independently sourced entries.  The
    read-only seed lexicon remains in the repository/runtime and is reloaded on
    every process start, so an expired dynamic term can never remove it.
    """

    def __init__(
        self,
        *,
        state_path: Path,
        seed_entries: Iterable[GlossaryEntry] = (),
        manual_entries: Iterable[GlossaryEntry] = (),
        dynamic_entries: Iterable[GlossaryEntry] = (),
    ) -> None:
        self.state_path = state_path
        self._seed_entries = tuple(seed_entries)
        self._manual_entries = tuple(manual_entries)
        self._dynamic_entries = tuple(dynamic_entries)

    @classmethod
    def from_paths(
        cls,
        *,
        seed_path: Path | None,
        state_path: Path,
        manual_path: Path | None = None,
    ) -> "AdaptiveDictionary":
        return cls(
            state_path=state_path,
            seed_entries=cls._read_seed_entries(seed_path),
            manual_entries=cls._read_manual_entries(manual_path),
            dynamic_entries=cls._read_dynamic_entries(state_path),
        )

    @staticmethod
    def _read_seed_entries(path: Path | None) -> tuple[GlossaryEntry, ...]:
        if not path or not path.exists():
            return ()
        now = _utc_now().isoformat()
        entries: list[GlossaryEntry] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = re.split(r"\s*(?:\||\t)\s*", line)
            canonical = fields[0].strip()
            aliases = tuple(
                item.strip() for item in fields[1:] if item.strip()
            )
            if canonical:
                entries.append(
                    GlossaryEntry(
                        canonical=canonical,
                        aliases=aliases,
                        source="seed",
                        confidence=1.0,
                        last_seen=now,
                    )
                )
        return tuple(entries)

    @staticmethod
    def _read_manual_entries(path: Path | None) -> tuple[GlossaryEntry, ...]:
        """Read operator-owned terms that are trusted for both ASR branches."""
        if not path or not path.exists():
            return ()
        now = _utc_now().isoformat()
        entries: list[GlossaryEntry] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = re.split(r"\s*(?:\||\t)\s*", line)
            canonical = fields[0].strip()
            aliases = tuple(
                dict.fromkeys(
                    item.strip() for item in fields[1:] if item.strip()
                )
            )
            if canonical:
                entries.append(
                    GlossaryEntry(
                        canonical=canonical,
                        aliases=aliases,
                        source="manual",
                        confidence=1.0,
                        last_seen=now,
                    )
                )
        return tuple(entries)

    @staticmethod
    def _read_dynamic_entries(path: Path) -> tuple[GlossaryEntry, ...]:
        if not path.exists():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_entries = payload.get("entries", []) if isinstance(payload, dict) else []
        entries: list[GlossaryEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            canonical = str(raw.get("canonical", "")).strip()
            aliases = tuple(
                str(alias).strip()
                for alias in raw.get("aliases", [])
                if str(alias).strip()
            )
            try:
                confidence = float(raw.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if canonical:
                entries.append(
                    GlossaryEntry(
                        canonical=canonical,
                        aliases=aliases,
                        source=str(raw.get("source", "unknown")).strip() or "unknown",
                        confidence=min(1.0, max(0.0, confidence)),
                        last_seen=str(raw.get("last_seen", "")) or _utc_now().isoformat(),
                        expires_at=(
                            str(raw["expires_at"])
                            if raw.get("expires_at")
                            else None
                        ),
                    )
                )
        return tuple(entries)

    def active_entries(self, *, now: datetime | None = None) -> tuple[GlossaryEntry, ...]:
        """Return active canonical entries, merging duplicate canonicals safely."""
        now = now or _utc_now()
        merged: dict[str, GlossaryEntry] = {}
        for entry in (
            *self._seed_entries,
            *self._manual_entries,
            *self._dynamic_entries,
        ):
            if not entry.is_active(now=now):
                continue
            key = entry.canonical.casefold()
            previous = merged.get(key)
            if previous is None or entry.confidence > previous.confidence:
                merged[key] = entry
        return tuple(merged.values())

    def dynamic_phonetic_entries(
        self,
        *,
        minimum_confidence: float,
        now: datetime | None = None,
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return only active, sufficiently evidenced dynamic phonetic terms."""
        now = now or _utc_now()
        result: list[tuple[str, tuple[str, ...]]] = []
        seen: set[str] = set()
        for entry in (*self._manual_entries, *self._dynamic_entries):
            key = entry.canonical.casefold()
            if (
                key in seen
                or not entry.is_active(now=now)
                or entry.confidence < minimum_confidence
            ):
                continue
            seen.add(key)
            result.append((entry.canonical, entry.aliases))
        return tuple(result)

    def active_dynamic_entries(
        self, *, now: datetime | None = None
    ) -> tuple[GlossaryEntry, ...]:
        now = now or _utc_now()
        return tuple(
            entry
            for entry in self._dynamic_entries
            if entry.is_active(now=now)
        )

    @staticmethod
    def entries_from_participants(
        display_names: Iterable[str],
        *,
        now: datetime | None = None,
    ) -> tuple[GlossaryEntry, ...]:
        """Create meeting-scoped proper-name entries from participant names."""
        now = now or _utc_now()
        entries: list[GlossaryEntry] = []
        seen: set[str] = set()
        for raw_name in display_names:
            canonical = " ".join(str(raw_name).split()).strip()[:80]
            key = canonical.casefold()
            if not canonical or key in seen:
                continue
            seen.add(key)
            entries.append(
                GlossaryEntry(
                    canonical=canonical,
                    aliases=(),
                    source="participant",
                    confidence=1.0,
                    last_seen=now.isoformat(),
                )
            )
        return tuple(entries)

    def hotword_phrases(
        self, *, minimum_confidence: float, now: datetime | None = None
    ) -> tuple[str, ...]:
        """Return contextual canonical forms only, ready for hotwords.txt.

        Static seed entries are deliberately excluded. They are a broad
        phonetic-recovery fallback; injecting them into every meeting decoder
        would bias unrelated rooms. Only evidence sourced for the current
        meeting can become a Zipformer hotword.
        """
        now = now or _utc_now()
        seen: set[str] = set()
        phrases: list[str] = []
        for entry in (*self._manual_entries, *self._dynamic_entries):
            if not entry.is_trusted_for_hotword(minimum_confidence, now=now):
                continue
            # Single-token participant display names such as "Long", "An" or
            # "Đạt" are ordinary Vietnamese words too.  They stay available
            # to the final phonetic gate but must not bias every decoder beam.
            if (
                entry.source == "participant"
                and len(entry.canonical.split()) < 2
            ):
                continue
            phrase = entry.canonical.strip()
            key = phrase.casefold()
            if key not in seen:
                seen.add(key)
                phrases.append(phrase)
        return tuple(phrases)

    def save_dynamic_entries(self, entries: Iterable[GlossaryEntry]) -> None:
        """Persist supplied evidence-backed entries atomically for the next room.

        Callers must supply a source/confidence/expiry; raw ASR strings are not
        upgraded here.  Reloading the ASR recognizer is intentionally separate
        because active streams must finish with their original decoder.
        """
        filtered = tuple(
            entry
            for entry in entries
            if entry.source != "seed" and entry.canonical.strip()
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.state_path.with_suffix(
            self.state_path.suffix + ".tmp"
        )
        temporary_path.write_text(
            json.dumps(
                {"version": 1, "entries": [entry.to_json() for entry in filtered]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(self.state_path)
        self._dynamic_entries = filtered


def build_hotword_artifacts(
    dictionary: AdaptiveDictionary,
    *,
    model_dir: Path,
    hotwords_path: Path,
    bpe_vocab_path: Path,
    minimum_confidence: float,
) -> HotwordArtifacts:
    """Write sherpa-onnx BPE vocabulary and natural-text hotword phrases.

    Sherpa expects the BPE vocab in SentencePiece's two-column ``piece score``
    format.  The ASR model's ``config.json`` is a separate token-to-ID table
    and must not be passed as ``bpe_vocab``.
    """
    try:
        import sentencepiece as spm
    except ImportError as exc:  # pragma: no cover - dependent on runtime setup
        raise RuntimeError(
            "ZIPFORMER_HOTWORDS_ENABLED requires the sentencepiece package"
        ) from exc

    model_path = model_dir / "bpe.model"
    if not model_path.exists():
        raise FileNotFoundError(f"Missing Zipformer BPE model: {model_path}")
    phrases = dictionary.hotword_phrases(minimum_confidence=minimum_confidence)
    hotwords_path.parent.mkdir(parents=True, exist_ok=True)
    tokenizer = spm.SentencePieceProcessor(model_file=str(model_path))
    bpe_vocab_path.write_text(
        "".join(
            f"{tokenizer.id_to_piece(index)}\t{tokenizer.get_score(index)}\n"
            for index in range(tokenizer.vocab_size())
        ),
        encoding="utf-8",
    )
    hotwords_path.write_text(
        "\n".join(phrases) + ("\n" if phrases else ""),
        encoding="utf-8",
    )
    return HotwordArtifacts(
        hotwords_path=hotwords_path,
        bpe_vocab_path=bpe_vocab_path,
        phrase_count=len(phrases),
    )
