"""Deterministic meeting-term and phonetic recovery before LLM refinement.

The recovery stage is intentionally conservative.  It only rewrites phrases
that are present in a local domain lexicon and have a strong grapheme/phonetic
similarity match.  This gives the prototype a useful stand-in for a G2P model
without making an unconstrained ASR correction pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
import math
import re
import threading
import unicodedata
import warnings
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Protocol


_MEETING_TERM_PATTERNS = (
    (r"\bmột năm chấm hai\b", "mục 5.2"),
    (r"\badolf\s+sor\w*\b", "Hadoop Storage"),
    (r"\bhpase\b", "HBase"),
    (r"\baptoris\b", "Architecture"),
    (r"\b(?:lồng|làm)\s+quét\b", "làm web"),
    (r"\bhệ\s+thống\s+tự\s+tin\b", "hệ thống tập tin"),
)

_BUILTIN_PHONETIC_ENTRIES = (
    ("HDFS", ("hdfs", "h d f s", "h d s", "hdx", "h đê ép ét")),
    ("HBase", ("hbase", "h base", "hpase", "h pase", "hbase")),
    ("Hadoop Storage", ("hadoop storage", "adolf storage", "adolf sorus")),
    ("Zipformer", ("zipformer", "zip former", "chip former")),
    ("WavLM", ("wavlm", "wave lm", "wav elm")),
    ("Qdrant", ("qdrant", "q durant", "kiu durant")),
    ("LiveKit", ("livekit", "live kit", "lai v kit")),
    ("DPDFNet", ("dpdfnet", "dpdf net", "đi pi đi ép net")),
    ("Qwen", ("qwen", "kiu en")),
    ("phonetic recovery", ("phonetic recovery", "fonetic recovery")),
)

# Zipformer commonly returns its final text in uppercase.  The ASR output does
# not carry enough information to recover every proper noun, so this list is
# deliberately limited to terms whose casing is deterministic.  Meeting-title
# terms are supplied separately by the caller after they have passed the
# adaptive-dictionary gate.
_DISPLAY_CASE_TERMS = tuple(entry[0] for entry in _BUILTIN_PHONETIC_ENTRIES) + (
    "AI",
    "ASR",
    "LLM",
    "VNPT",
    "WSL",
    "CPU",
    "GPU",
)


def _phonetic_key(value: str) -> str:
    """Build a language-agnostic key for common Vietnamese ASR confusions."""
    value = value.casefold().replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^a-z0-9]+", "", value)
    # These substitutions reduce spelling variants without touching the
    # original transcript.  Domain aliases remain the primary safety gate.
    value = value.replace("ph", "f")
    value = value.replace("qu", "k")
    value = value.replace("c", "k")
    value = value.replace("q", "k")
    return value


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _canonical_group_key(value: str) -> str:
    """Collapse contextual variants of the same domain term for ranking.

    The seed lexicon carries both ``Hadoop Storage`` and the safer contextual
    form ``lớp Hadoop Storage``. They should not compete in the margin check:
    an observed phrase beginning with ``lớp`` needs the contextual replacement
    to consume the whole span instead of leaving a trailing ASR token.
    """
    value = re.sub(r"^\s*lớp\s+", "", value, flags=re.IGNORECASE)
    return value.casefold().strip()


class PronunciationBackend(Protocol):
    """Optional source of IPA-like phonetic strings for a phrase."""

    def phonemize(self, text: str) -> str:
        ...


class G2POnnxPhonemizer:
    """Lazy-safe adapter for the multilingual ByT5 G2P ONNX export.

    The model is only instantiated when the ``g2p_onnx`` backend is enabled.
    Its output is cached because final turns often contain overlapping phrases
    from multiple microphones.
    """

    def __init__(
        self,
        model_path: Path,
        *,
        language_code: str = "vie-c",
        threads: int = 4,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(f"G2P ONNX model không tồn tại: {model_path}")

        import onnxruntime as ort
        from optimum.onnxruntime import ORTModelForSeq2SeqLM
        from transformers import AutoTokenizer

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        # The repository ships the standard encoder/decoder pair rather than
        # a merged decoder. Optimum emits a harmless discovery warning at
        # generation time although these three graphs work correctly.
        warnings.filterwarnings(
            "ignore",
            message="Could not find any ONNX files with standard file name",
            category=UserWarning,
        )
        self._model = ORTModelForSeq2SeqLM.from_pretrained(
            str(model_path),
            provider="CPUExecutionProvider",
            session_options=options,
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(model_path))
        self._language_code = language_code
        self._lock = threading.Lock()

    @lru_cache(maxsize=2048)
    def phonemize(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        try:
            inputs = self._tokenizer(
                f"<{self._language_code}>: {text}",
                add_special_tokens=False,
                return_tensors="pt",
            )
            with self._lock:
                tokens = self._model.generate(
                    **inputs,
                    num_beams=1,
                    max_length=96,
                )
            # Spaces, stress marks and tone symbols are retained; only
            # whitespace is irrelevant to the sequence-distance comparison.
            return re.sub(
                r"\s+", "", self._tokenizer.decode(
                    tokens[0], skip_special_tokens=True
                )
            )
        except Exception:
            # Phonetic recovery must never make the transcript pipeline fail.
            return ""


class EpitranVietnamesePhonemizer:
    """Lazy Epitran adapter for a deterministic Vietnamese IPA view.

    Unlike the ByT5 ONNX model, Epitran is rule based.  Keeping both views is
    useful: a candidate is less likely to be accepted merely because one G2P
    engine makes a surprising pronunciation prediction.  The import stays in
    ``__init__`` so the normal demo has no Epitran/PanPhon dependency unless
    this experimental scorer is explicitly constructed.
    """

    def __init__(self, *, language_code: str = "vie-Latn") -> None:
        import epitran

        self._engine = epitran.Epitran(language_code)
        self._lock = threading.Lock()

    @lru_cache(maxsize=2048)
    def phonemize(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        try:
            with self._lock:
                ipa = self._engine.transliterate(text.casefold())
            return re.sub(r"\s+", "", ipa)
        except Exception:
            return ""


class SeaG2PVietnamesePhonemizer:
    """SEA-G2P adapter providing a third, independent Vietnamese G2P view."""

    def __init__(self, *, language_code: str = "vi") -> None:
        from sea_g2p import G2P

        self._engine = G2P(lang=language_code)
        self._lock = threading.Lock()

    @lru_cache(maxsize=2048)
    def phonemize(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        try:
            with self._lock:
                ipa = self._engine.convert(text.casefold())
            return re.sub(r"\s+", "", str(ipa))
        except Exception:
            return ""


class PanphonFeatureScorer:
    """Convert PanPhon's weighted IPA feature distance into a 0..1 score.

    PanPhon's weighted distance has no fixed upper bound.  An exponential
    calibration preserves ordering while avoiding an arbitrary hard maximum;
    the scale of four makes one consonant-class mismatch meaningful without
    rejecting a whole otherwise matching phrase.
    """

    _NON_SEGMENTAL_MARKS = re.compile(r"[ˈˌ˥˦˧˨˩ː.]")

    def __init__(self, *, distance_scale: float = 4.0) -> None:
        import panphon.distance

        self._distance = panphon.distance.Distance()
        self._distance_scale = max(0.1, distance_scale)

    @classmethod
    def _clean_ipa(cls, value: str) -> str:
        return cls._NON_SEGMENTAL_MARKS.sub("", value or "")

    def similarity(self, left: str, right: str) -> float:
        left = self._clean_ipa(left)
        right = self._clean_ipa(right)
        if not left or not right:
            return 0.0
        try:
            distance = self._distance.weighted_feature_edit_distance_div_maxlen(
                left, right
            )
        except Exception:
            return 0.0
        return math.exp(-max(0.0, float(distance)) / self._distance_scale)


@dataclass(frozen=True)
class ParallelPhoneticScore:
    """Evidence from the rule-based and neural Vietnamese G2P paths."""

    score: float
    epitran_score: float | None
    g2p_score: float | None
    disagreement: float | None
    observed_epitran: str
    candidate_epitran: str
    observed_g2p: str
    candidate_g2p: str


class ParallelPhoneticScorer:
    """Score a phrase/candidate pair with Epitran and ByT5 G2P in parallel.

    "Parallel" here means independent pronunciation evidence, merged only
    after each source is scored by the same PanPhon feature metric.  The
    caller may still run the two sources concurrently at a higher layer; ByT5
    itself is lock-protected for ONNX Runtime safety.
    """

    def __init__(
        self,
        epitran_phonemizer: PronunciationBackend,
        g2p_phonemizer: PronunciationBackend,
        feature_scorer: PanphonFeatureScorer,
        *,
        epitran_weight: float = 0.50,
    ) -> None:
        self.epitran_phonemizer = epitran_phonemizer
        self.g2p_phonemizer = g2p_phonemizer
        self.feature_scorer = feature_scorer
        self.epitran_weight = min(1.0, max(0.0, epitran_weight))

    def score(self, observed: str, candidate: str) -> ParallelPhoneticScore:
        observed = observed.casefold()
        candidate = candidate.casefold()

        def phonemize_pair(phonemizer: PronunciationBackend) -> tuple[str, str]:
            return phonemizer.phonemize(observed), phonemizer.phonemize(candidate)

        # Epitran is CPU-light and G2P ONNX is comparatively expensive.  Run
        # them concurrently so the rule-based view is effectively free in the
        # critical path.  G2P keeps its own inference lock for ONNX safety.
        with ThreadPoolExecutor(max_workers=2) as executor:
            epitran_future = executor.submit(
                phonemize_pair, self.epitran_phonemizer
            )
            g2p_future = executor.submit(phonemize_pair, self.g2p_phonemizer)
            observed_epitran, candidate_epitran = epitran_future.result()
            observed_g2p, candidate_g2p = g2p_future.result()

        epitran_score = (
            self.feature_scorer.similarity(observed_epitran, candidate_epitran)
            if observed_epitran and candidate_epitran
            else None
        )
        g2p_score = (
            self.feature_scorer.similarity(observed_g2p, candidate_g2p)
            if observed_g2p and candidate_g2p
            else None
        )
        available = [score for score in (epitran_score, g2p_score) if score is not None]
        if not available:
            total = 0.0
        elif epitran_score is None:
            total = g2p_score or 0.0
        elif g2p_score is None:
            total = epitran_score
        else:
            total = (
                self.epitran_weight * epitran_score
                + (1.0 - self.epitran_weight) * g2p_score
            )
        disagreement = (
            abs(epitran_score - g2p_score)
            if epitran_score is not None and g2p_score is not None
            else None
        )
        return ParallelPhoneticScore(
            score=total,
            epitran_score=epitran_score,
            g2p_score=g2p_score,
            disagreement=disagreement,
            observed_epitran=observed_epitran,
            candidate_epitran=candidate_epitran,
            observed_g2p=observed_g2p,
            candidate_g2p=candidate_g2p,
        )


@dataclass(frozen=True)
class TriplePhoneticScore:
    """Robust consensus from Epitran, ByT5 G2P and SEA-G2P."""

    score: float
    epitran_score: float | None
    g2p_score: float | None
    sea_g2p_score: float | None
    consensus_count: int
    disagreement: float | None


class TriplePhoneticScorer:
    """Use median phonetic similarity to reject a single-engine surprise.

    The scorer is used only after a cheap grapheme prefilter on finalized ASR
    turns. Candidate pronunciations are cached by all three adapters, so the
    second occurrence of a meeting term avoids model inference.
    """

    def __init__(
        self,
        epitran_phonemizer: PronunciationBackend,
        g2p_phonemizer: PronunciationBackend,
        sea_g2p_phonemizer: PronunciationBackend,
        feature_scorer: PanphonFeatureScorer,
        *,
        consensus_tolerance: float = 0.18,
    ) -> None:
        self.epitran_phonemizer = epitran_phonemizer
        self.g2p_phonemizer = g2p_phonemizer
        self.sea_g2p_phonemizer = sea_g2p_phonemizer
        self.feature_scorer = feature_scorer
        self.consensus_tolerance = max(0.0, consensus_tolerance)

    def score(self, observed: str, candidate: str) -> TriplePhoneticScore:
        observed = observed.casefold()
        candidate = candidate.casefold()

        def phonemize_pair(phonemizer: PronunciationBackend) -> tuple[str, str]:
            return phonemizer.phonemize(observed), phonemizer.phonemize(candidate)

        with ThreadPoolExecutor(max_workers=3) as executor:
            epitran_future = executor.submit(
                phonemize_pair, self.epitran_phonemizer
            )
            g2p_future = executor.submit(phonemize_pair, self.g2p_phonemizer)
            sea_future = executor.submit(
                phonemize_pair, self.sea_g2p_phonemizer
            )
            observed_epitran, candidate_epitran = epitran_future.result()
            observed_g2p, candidate_g2p = g2p_future.result()
            observed_sea, candidate_sea = sea_future.result()

        epitran_score = (
            self.feature_scorer.similarity(observed_epitran, candidate_epitran)
            if observed_epitran and candidate_epitran
            else None
        )
        g2p_score = (
            self.feature_scorer.similarity(observed_g2p, candidate_g2p)
            if observed_g2p and candidate_g2p
            else None
        )
        sea_score = (
            self.feature_scorer.similarity(observed_sea, candidate_sea)
            if observed_sea and candidate_sea
            else None
        )
        available = sorted(
            score
            for score in (epitran_score, g2p_score, sea_score)
            if score is not None
        )
        if not available:
            total = 0.0
            consensus_count = 0
            disagreement = None
        else:
            middle = len(available) // 2
            total = available[middle]
            consensus_count = sum(
                abs(score - total) <= self.consensus_tolerance
                for score in available
            )
            disagreement = available[-1] - available[0]
        return TriplePhoneticScore(
            score=total,
            epitran_score=epitran_score,
            g2p_score=g2p_score,
            sea_g2p_score=sea_score,
            consensus_count=consensus_count,
            disagreement=disagreement,
        )


@dataclass(frozen=True)
class PhoneticRecovery:
    text: str
    replacements: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _LexiconEntry:
    canonical: str
    alias: str
    key: str
    ipa: str = ""


class PhoneticLexicon:
    """Small, reloadable domain lexicon used for conservative recovery."""

    def __init__(
        self,
        entries: Iterable[tuple[str, Iterable[str]]] = (),
        *,
        threshold: float = 0.86,
        margin: float = 0.06,
        max_words: int = 4,
        phonemizer: PronunciationBackend | None = None,
        g2p_weight: float = 0.65,
        g2p_prefilter: float = 0.80,
        g2p_max_calls: int = 8,
        g2p_force: bool = False,
        triple_scorer: TriplePhoneticScorer | None = None,
        triple_weight: float = 0.75,
        triple_min_consensus: int = 2,
        auto_apply: bool = True,
    ) -> None:
        self.threshold = threshold
        self.margin = margin
        self.max_words = max(1, max_words)
        self.phonemizer = phonemizer
        self.g2p_weight = min(1.0, max(0.0, g2p_weight))
        self.g2p_prefilter = min(1.0, max(0.0, g2p_prefilter))
        self.g2p_max_calls = max(0, g2p_max_calls)
        self.g2p_force = g2p_force
        self.triple_scorer = triple_scorer
        self.triple_weight = min(1.0, max(0.0, triple_weight))
        self.triple_min_consensus = max(1, triple_min_consensus)
        self.auto_apply = auto_apply
        self.entries: list[_LexiconEntry] = []
        for canonical, aliases in entries:
            canonical = canonical.strip()
            if not canonical:
                continue
            for alias in (canonical, *aliases):
                alias = alias.strip()
                key = _phonetic_key(alias)
                if key:
                    # Vietnamese ASR finals are often all-uppercase while
                    # the lexicon is title/lower case. G2P output is not
                    # guaranteed case-invariant, so normalize before every
                    # IPA lookup (the cache still removes repeat inference).
                    ipa = (
                        phonemizer.phonemize(alias.casefold())
                        if phonemizer
                        else ""
                    )
                    self.entries.append(
                        _LexiconEntry(canonical, alias, key, ipa)
                    )

    @classmethod
    def from_file(
        cls,
        path: Path | None,
        *,
        extra_entries: Iterable[tuple[str, Iterable[str]]] = (),
        threshold: float = 0.86,
        margin: float = 0.06,
        max_words: int = 4,
        phonemizer: PronunciationBackend | None = None,
        g2p_weight: float = 0.65,
        g2p_prefilter: float = 0.80,
        g2p_max_calls: int = 8,
        g2p_force: bool = False,
        triple_scorer: TriplePhoneticScorer | None = None,
        triple_weight: float = 0.75,
        triple_min_consensus: int = 2,
        auto_apply: bool = True,
    ) -> "PhoneticLexicon":
        entries: list[tuple[str, tuple[str, ...]]] = list(
            _BUILTIN_PHONETIC_ENTRIES
        )
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = re.split(r"\s*(?:\||\t)\s*", line)
                canonical = fields[0].strip()
                aliases = tuple(item.strip() for item in fields[1:] if item.strip())
                if canonical:
                    entries.append((canonical, aliases))
        entries.extend(extra_entries)
        return cls(
            entries,
            threshold=threshold,
            margin=margin,
            max_words=max_words,
            phonemizer=phonemizer,
            g2p_weight=g2p_weight,
            g2p_prefilter=g2p_prefilter,
            g2p_max_calls=g2p_max_calls,
            g2p_force=g2p_force,
            triple_scorer=triple_scorer,
            triple_weight=triple_weight,
            triple_min_consensus=triple_min_consensus,
            auto_apply=auto_apply,
        )

    def _has_unresolved_longer_alias(
        self,
        entry: _LexiconEntry,
        observed: str,
        following_word: str | None,
    ) -> bool:
        """Block a safe-looking replacement that would split a longer alias.

        Example: ``lớp adolf sorris`` may be a noisy form of the known alias
        ``lớp adolf sorus``. If the complete candidate is too ambiguous for
        the margin gate, replacing only ``lớp adolf`` would create a corrupted
        hybrid transcript. Keep the raw span instead; a future turn or a
        stronger dictionary alias can resolve it safely.
        """
        if not following_word:
            return False
        observed_words = re.findall(r"[\wÀ-ỹĐđ]+", observed, flags=re.UNICODE)
        if not observed_words:
            return False
        observed_keys = [_phonetic_key(word) for word in observed_words]
        next_key = _phonetic_key(following_word)
        if not next_key:
            return False
        group = _canonical_group_key(entry.canonical)
        for sibling in self.entries:
            if _canonical_group_key(sibling.canonical) != group:
                continue
            alias_words = re.findall(
                r"[\wÀ-ỹĐđ]+", sibling.alias, flags=re.UNICODE
            )
            if len(alias_words) <= len(observed_words):
                continue
            alias_keys = [_phonetic_key(word) for word in alias_words]
            prefix_matches = all(
                _similarity(left, right) >= 0.88
                for left, right in zip(
                    observed_keys,
                    alias_keys[:len(observed_keys)],
                    strict=True,
                )
            )
            if not prefix_matches:
                continue
            if _similarity(next_key, alias_keys[len(observed_words)]) >= 0.60:
                return True
        return False

    def recover(self, text: str) -> PhoneticRecovery:
        if not text or not self.entries:
            return PhoneticRecovery(text=text, replacements=())

        word_matches = list(re.finditer(r"[\wÀ-ỹĐđ]+", text, flags=re.UNICODE))
        if not word_matches:
            return PhoneticRecovery(text=text, replacements=())

        replacements: list[tuple[int, int, str, dict[str, object]]] = []
        g2p_calls = 0
        i = 0
        while i < len(word_matches):
            best: tuple[
                float,
                float,
                float,
                float,
                _LexiconEntry,
                int,
                str,
                TriplePhoneticScore | None,
            ] | None = None
            max_end = min(len(word_matches), i + self.max_words)
            for end in range(max_end, i, -1):
                observed = text[
                    word_matches[i].start():word_matches[end - 1].end()
                ]
                observed_key = _phonetic_key(observed)
                if len(observed_key) < 3:
                    continue
                candidates = [
                    entry
                    for entry in self.entries
                    if abs(len(observed_key) - len(entry.key)) <= 8
                ]
                if not candidates:
                    continue
                # Baseline mode accepts an exact alias immediately.  The
                # Sailor A/B mode deliberately disables that shortcut: each
                # proposal, including an exact alias, must have G2P evidence.
                has_exact_alias = any(
                    observed_key == entry.key for entry in candidates
                )
                by_canonical: dict[
                    str,
                    tuple[
                        float,
                        float,
                        float,
                        _LexiconEntry,
                        TriplePhoneticScore | None,
                    ],
                ] = {}
                observed_ipa: str | None = None
                phonetic_attempted = False
                for entry in candidates:
                    # Fuzzy matching is allowed to absorb spelling/noise
                    # inside a known alias, never an extra neighbouring word.
                    # Without this guard, ``tiên lớp adolf sorus`` can score
                    # close enough to ``lớp adolf sorus`` and erase the word
                    # ``tiên`` before the correct window is considered.
                    observed_word_count = end - i
                    alias_word_count = len(
                        re.findall(r"[\wÀ-ỹĐđ]+", entry.alias, flags=re.UNICODE)
                    )
                    if observed_word_count > alias_word_count:
                        continue
                    key_score = _similarity(observed_key, entry.key)
                    ipa_score = 0.0
                    score = key_score
                    triple_result: TriplePhoneticScore | None = None
                    if (
                        self.triple_scorer
                        and (self.g2p_force or key_score >= self.g2p_prefilter)
                        and g2p_calls < self.g2p_max_calls
                    ):
                        if not phonetic_attempted:
                            g2p_calls += 1
                            phonetic_attempted = True
                        triple_result = self.triple_scorer.score(
                            observed, entry.alias
                        )
                        ipa_score = triple_result.score
                        score = (
                            (1.0 - self.triple_weight) * key_score
                            + self.triple_weight * ipa_score
                        )
                    elif (
                        self.triple_scorer is None
                        and self.phonemizer
                        and entry.ipa
                        and (self.g2p_force or not has_exact_alias)
                        and (self.g2p_force or key_score >= self.g2p_prefilter)
                        and g2p_calls < self.g2p_max_calls
                    ):
                        if not phonetic_attempted:
                            observed_ipa = self.phonemizer.phonemize(
                                observed.casefold()
                            )
                            g2p_calls += 1
                            phonetic_attempted = True
                        if observed_ipa:
                            ipa_score = _similarity(observed_ipa, entry.ipa)
                            score = (
                                (1.0 - self.g2p_weight) * key_score
                                + self.g2p_weight * ipa_score
                            )
                    canonical_group = _canonical_group_key(entry.canonical)
                    existing = by_canonical.get(canonical_group)
                    if existing is None or score > existing[0]:
                        by_canonical[canonical_group] = (
                            score,
                            key_score,
                            ipa_score,
                            entry,
                            triple_result,
                        )
                if not by_canonical:
                    continue
                ranked = sorted(
                    by_canonical.values(), key=lambda item: item[0], reverse=True
                )
                score, key_score, ipa_score, entry, triple_result = ranked[0]
                candidate_second = ranked[1][0] if len(ranked) > 1 else 0.0
                # Prefer exact alias matches and longer phrases.  Fuzzy
                # matching requires a margin so similar technical terms do
                # not replace each other accidentally.
                exact = observed_key == entry.key
                g2p_verified = not self.g2p_force or ipa_score > 0.0
                triple_verified = (
                    triple_result is None
                    or triple_result.consensus_count >= self.triple_min_consensus
                )
                accepted = (exact and g2p_verified and triple_verified) or (
                    score >= self.threshold
                    and score - candidate_second >= self.margin
                    and len(observed_key) >= 5
                    and g2p_verified
                    and triple_verified
                )
                if not accepted:
                    continue
                following_word = (
                    word_matches[end].group()
                    if end < len(word_matches)
                    else None
                )
                if self._has_unresolved_longer_alias(
                    entry, observed, following_word
                ):
                    continue
                rank = score + (0.02 * (end - i))
                if best is None or rank > best[0]:
                    best = (
                        rank,
                        score,
                        key_score,
                        ipa_score,
                        entry,
                        end,
                        observed,
                        triple_result,
                    )
            if best is None:
                i += 1
                continue

            (
                _,
                score,
                key_score,
                ipa_score,
                entry,
                end,
                observed,
                triple_result,
            ) = best
            if observed == entry.canonical:
                # The dictionary always indexes the canonical spelling too;
                # do not report a no-op replacement for already-correct text.
                i = end
                continue
            metadata = {
                "from": observed,
                "to": entry.canonical,
                "start": word_matches[i].start(),
                "end": word_matches[end - 1].end(),
                "score": round(score, 4),
                "key_score": round(key_score, 4),
                "g2p_score": round(ipa_score, 4) if ipa_score else None,
                "backend": (
                    "triple_phonetic"
                    if triple_result is not None
                    else "g2p_onnx" if ipa_score else "grapheme"
                ),
                "alias": entry.alias,
            }
            if triple_result is not None:
                metadata["triple_phonetic"] = {
                    "epitran_score": round(
                        triple_result.epitran_score, 4
                    ) if triple_result.epitran_score is not None else None,
                    "g2p_score": round(
                        triple_result.g2p_score, 4
                    ) if triple_result.g2p_score is not None else None,
                    "sea_g2p_score": round(
                        triple_result.sea_g2p_score, 4
                    ) if triple_result.sea_g2p_score is not None else None,
                    "consensus_count": triple_result.consensus_count,
                    "disagreement": round(
                        triple_result.disagreement, 4
                    ) if triple_result.disagreement is not None else None,
                }
            replacements.append(
                (
                    word_matches[i].start(),
                    word_matches[end - 1].end(),
                    entry.canonical,
                    metadata,
                )
            )
            i = end

        if not replacements:
            return PhoneticRecovery(text=text, replacements=())
        recovered = text
        if self.auto_apply:
            for start, end, replacement, _ in reversed(replacements):
                recovered = recovered[:start] + replacement + recovered[end:]
        return PhoneticRecovery(
            text=recovered,
            replacements=tuple(item[3] for item in replacements),
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


def format_transcript_sentence(
    text: str,
    *,
    protected_terms: Iterable[str] = (),
    add_terminal_punctuation: bool = True,
) -> str:
    """Make final ASR evidence readable without changing its words.

    This is intentionally *not* a language-model rewrite.  It only collapses
    whitespace, runs the existing deterministic technical-term normaliser and
    converts an all-caps decoder result to Vietnamese sentence case.  Known
    acronyms/product names and trusted meeting glossary entries keep their
    canonical spelling.  The source ``raw_text`` is still stored unchanged for
    diagnostics and evaluation.
    """
    normalized = normalize_meeting_terms(" ".join(str(text or "").split()))
    if not normalized:
        return ""

    # Protect canonical forms before lowering the rest.  Longest first avoids
    # replacing ``Qwen`` inside a longer user-supplied title term.
    protected = tuple(
        dict.fromkeys(
            term.strip()
            for term in (*_DISPLAY_CASE_TERMS, *protected_terms)
            if isinstance(term, str) and term.strip()
        )
    )
    placeholders: dict[str, str] = {}
    display = normalized
    for index, term in enumerate(sorted(protected, key=len, reverse=True)):
        placeholder = f"\ue000{index}\ue001"
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        display, count = pattern.subn(placeholder, display)
        if count:
            placeholders[placeholder] = term

    display = display.lower()
    for index, character in enumerate(display):
        if character.isalpha():
            display = display[:index] + character.upper() + display[index + 1 :]
            break
    for placeholder, term in placeholders.items():
        display = display.replace(placeholder, term)

    # Final segments represent a completed turn.  A full stop makes the
    # timeline legible but is not added to a one/two-token acknowledgement.
    word_count = len(re.findall(r"\w+", display, flags=re.UNICODE))
    if (
        add_terminal_punctuation
        and word_count >= 3
        and display[-1] not in ".?!…:;"
    ):
        display += "."
    return display
