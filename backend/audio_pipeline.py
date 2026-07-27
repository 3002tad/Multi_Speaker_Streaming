"""Pure audio front-end utilities shared by the LiveKit bridge and AI server.

This module intentionally has no model dependencies.  It keeps the two audio
branches separate:

* speaker identity receives the VAD-selected, otherwise unmodified waveform;
* ASR receives a lightly enhanced waveform after cross-microphone selection.
"""

from __future__ import annotations

import asyncio
import collections
import math
import re
import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


SAMPLE_RATE = 16_000
_PACKET_MAGIC = b"PAF1"
_PACKET_HEADER = struct.Struct("!4sQd")


def pack_audio_packet(
    pcm: bytes,
    *,
    sequence: int,
    captured_at: float,
) -> bytes:
    """Attach a shared-clock timestamp to PCM received from LiveKit."""
    return _PACKET_HEADER.pack(
        _PACKET_MAGIC, int(sequence), float(captured_at)
    ) + pcm


def unpack_audio_packet(
    packet: bytes,
    *,
    fallback_timestamp: float | None = None,
) -> tuple[bytes, int | None, float]:
    """Read a timestamped packet while accepting legacy raw-PCM clients."""
    if len(packet) >= _PACKET_HEADER.size:
        magic, sequence, captured_at = _PACKET_HEADER.unpack_from(packet)
        if magic == _PACKET_MAGIC and math.isfinite(captured_at):
            return packet[_PACKET_HEADER.size :], sequence, captured_at
    return (
        packet,
        None,
        time.monotonic()
        if fallback_timestamp is None
        else float(fallback_timestamp),
    )


@dataclass(frozen=True)
class FrameQuality:
    rms: float
    peak: float
    clipping_ratio: float
    noise_floor: float
    snr_db: float
    score: float


class AudioQualityTracker:
    """Estimate per-mic quality without modifying speaker characteristics."""

    def __init__(
        self,
        initial_noise_floor: float = 0.003,
        *,
        history_frames: int = 80,
        minimum_noise_floor: float = 0.0003,
    ) -> None:
        self.minimum_noise_floor = max(
            1e-5, float(minimum_noise_floor)
        )
        self.noise_floor = max(
            float(initial_noise_floor), self.minimum_noise_floor
        )
        self._non_speech_rms: collections.deque[float] = (
            collections.deque(maxlen=max(10, int(history_frames)))
        )

    def measure(
        self, audio: np.ndarray, *, speech_active: bool
    ) -> FrameQuality:
        samples = np.asarray(audio, dtype=np.float32)
        if samples.size == 0:
            return FrameQuality(
                rms=0.0,
                peak=0.0,
                clipping_ratio=0.0,
                noise_floor=self.noise_floor,
                snr_db=-20.0,
                score=-40.0,
            )

        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
        peak = float(np.max(np.abs(samples)))
        clipping_ratio = float(np.mean(np.abs(samples) >= 0.98))

        # Learn only plausible non-speech frames. Silero reports START after
        # several frames, so blindly updating while speech_active=False lets
        # the beginning of speech/leakage poison the floor. A rolling lower
        # percentile and a capped upward step keep the estimator responsive
        # to room noise without following speech energy.
        if not speech_active:
            admission_limit = max(0.008, self.noise_floor * 2.5)
            if rms <= admission_limit:
                self._non_speech_rms.append(rms)
            if self._non_speech_rms:
                target = float(
                    np.percentile(self._non_speech_rms, 25)
                )
                if target <= self.noise_floor:
                    rate = 0.18
                    proposed = (
                        (1.0 - rate) * self.noise_floor + rate * target
                    )
                else:
                    # At 100 ms/frame this permits roughly a 22% rise/sec,
                    # enough for changing ambience but far slower than speech.
                    proposed = min(
                        target,
                        self.noise_floor * 1.02 + 1e-5,
                    )
                self.noise_floor = float(
                    np.clip(
                        proposed,
                        self.minimum_noise_floor,
                        0.1,
                    )
                )

        snr_db = float(
            np.clip(
                20.0
                * math.log10(
                    max(rms, 1e-6) / max(self.noise_floor, 1e-5)
                ),
                -20.0,
                50.0,
            )
        )
        # RMS helps choose the near-field mic, SNR avoids a naturally loud but
        # noisy channel, and clipping receives a steep penalty.
        level_db = 20.0 * math.log10(max(rms, 1e-6))
        score = (
            0.65 * snr_db
            + 0.35 * (level_db + 40.0)
            - min(30.0, clipping_ratio * 500.0)
        )
        return FrameQuality(
            rms=rms,
            peak=peak,
            clipping_ratio=clipping_ratio,
            noise_floor=self.noise_floor,
            snr_db=snr_db,
            score=score,
        )


def summarize_quality(
    observations: Sequence[FrameQuality],
) -> FrameQuality:
    if not observations:
        return FrameQuality(0.0, 0.0, 0.0, 0.003, -20.0, -40.0)

    # VAD deliberately tolerates pauses inside a turn. Summarizing all frames
    # made long pauses dominate SNR and produced values such as -80 dB. Keep
    # the energetic speech portion while retaining at least one observation.
    rms_values = np.asarray(
        [observation.rms for observation in observations],
        dtype=np.float32,
    )
    energy_floor = max(
        1e-5,
        float(np.percentile(rms_values, 40)),
    )
    voiced = [
        observation
        for observation in observations
        if observation.rms >= energy_floor
        and observation.rms
        >= max(1e-5, observation.noise_floor * 1.15)
    ]
    representative = voiced or list(observations)

    def median(name: str) -> float:
        return float(
            np.median(
                [
                    getattr(observation, name)
                    for observation in representative
                ]
            )
        )

    return FrameQuality(
        rms=median("rms"),
        peak=float(max(item.peak for item in representative)),
        clipping_ratio=median("clipping_ratio"),
        noise_floor=median("noise_floor"),
        snr_db=median("snr_db"),
        score=median("score"),
    )


class StreamingAsrPreprocessor:
    """Light, stateful DSP for Zipformer; never use this output for WavLM."""

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        high_pass_hz: float = 70.0,
        target_rms: float = 0.065,
        minimum_noise_gain: float = 0.65,
    ) -> None:
        self.sample_rate = sample_rate
        self.target_rms = target_rms
        self.minimum_noise_gain = minimum_noise_gain
        self._hp_alpha = math.exp(
            -2.0 * math.pi * high_pass_hz / sample_rate
        )
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._smoothed_gain = 1.0

    def reset(self) -> None:
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._smoothed_gain = 1.0

    def process(
        self,
        audio: np.ndarray,
        *,
        quality: FrameQuality,
    ) -> np.ndarray:
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return samples.copy()

        # One-pole DC blocker/high-pass. Frames are short, and retaining the
        # filter state avoids a discontinuity at every WebSocket packet.
        filtered = np.empty_like(samples)
        previous_input = self._previous_input
        previous_output = self._previous_output
        alpha = self._hp_alpha
        for index, sample in enumerate(samples):
            output = alpha * (
                previous_output + float(sample) - previous_input
            )
            filtered[index] = output
            previous_input = float(sample)
            previous_output = output
        self._previous_input = previous_input
        self._previous_output = previous_output

        filtered_rms = float(
            np.sqrt(np.mean(np.square(filtered), dtype=np.float64))
        )
        # Conservative Wiener-style attenuation. It reduces stationary noise
        # without zeroing consonants or creating hard gate boundaries.
        noise_ratio = quality.noise_floor / max(filtered_rms, 1e-6)
        noise_gain = float(
            np.clip(
                1.0 - noise_ratio * noise_ratio,
                self.minimum_noise_gain,
                1.0,
            )
        )

        desired_gain = float(
            np.clip(
                self.target_rms / max(filtered_rms * noise_gain, 1e-5),
                0.65,
                2.5,
            )
        )
        self._smoothed_gain = (
            0.88 * self._smoothed_gain + 0.12 * desired_gain
        )
        enhanced = filtered * noise_gain * self._smoothed_gain
        peak = float(np.max(np.abs(enhanced)))
        if peak > 0.97:
            enhanced *= 0.97 / peak
        return enhanced.astype(np.float32, copy=False)


@dataclass
class _FrameState:
    timestamp: float
    quality: FrameQuality
    sequence: int | None


@dataclass
class VadTurn:
    turn_id: str
    start_time: float
    end_time: float | None = None
    active_sources: set[str] = field(default_factory=set)
    all_sources: set[str] = field(default_factory=set)


@dataclass
class FinalCandidate:
    candidate_id: str
    turn_id: str
    source_id: str
    raw_text: str
    start_time: float
    end_time: float
    quality: FrameQuality
    created_at: float
    fingerprint: tuple[float, ...] = ()
    winner_id: str | None = None


def speech_envelope(
    audio: np.ndarray,
    *,
    sample_rate: int = SAMPLE_RATE,
    frame_ms: int = 100,
) -> tuple[float, ...]:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    frame_size = max(1, sample_rate * frame_ms // 1000)
    values = []
    for start in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[start : start + frame_size]
        values.append(
            float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
        )
    if len(values) < 3:
        return tuple(values)
    vector = np.asarray(values, dtype=np.float32)
    scale = float(np.linalg.norm(vector))
    if scale > 1e-8:
        vector /= scale
    return tuple(float(item) for item in vector)


def select_speaker_windows(
    audio: np.ndarray,
    *,
    minimum_seconds: float,
    sample_rate: int = SAMPLE_RATE,
    window_seconds: float = 4.0,
    step_seconds: float = 2.0,
    max_windows: int = 3,
) -> list[np.ndarray]:
    """Select clean raw windows for WavLM without enhancing the waveform."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    minimum_samples = int(minimum_seconds * sample_rate)
    if len(samples) < minimum_samples:
        return []

    window = int(window_seconds * sample_rate)
    step = int(step_seconds * sample_rate)
    if len(samples) <= window:
        candidates = [samples]
    else:
        candidates = [
            samples[start : start + window]
            for start in range(0, len(samples) - window + 1, step)
        ]

    measured = []
    for index, clip in enumerate(candidates):
        rms = float(
            np.sqrt(np.mean(np.square(clip), dtype=np.float64))
        )
        clipping_ratio = float(np.mean(np.abs(clip) >= 0.98))
        # A very high zero-crossing rate is usually fan/keyboard/broadband
        # noise rather than a stable speaker window.
        zero_crossing_rate = float(
            np.mean(np.signbit(clip[1:]) != np.signbit(clip[:-1]))
        )
        measured.append(
            (rms, clipping_ratio, zero_crossing_rate, index, clip)
        )

    reference_levels = [
        item[0]
        for item in measured
        if item[1] <= 0.03 and item[2] <= 0.38
    ]
    median_rms = (
        float(np.median(reference_levels)) if reference_levels else 0.0
    )
    rms_floor = max(0.004, median_rms * 0.35)
    clean = [
        item
        for item in measured
        if item[0] >= rms_floor
        and item[1] <= 0.03
        and item[2] <= 0.38
    ]
    clean_by_time = sorted(clean, key=lambda item: item[3])
    if len(clean_by_time) <= max_windows:
        selected = clean_by_time
    else:
        # Temporal coverage is more useful than selecting only the loudest
        # windows: a speaker may change pitch/emphasis while remaining clean.
        positions = np.linspace(
            0, len(clean_by_time) - 1, num=max_windows, dtype=int
        )
        selected = [clean_by_time[position] for position in positions]
    return [item[4] for item in selected]


class CoordinatedVadTimeline:
    """Coordinate per-mic VAD and quality on one room-wide clock."""

    def __init__(
        self,
        *,
        frame_freshness_seconds: float = 0.45,
        turn_join_gap_seconds: float = 0.8,
        asr_quality_margin: float = 3.5,
        asr_rms_ratio: float = 0.48,
        final_settle_seconds: float = 0.75,
    ) -> None:
        self.frame_freshness_seconds = frame_freshness_seconds
        self.turn_join_gap_seconds = turn_join_gap_seconds
        self.asr_quality_margin = asr_quality_margin
        self.asr_rms_ratio = asr_rms_ratio
        self.final_settle_seconds = final_settle_seconds
        self._frames: dict[str, _FrameState] = {}
        self._turns: dict[str, VadTurn] = {}
        self._source_turn: dict[str, str] = {}
        self._last_split_turn_id: str | None = None
        self._last_split_timestamp: float | None = None
        self._candidates: dict[str, FinalCandidate] = {}
        self._lock = asyncio.Lock()

    def note_frame(
        self,
        source_id: str,
        *,
        timestamp: float,
        quality: FrameQuality,
        sequence: int | None,
    ) -> None:
        self._frames[source_id] = _FrameState(
            timestamp=timestamp,
            quality=quality,
            sequence=sequence,
        )

    def should_route_asr(
        self, source_id: str, *, timestamp: float
    ) -> bool:
        current = self._frames.get(source_id)
        if current is None:
            return True
        current_turn = self._source_turn.get(source_id)
        peers = [
            state
            for peer_id, state in self._frames.items()
            if abs(timestamp - state.timestamp)
            <= self.frame_freshness_seconds
            and (
                current_turn is None
                or self._source_turn.get(peer_id) == current_turn
            )
        ]
        if len(peers) <= 1:
            return True
        best_score = max(state.quality.score for state in peers)
        best_rms = max(state.quality.rms for state in peers)
        return (
            current.quality.score >= best_score - self.asr_quality_margin
            and current.quality.rms >= best_rms * self.asr_rms_ratio
        )

    def speech_started(self, source_id: str, *, timestamp: float) -> str:
        existing = self._source_turn.get(source_id)
        if existing:
            return existing

        compatible = [
            turn
            for turn in self._turns.values()
            if (
                turn.end_time is None
                or timestamp - turn.end_time <= self.turn_join_gap_seconds
            )
            and timestamp >= turn.start_time - self.turn_join_gap_seconds
        ]
        if compatible:
            turn = max(compatible, key=lambda item: item.start_time)
        else:
            turn = VadTurn(
                turn_id=f"turn-{uuid.uuid4().hex}",
                start_time=timestamp,
            )
            self._turns[turn.turn_id] = turn
        turn.active_sources.add(source_id)
        turn.all_sources.add(source_id)
        turn.end_time = None
        self._source_turn[source_id] = turn.turn_id
        self._prune(timestamp)
        return turn.turn_id

    def speech_ended(self, source_id: str, *, timestamp: float) -> str:
        turn_id = self._source_turn.pop(source_id, None)
        if turn_id is None:
            turn_id = self.speech_started(
                source_id, timestamp=timestamp
            )
            self._source_turn.pop(source_id, None)
        turn = self._turns[turn_id]
        turn.active_sources.discard(source_id)
        if not turn.active_sources:
            turn.end_time = timestamp
        return turn_id

    def split_turn(self, source_id: str, *, timestamp: float) -> str:
        """Rotate a long continuous VAD turn at the ASR soft boundary."""
        old_turn_id = self._source_turn.pop(source_id, None)
        if old_turn_id is not None:
            old_turn = self._turns[old_turn_id]
            old_turn.active_sources.discard(source_id)
            if not old_turn.active_sources:
                old_turn.end_time = timestamp

        if (
            self._last_split_turn_id is not None
            and self._last_split_timestamp is not None
            and timestamp - self._last_split_timestamp
            <= self.turn_join_gap_seconds
        ):
            turn = self._turns[self._last_split_turn_id]
        else:
            turn = VadTurn(
                turn_id=f"turn-{uuid.uuid4().hex}",
                start_time=timestamp,
            )
            self._turns[turn.turn_id] = turn
        turn.active_sources.add(source_id)
        turn.all_sources.add(source_id)
        turn.end_time = None
        self._source_turn[source_id] = turn.turn_id
        self._last_split_turn_id = turn.turn_id
        self._last_split_timestamp = timestamp
        self._prune(timestamp)
        return turn.turn_id

    async def select_final(self, candidate: FinalCandidate) -> bool:
        async with self._lock:
            self._candidates[candidate.candidate_id] = candidate

        if self.final_settle_seconds > 0:
            await asyncio.sleep(self.final_settle_seconds)

        async with self._lock:
            current = self._candidates.get(candidate.candidate_id)
            if current is None:
                return False
            if current.winner_id is not None:
                return current.winner_id == current.candidate_id

            group = [
                item
                for item in self._candidates.values()
                if item.winner_id is None
                and item.turn_id == current.turn_id
                and self._same_utterance(current, item)
            ]
            if not group:
                group = [current]
            winner = max(group, key=lambda item: item.quality.score)
            for item in group:
                item.winner_id = winner.candidate_id

            cutoff = time.monotonic() - 20.0
            self._candidates = {
                key: item
                for key, item in self._candidates.items()
                if item.created_at >= cutoff
            }
            return winner.candidate_id == current.candidate_id

    @staticmethod
    def _text_similarity(left: str, right: str) -> float:
        left_words = set(
            re.findall(r"\w+", left.casefold(), flags=re.UNICODE)
        )
        right_words = set(
            re.findall(r"\w+", right.casefold(), flags=re.UNICODE)
        )
        if not left_words or not right_words:
            return 0.0
        return len(left_words & right_words) / min(
            len(left_words), len(right_words)
        )

    @staticmethod
    def _envelope_similarity(
        left: Sequence[float], right: Sequence[float]
    ) -> float:
        if len(left) < 3 or len(right) < 3:
            return 0.0
        best = 0.0
        for shift in range(-3, 4):
            if shift < 0:
                lhs = left[-shift:]
                rhs = right[: len(lhs)]
            elif shift > 0:
                rhs = right[shift:]
                lhs = left[: len(rhs)]
            else:
                length = min(len(left), len(right))
                lhs = left[:length]
                rhs = right[:length]
            length = min(len(lhs), len(rhs))
            if length < 3:
                continue
            lhs_array = np.asarray(lhs[:length], dtype=np.float32)
            rhs_array = np.asarray(rhs[:length], dtype=np.float32)
            denominator = float(
                np.linalg.norm(lhs_array) * np.linalg.norm(rhs_array)
            )
            if denominator > 1e-8:
                best = max(
                    best,
                    float(np.dot(lhs_array, rhs_array) / denominator),
                )
        return best

    @classmethod
    def _same_utterance(
        cls, left: FinalCandidate, right: FinalCandidate
    ) -> bool:
        overlap = (
            min(left.end_time, right.end_time)
            - max(left.start_time, right.start_time)
        )
        if overlap < 0.5:
            return False
        shorter_duration = max(
            1e-6,
            min(
                left.end_time - left.start_time,
                right.end_time - right.start_time,
            ),
        )
        overlap_ratio = overlap / shorter_duration
        stronger_rms = max(left.quality.rms, right.quality.rms, 1e-6)
        weaker_rms = min(left.quality.rms, right.quality.rms)
        rms_ratio = weaker_rms / stronger_rms
        quality_gap = abs(left.quality.score - right.quality.score)

        # Two microphones can decode a weak acoustic copy into different
        # words, especially at the tail of a turn. If their time ranges nearly
        # coincide and one source is far weaker, treat it as leakage even when
        # text/envelope similarity is unreliable. Similar-strength concurrent
        # speakers remain separate.
        weak_mic_copy = (
            left.source_id != right.source_id
            and overlap_ratio >= 0.60
            and rms_ratio <= 0.35
            and quality_gap >= 4.0
        )
        return (
            weak_mic_copy
            or cls._text_similarity(left.raw_text, right.raw_text) >= 0.58
            or cls._envelope_similarity(
                left.fingerprint, right.fingerprint
            )
            >= 0.94
        )

    def _prune(self, now: float) -> None:
        cutoff = now - 30.0
        self._turns = {
            key: turn
            for key, turn in self._turns.items()
            if turn.end_time is None or turn.end_time >= cutoff
        }
