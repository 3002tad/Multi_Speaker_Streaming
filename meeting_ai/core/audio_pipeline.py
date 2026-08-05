"""Audio front-end utilities shared by the LiveKit bridge and AI server.

This module intentionally has no eager model imports. It keeps the two audio
branches separate:

* speaker identity receives the VAD-selected, otherwise unmodified waveform;
* ASR receives a lightly enhanced waveform after cross-microphone selection.

Optional neural models are imported lazily so ordinary audio utilities and
unit tests do not pay a model-loading cost.
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


@dataclass(frozen=True)
class AsrPreprocessingTelemetry:
    """Observability for the lightweight legacy ASR frontend."""

    processed_seconds: float
    voiced_seconds: float
    average_noise_gain: float
    average_gain: float
    minimum_gain: float
    maximum_gain: float
    peak_limited_frames: int


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
        loudness_window_seconds: float = 0.60,
        boost_rate: float = 0.10,
        attenuation_rate: float = 0.30,
    ) -> None:
        self.sample_rate = sample_rate
        self.target_rms = target_rms
        self.minimum_noise_gain = minimum_noise_gain
        self.loudness_window_seconds = max(
            0.10, float(loudness_window_seconds)
        )
        self.boost_rate = float(np.clip(boost_rate, 0.01, 1.0))
        self.attenuation_rate = float(
            np.clip(attenuation_rate, 0.01, 1.0)
        )
        self._hp_alpha = math.exp(
            -2.0 * math.pi * high_pass_hz / sample_rate
        )
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._smoothed_gain = 1.0
        self._smoothed_loudness_sq: float | None = None
        self.reset_telemetry()

    def reset(self) -> None:
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._smoothed_gain = 1.0
        self._smoothed_loudness_sq = None
        self.reset_telemetry()

    def reset_telemetry(self) -> None:
        self._processed_samples = 0
        self._voiced_samples = 0
        self._weighted_noise_gain = 0.0
        self._weighted_gain = 0.0
        self._minimum_observed_gain = float("inf")
        self._maximum_observed_gain = 0.0
        self._peak_limited_frames = 0

    def telemetry(self) -> AsrPreprocessingTelemetry:
        processed = max(1, self._processed_samples)
        return AsrPreprocessingTelemetry(
            processed_seconds=self._processed_samples / self.sample_rate,
            voiced_seconds=self._voiced_samples / self.sample_rate,
            average_noise_gain=self._weighted_noise_gain / processed,
            average_gain=self._weighted_gain / processed,
            minimum_gain=(
                1.0
                if not math.isfinite(self._minimum_observed_gain)
                else self._minimum_observed_gain
            ),
            maximum_gain=self._maximum_observed_gain,
            peak_limited_frames=self._peak_limited_frames,
        )

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

        # Learn a loudness reference only from probable speech. Silence and
        # short pauses otherwise pull AGC upward and over-amplify the next
        # word. The window is deliberately longer than one ASR frame.
        speech_floor = max(0.004, quality.noise_floor * 1.8)
        speech_active = filtered_rms >= speech_floor
        if speech_active:
            frame_seconds = samples.size / self.sample_rate
            alpha = 1.0 - math.exp(
                -frame_seconds / self.loudness_window_seconds
            )
            frame_power = filtered_rms * filtered_rms
            if self._smoothed_loudness_sq is None:
                self._smoothed_loudness_sq = frame_power
            else:
                self._smoothed_loudness_sq = (
                    (1.0 - alpha) * self._smoothed_loudness_sq
                    + alpha * frame_power
                )
        loudness_rms = math.sqrt(
            max(
                1e-12,
                self._smoothed_loudness_sq
                if self._smoothed_loudness_sq is not None
                else filtered_rms * filtered_rms,
            )
        )
        desired_gain = float(
            np.clip(
                self.target_rms / max(loudness_rms * noise_gain, 1e-5),
                0.65,
                2.5,
            )
        )
        rate = (
            self.boost_rate
            if desired_gain > self._smoothed_gain
            else self.attenuation_rate
        )
        self._smoothed_gain += rate * (desired_gain - self._smoothed_gain)
        enhanced = filtered * noise_gain * self._smoothed_gain
        peak = float(np.max(np.abs(enhanced)))
        if peak > 0.97:
            enhanced *= 0.97 / peak
            self._peak_limited_frames += 1
        self._processed_samples += samples.size
        self._voiced_samples += samples.size if speech_active else 0
        self._weighted_noise_gain += noise_gain * samples.size
        self._weighted_gain += self._smoothed_gain * samples.size
        self._minimum_observed_gain = min(
            self._minimum_observed_gain, self._smoothed_gain
        )
        self._maximum_observed_gain = max(
            self._maximum_observed_gain, self._smoothed_gain
        )
        return enhanced.astype(np.float32, copy=False)


@dataclass(frozen=True)
class EnhancementTelemetry:
    """Per-turn observability for the ASR-only neural enhancement branch."""

    processed_seconds: float
    average_mix: float
    peak_mix: float


class DynamicEnhancementController:
    """Convert quality estimates into a smoothed DPDFNet mix coefficient."""

    def __init__(
        self,
        *,
        bypass_snr_db: float = 15.0,
        full_snr_db: float = 3.0,
        maximum_mix: float = 0.65,
        attack: float = 0.20,
        release: float = 0.65,
    ) -> None:
        if full_snr_db >= bypass_snr_db:
            raise ValueError(
                "full_snr_db must be below bypass_snr_db"
            )
        self.bypass_snr_db = float(bypass_snr_db)
        self.full_snr_db = float(full_snr_db)
        self.maximum_mix = float(np.clip(maximum_mix, 0.0, 1.0))
        self.attack = float(np.clip(attack, 0.0, 1.0))
        self.release = float(np.clip(release, 0.0, 1.0))
        self._mix = 0.0

    def reset(self) -> None:
        self._mix = 0.0

    def next_mix(self, quality: FrameQuality) -> float:
        # A squared ramp keeps clean/normal speech almost untouched and
        # reserves strong enhancement for genuinely poor SNR.
        normalized = float(
            np.clip(
                (self.bypass_snr_db - quality.snr_db)
                / (self.bypass_snr_db - self.full_snr_db),
                0.0,
                1.0,
            )
        )
        target = self.maximum_mix * normalized * normalized
        rate = self.attack if target >= self._mix else self.release
        self._mix += rate * (target - self._mix)
        return float(np.clip(self._mix, 0.0, self.maximum_mix))


class FixedEnhancementController:
    """Use a constant enhanced/raw mix for controlled frontend experiments."""

    def __init__(self, mix: float = 1.0) -> None:
        self.mix = float(np.clip(mix, 0.0, 1.0))

    def reset(self) -> None:
        return None

    def next_mix(self, quality: FrameQuality) -> float:
        del quality
        return self.mix


class StreamingDpdfNetEnhancer:
    """Stateful 16-kHz DPDFNet adapter with dynamic raw/enhanced blending.

    ``alignment_delay_samples`` compensates for model latency by delaying the
    raw branch and its mix envelope by the same amount. Without that explicit
    compensation, a sample-count aligned blend can combine speech from two
    different instants and smear short consonants. This object is
    intentionally ASR-only: WavLM must continue to receive raw VAD-selected
    audio.
    """

    def __init__(
        self,
        *,
        model_path: str,
        num_threads: int = 1,
        controller: (
            DynamicEnhancementController
            | FixedEnhancementController
            | None
        ) = None,
        denoiser: object | None = None,
        sample_rate: int = SAMPLE_RATE,
        alignment_delay_samples: int = 0,
        model_type: str = "dpdfnet",
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.alignment_delay_samples = max(
            0, int(alignment_delay_samples)
        )
        self.model_type = model_type.strip().lower()
        if self.model_type not in {"dpdfnet", "gtcrn"}:
            raise ValueError(
                "model_type must be either 'dpdfnet' or 'gtcrn'"
            )
        self.controller = controller or DynamicEnhancementController()
        self._reset_alignment_buffers()
        self._processed_samples = 0
        self._weighted_mix = 0.0
        self._peak_mix = 0.0

        if denoiser is None:
            from pathlib import Path

            import sherpa_onnx

            if not Path(model_path).is_file():
                raise FileNotFoundError(
                    "Không tìm thấy speech-enhancement model: "
                    f"{model_path}"
                )
            config = sherpa_onnx.OnlineSpeechDenoiserConfig()
            if self.model_type == "dpdfnet":
                config.model.dpdfnet.model = str(model_path)
            else:
                config.model.gtcrn.model = str(model_path)
            config.model.num_threads = max(1, int(num_threads))
            config.model.provider = "cpu"
            if not config.validate():
                raise RuntimeError(
                    f"{self.model_type} configuration is invalid"
                )
            denoiser = sherpa_onnx.OnlineSpeechDenoiser(config)
        self._denoiser = denoiser

        model_rate = int(getattr(self._denoiser, "sample_rate"))
        if model_rate != self.sample_rate:
            raise ValueError(
                f"{self.model_type} sample rate mismatch: "
                f"expected {self.sample_rate}, got {model_rate}"
            )

    def reset(self) -> None:
        self._denoiser.reset()
        self.controller.reset()
        self._reset_alignment_buffers()
        self.reset_telemetry()

    def _reset_alignment_buffers(self) -> None:
        self._raw_pending = np.zeros(
            self.alignment_delay_samples, dtype=np.float32
        )
        # Never mix the model output into the leading delay padding.
        self._mix_pending = np.zeros(
            self.alignment_delay_samples, dtype=np.float32
        )

    def reset_telemetry(self) -> None:
        self._processed_samples = 0
        self._weighted_mix = 0.0
        self._peak_mix = 0.0

    def telemetry(self) -> EnhancementTelemetry:
        average = self._weighted_mix / max(1, self._processed_samples)
        return EnhancementTelemetry(
            processed_seconds=self._processed_samples / self.sample_rate,
            average_mix=float(average),
            peak_mix=float(self._peak_mix),
        )

    def process(
        self,
        audio: np.ndarray,
        *,
        quality: FrameQuality,
    ) -> np.ndarray:
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return samples.copy()
        mix = self.controller.next_mix(quality)
        self._append_pending(samples, mix)
        result = self._denoiser.run(samples, self.sample_rate)
        return self._blend_result(result)

    def flush(self) -> np.ndarray:
        result = self._denoiser.flush()
        emitted = self._blend_result(result)
        if self.alignment_delay_samples <= 0:
            return emitted

        # Some online denoisers keep output length equal to input length and
        # therefore cannot emit the last algorithmic-delay samples. Preserve
        # those samples from the delayed raw branch instead of truncating the
        # end of an utterance.
        tail_size = min(len(self._raw_pending), len(self._mix_pending))
        if tail_size <= 0:
            return emitted
        raw_tail = self._raw_pending[:tail_size].copy()
        self._raw_pending = self._raw_pending[tail_size:]
        self._mix_pending = self._mix_pending[tail_size:]
        self._processed_samples += tail_size
        if emitted.size == 0:
            return raw_tail
        return np.concatenate((emitted, raw_tail)).astype(
            np.float32, copy=False
        )

    def _append_pending(self, samples: np.ndarray, mix: float) -> None:
        self._raw_pending = np.concatenate((self._raw_pending, samples))
        self._mix_pending = np.concatenate(
            (
                self._mix_pending,
                np.full(len(samples), mix, dtype=np.float32),
            )
        )

    def _blend_result(self, result: object) -> np.ndarray:
        denoised = np.asarray(
            getattr(result, "samples", ()), dtype=np.float32
        ).reshape(-1)
        if denoised.size == 0:
            return denoised
        usable = min(
            len(denoised), len(self._raw_pending), len(self._mix_pending)
        )
        if usable <= 0:
            return np.empty(0, dtype=np.float32)
        raw = self._raw_pending[:usable]
        mix = self._mix_pending[:usable]
        self._raw_pending = self._raw_pending[usable:]
        self._mix_pending = self._mix_pending[usable:]
        denoised = denoised[:usable]
        blended = raw * (1.0 - mix) + denoised * mix
        self._processed_samples += usable
        self._weighted_mix += float(np.sum(mix, dtype=np.float64))
        self._peak_mix = max(self._peak_mix, float(np.max(mix)))
        return blended.astype(np.float32, copy=False)


@dataclass(frozen=True)
class VoicePreservationTelemetry:
    """Observability for the raw/enhanced preservation gate."""

    input_seconds: float
    evaluated_speech_frames: int
    accepted_speech_frames: int
    fallback_speech_frames: int
    noise_frames: int
    average_mix: float
    peak_mix: float
    average_correlation: float
    average_energy_ratio: float
    average_speech_band_ratio: float


class StreamingVoicePreservationGate:
    """Blend only enhanced frames that retain the aligned speech waveform.

    The neural model is still evaluated in shadow on every input frame. Its
    waveform is mixed into the ASR branch only when correlation, total energy,
    the 1--4 kHz speech band, and clipping all pass conservative checks.
    Otherwise the time-aligned raw branch is used.
    """

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        alignment_delay_samples: int = 640,
        minimum_correlation: float = 0.93,
        minimum_energy_ratio: float = 0.65,
        maximum_energy_ratio: float = 1.35,
        minimum_speech_band_ratio: float = 0.80,
        maximum_speech_mix: float = 0.10,
        maximum_noise_mix: float = 0.65,
        crossfade_samples: int = 240,
        activity_floor: float = 0.003,
    ) -> None:
        if minimum_energy_ratio <= 0:
            raise ValueError("minimum_energy_ratio must be positive")
        if maximum_energy_ratio < minimum_energy_ratio:
            raise ValueError(
                "maximum_energy_ratio must not be below minimum"
            )
        self.sample_rate = int(sample_rate)
        self.alignment_delay_samples = max(
            0, int(alignment_delay_samples)
        )
        self.minimum_correlation = float(
            np.clip(minimum_correlation, -1.0, 1.0)
        )
        self.minimum_energy_ratio = float(minimum_energy_ratio)
        self.maximum_energy_ratio = float(maximum_energy_ratio)
        self.minimum_speech_band_ratio = max(
            0.0, float(minimum_speech_band_ratio)
        )
        self.maximum_speech_mix = float(
            np.clip(maximum_speech_mix, 0.0, 1.0)
        )
        self.maximum_noise_mix = float(
            np.clip(maximum_noise_mix, 0.0, 1.0)
        )
        self.crossfade_samples = max(0, int(crossfade_samples))
        self.activity_floor = max(1e-6, float(activity_floor))
        self.reset()

    def reset(self) -> None:
        self._raw_pending = np.zeros(
            self.alignment_delay_samples, dtype=np.float32
        )
        self._current_mix = 0.0
        self._input_samples = 0
        self._output_samples = 0
        self._weighted_mix = 0.0
        self._peak_mix = 0.0
        self._speech_frames = 0
        self._accepted_speech_frames = 0
        self._fallback_speech_frames = 0
        self._noise_frames = 0
        self._correlations: list[float] = []
        self._energy_ratios: list[float] = []
        self._speech_band_ratios: list[float] = []

    @staticmethod
    def _rms(samples: np.ndarray) -> float:
        return float(
            np.sqrt(np.mean(np.square(samples), dtype=np.float64))
        )

    def _speech_band_rms(self, samples: np.ndarray) -> float:
        if len(samples) < 16:
            return self._rms(samples)
        centered = samples - float(np.mean(samples))
        windowed = centered * np.hanning(len(centered)).astype(np.float32)
        spectrum = np.fft.rfft(windowed)
        frequencies = np.fft.rfftfreq(
            len(windowed), d=1.0 / self.sample_rate
        )
        band = spectrum[
            (frequencies >= 1_000.0) & (frequencies <= 4_000.0)
        ]
        if band.size == 0:
            return 0.0
        return float(
            np.sqrt(np.mean(np.square(np.abs(band)), dtype=np.float64))
        )

    @staticmethod
    def _correlation(
        raw: np.ndarray, enhanced: np.ndarray
    ) -> float:
        raw_centered = raw - float(np.mean(raw))
        enhanced_centered = enhanced - float(np.mean(enhanced))
        denominator = float(
            np.linalg.norm(raw_centered)
            * np.linalg.norm(enhanced_centered)
        )
        if denominator <= 1e-9:
            return 1.0
        return float(
            np.clip(
                np.dot(raw_centered, enhanced_centered) / denominator,
                -1.0,
                1.0,
            )
        )

    def process(
        self,
        raw_audio: np.ndarray,
        enhanced_audio: np.ndarray,
        *,
        quality: FrameQuality,
    ) -> np.ndarray:
        raw_input = np.asarray(
            raw_audio, dtype=np.float32
        ).reshape(-1)
        enhanced = np.asarray(
            enhanced_audio, dtype=np.float32
        ).reshape(-1)
        if raw_input.size:
            self._raw_pending = np.concatenate(
                (self._raw_pending, raw_input)
            )
            self._input_samples += len(raw_input)
        if enhanced.size == 0:
            return enhanced.copy()

        usable = min(len(enhanced), len(self._raw_pending))
        if usable <= 0:
            return np.empty(0, dtype=np.float32)
        raw = self._raw_pending[:usable]
        self._raw_pending = self._raw_pending[usable:]
        enhanced = enhanced[:usable]

        raw_rms = self._rms(raw)
        speech_active = raw_rms >= max(
            self.activity_floor,
            float(quality.noise_floor) * 1.5,
        )
        if speech_active:
            correlation = self._correlation(raw, enhanced)
            enhanced_rms = self._rms(enhanced)
            energy_ratio = enhanced_rms / max(raw_rms, 1e-7)
            raw_band = self._speech_band_rms(raw)
            enhanced_band = self._speech_band_rms(enhanced)
            speech_band_ratio = enhanced_band / max(raw_band, 1e-7)
            enhanced_clipping = float(
                np.mean(np.abs(enhanced) >= 0.98)
            )
            clipping_preserved = enhanced_clipping <= (
                float(quality.clipping_ratio) + 0.001
            )
            preserved = (
                correlation >= self.minimum_correlation
                and self.minimum_energy_ratio
                <= energy_ratio
                <= self.maximum_energy_ratio
                and speech_band_ratio >= self.minimum_speech_band_ratio
                and clipping_preserved
            )
            target_mix = self.maximum_speech_mix if preserved else 0.0
            self._speech_frames += 1
            if preserved:
                self._accepted_speech_frames += 1
            else:
                self._fallback_speech_frames += 1
            self._correlations.append(correlation)
            self._energy_ratios.append(energy_ratio)
            self._speech_band_ratios.append(speech_band_ratio)
        else:
            target_mix = self.maximum_noise_mix
            self._noise_frames += 1

        mix = np.full(usable, target_mix, dtype=np.float32)
        fade_size = min(self.crossfade_samples, usable)
        if fade_size:
            mix[:fade_size] = np.linspace(
                self._current_mix,
                target_mix,
                fade_size,
                endpoint=True,
                dtype=np.float32,
            )
        self._current_mix = target_mix
        output = raw * (1.0 - mix) + enhanced * mix
        self._output_samples += usable
        self._weighted_mix += float(np.sum(mix, dtype=np.float64))
        self._peak_mix = max(
            self._peak_mix, float(np.max(mix, initial=0.0))
        )
        return output.astype(np.float32, copy=False)

    def flush(self) -> np.ndarray:
        """Return speech not covered by the model without truncating it."""
        tail = self._raw_pending.copy()
        self._raw_pending = np.empty(0, dtype=np.float32)
        if tail.size:
            self._output_samples += len(tail)
            # The uncovered model tail is deliberately 100% raw.
            self._current_mix = 0.0
        return tail

    def telemetry(self) -> VoicePreservationTelemetry:
        def average(values: list[float]) -> float:
            return float(np.mean(values)) if values else 0.0

        return VoicePreservationTelemetry(
            input_seconds=self._input_samples / self.sample_rate,
            evaluated_speech_frames=self._speech_frames,
            accepted_speech_frames=self._accepted_speech_frames,
            fallback_speech_frames=self._fallback_speech_frames,
            noise_frames=self._noise_frames,
            average_mix=self._weighted_mix
            / max(1, self._output_samples),
            peak_mix=float(self._peak_mix),
            average_correlation=average(self._correlations),
            average_energy_ratio=average(self._energy_ratios),
            average_speech_band_ratio=average(
                self._speech_band_ratios
            ),
        )


@dataclass(frozen=True)
class PostConditioningTelemetry:
    """Observability for the lightweight stage after neural enhancement."""

    processed_seconds: float
    average_gain: float
    minimum_gain: float
    maximum_gain: float
    peak_limited_frames: int


class StreamingAsrPostConditioner:
    """Minimal stateful conditioning after DPDFNet.

    DPDFNet receives the untouched 16-kHz waveform. This stage only removes
    residual DC below the speech band, applies slow bounded gain correction
    to reduce microphone-level differences, and prevents clipping. It does
    not estimate noise or attenuate spectral content.
    """

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        dc_block_hz: float = 20.0,
        target_rms: float = 0.055,
        minimum_gain: float = 0.75,
        maximum_gain: float = 1.50,
        attenuation_rate: float = 0.08,
        boost_rate: float = 0.02,
        activity_floor: float = 0.003,
        peak_limit: float = 0.97,
    ) -> None:
        if minimum_gain <= 0.0 or maximum_gain < minimum_gain:
            raise ValueError("invalid post-conditioning gain range")
        if not 0.0 < peak_limit <= 1.0:
            raise ValueError("peak_limit must be in (0, 1]")
        self.sample_rate = int(sample_rate)
        self.target_rms = max(1e-5, float(target_rms))
        self.minimum_gain = float(minimum_gain)
        self.maximum_gain = float(maximum_gain)
        self.attenuation_rate = float(
            np.clip(attenuation_rate, 0.0, 1.0)
        )
        self.boost_rate = float(np.clip(boost_rate, 0.0, 1.0))
        self.activity_floor = max(0.0, float(activity_floor))
        self.peak_limit = float(peak_limit)
        self._dc_alpha = math.exp(
            -2.0 * math.pi * max(0.0, float(dc_block_hz))
            / self.sample_rate
        )
        self.reset()

    def reset(self) -> None:
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._gain = 1.0
        self._processed_samples = 0
        self._weighted_gain = 0.0
        self._minimum_observed_gain = 1.0
        self._maximum_observed_gain = 1.0
        self._peak_limited_frames = 0

    def process(self, audio: np.ndarray) -> np.ndarray:
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return samples.copy()

        conditioned = np.empty_like(samples)
        previous_input = self._previous_input
        previous_output = self._previous_output
        alpha = self._dc_alpha
        for index, sample in enumerate(samples):
            output = alpha * (
                previous_output + float(sample) - previous_input
            )
            conditioned[index] = output
            previous_input = float(sample)
            previous_output = output
        self._previous_input = previous_input
        self._previous_output = previous_output

        rms = float(
            np.sqrt(np.mean(np.square(conditioned), dtype=np.float64))
        )
        if rms >= self.activity_floor:
            desired_gain = float(
                np.clip(
                    self.target_rms / max(rms, 1e-6),
                    self.minimum_gain,
                    self.maximum_gain,
                )
            )
            rate = (
                self.attenuation_rate
                if desired_gain < self._gain
                else self.boost_rate
            )
            self._gain += rate * (desired_gain - self._gain)

        conditioned *= self._gain
        peak = float(np.max(np.abs(conditioned)))
        if peak > self.peak_limit:
            conditioned *= self.peak_limit / peak
            self._peak_limited_frames += 1

        self._processed_samples += len(conditioned)
        self._weighted_gain += self._gain * len(conditioned)
        self._minimum_observed_gain = min(
            self._minimum_observed_gain, self._gain
        )
        self._maximum_observed_gain = max(
            self._maximum_observed_gain, self._gain
        )
        return conditioned.astype(np.float32, copy=False)

    def telemetry(self) -> PostConditioningTelemetry:
        average_gain = self._weighted_gain / max(
            1, self._processed_samples
        )
        return PostConditioningTelemetry(
            processed_seconds=self._processed_samples / self.sample_rate,
            average_gain=float(average_gain),
            minimum_gain=float(self._minimum_observed_gain),
            maximum_gain=float(self._maximum_observed_gain),
            peak_limited_frames=self._peak_limited_frames,
        )


class StreamingGuardedEnhancementFrontend:
    """Raw → denoiser candidate → preservation gate → post-conditioner."""

    def __init__(
        self,
        *,
        model_path: str,
        num_threads: int = 1,
        denoiser: object | None = None,
        sample_rate: int = SAMPLE_RATE,
        dc_block_hz: float = 20.0,
        target_rms: float = 0.055,
        minimum_gain: float = 0.75,
        maximum_gain: float = 1.50,
        attenuation_rate: float = 0.08,
        boost_rate: float = 0.02,
        activity_floor: float = 0.003,
        peak_limit: float = 0.97,
        model_type: str = "dpdfnet",
        alignment_delay_samples: int = 640,
        preservation_minimum_correlation: float = 0.93,
        preservation_minimum_energy_ratio: float = 0.65,
        preservation_maximum_energy_ratio: float = 1.35,
        preservation_minimum_speech_band_ratio: float = 0.80,
        preservation_maximum_speech_mix: float = 0.10,
        preservation_maximum_noise_mix: float = 0.65,
        preservation_crossfade_samples: int = 240,
    ) -> None:
        self.model_type = model_type.strip().lower()
        self.enhancer = StreamingDpdfNetEnhancer(
            model_path=model_path,
            num_threads=num_threads,
            denoiser=denoiser,
            sample_rate=sample_rate,
            controller=FixedEnhancementController(1.0),
            alignment_delay_samples=0,
            model_type=self.model_type,
        )
        self.preservation_gate = StreamingVoicePreservationGate(
            sample_rate=sample_rate,
            alignment_delay_samples=alignment_delay_samples,
            minimum_correlation=preservation_minimum_correlation,
            minimum_energy_ratio=preservation_minimum_energy_ratio,
            maximum_energy_ratio=preservation_maximum_energy_ratio,
            minimum_speech_band_ratio=(
                preservation_minimum_speech_band_ratio
            ),
            maximum_speech_mix=preservation_maximum_speech_mix,
            maximum_noise_mix=preservation_maximum_noise_mix,
            crossfade_samples=preservation_crossfade_samples,
            activity_floor=activity_floor,
        )
        self.conditioner = StreamingAsrPostConditioner(
            sample_rate=sample_rate,
            dc_block_hz=dc_block_hz,
            target_rms=target_rms,
            minimum_gain=minimum_gain,
            maximum_gain=maximum_gain,
            attenuation_rate=attenuation_rate,
            boost_rate=boost_rate,
            activity_floor=activity_floor,
            peak_limit=peak_limit,
        )

    def reset(self) -> None:
        self.enhancer.reset()
        self.preservation_gate.reset()
        self.conditioner.reset()

    def process(
        self,
        audio: np.ndarray,
        *,
        quality: FrameQuality,
    ) -> np.ndarray:
        enhanced = self.enhancer.process(audio, quality=quality)
        preserved = self.preservation_gate.process(
            audio, enhanced, quality=quality
        )
        return self.conditioner.process(preserved)

    def flush(self) -> np.ndarray:
        enhanced_tail = self.enhancer.flush()
        parts = []
        if enhanced_tail.size:
            # No new raw samples arrive at flush; the gate consumes the raw
            # samples that were delayed for model alignment.
            quality = FrameQuality(
                rms=0.0,
                peak=0.0,
                clipping_ratio=0.0,
                noise_floor=self.preservation_gate.activity_floor,
                snr_db=-20.0,
                score=-40.0,
            )
            aligned = self.preservation_gate.process(
                np.empty(0, dtype=np.float32),
                enhanced_tail,
                quality=quality,
            )
            if aligned.size:
                parts.append(aligned)
        raw_tail = self.preservation_gate.flush()
        if raw_tail.size:
            parts.append(raw_tail)
        if not parts:
            return np.empty(0, dtype=np.float32)
        return self.conditioner.process(np.concatenate(parts))

    def telemetry(
        self,
    ) -> tuple[PostConditioningTelemetry, VoicePreservationTelemetry]:
        return (
            self.conditioner.telemetry(),
            self.preservation_gate.telemetry(),
        )


# Backwards-compatible alias for earlier A/B scripts.
StreamingDpdfNetFrontend = StreamingGuardedEnhancementFrontend


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
