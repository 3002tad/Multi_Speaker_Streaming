from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace

import numpy as np

from backend.audio_pipeline import (
    AudioQualityTracker,
    CoordinatedVadTimeline,
    DynamicEnhancementController,
    FinalCandidate,
    FrameQuality,
    StreamingGuardedEnhancementFrontend,
    StreamingDpdfNetEnhancer,
    StreamingAsrPreprocessor,
    pack_audio_packet,
    select_speaker_windows,
    speech_envelope,
    summarize_quality,
    unpack_audio_packet,
)
from backend.final_turn import choose_redecode_transcript


def quality(
    *, rms: float, score: float, snr: float = 20.0
) -> FrameQuality:
    return FrameQuality(
        rms=rms,
        peak=min(0.9, rms * 4),
        clipping_ratio=0.0,
        noise_floor=0.003,
        snr_db=snr,
        score=score,
    )


class AudioPacketTests(unittest.TestCase):
    def test_timestamped_packet_round_trip_and_legacy_fallback(self) -> None:
        pcm = b"\x01\x02\x03\x04"
        packet = pack_audio_packet(
            pcm, sequence=42, captured_at=123.25
        )
        decoded, sequence, timestamp = unpack_audio_packet(packet)
        self.assertEqual(decoded, pcm)
        self.assertEqual(sequence, 42)
        self.assertEqual(timestamp, 123.25)

        decoded, sequence, timestamp = unpack_audio_packet(
            pcm, fallback_timestamp=99.0
        )
        self.assertEqual(decoded, pcm)
        self.assertIsNone(sequence)
        self.assertEqual(timestamp, 99.0)


class FinalTurnRedecodeTests(unittest.TestCase):
    def test_accepts_compatible_replay_that_restores_a_word(self) -> None:
        decision = choose_redecode_transcript(
            "triển khai hệ thống dữ liệu",
            "triển khai hệ thống dữ liệu realtime",
        )
        self.assertTrue(decision.selected_redecode)
        self.assertEqual(decision.reason, "compatible_word_gain")

    def test_rejects_a_different_or_excessively_long_replay(self) -> None:
        decision = choose_redecode_transcript(
            "triển khai hệ thống dữ liệu",
            "chúng ta cần triển khai toàn bộ phương án khác hoàn toàn",
        )
        self.assertFalse(decision.selected_redecode)
        self.assertIn(decision.reason, {"low_overlap", "too_long"})

    def test_keeps_streaming_text_when_replay_drops_words(self) -> None:
        decision = choose_redecode_transcript(
            "hệ thống thu thập dữ liệu realtime",
            "hệ thống dữ liệu",
        )
        self.assertFalse(decision.selected_redecode)
        self.assertIn(decision.reason, {"low_overlap", "no_word_gain"})


class AudioBranchTests(unittest.TestCase):
    def test_asr_preprocessor_removes_dc_and_limits_peak(self) -> None:
        tracker = AudioQualityTracker(initial_noise_floor=0.002)
        audio = np.full(1600, 0.12, dtype=np.float32)
        measured = tracker.measure(audio, speech_active=True)
        processor = StreamingAsrPreprocessor()
        enhanced = processor.process(audio, quality=measured)

        self.assertEqual(len(enhanced), len(audio))
        self.assertTrue(np.all(np.isfinite(enhanced)))
        self.assertLess(abs(float(np.mean(enhanced))), 0.02)
        self.assertLessEqual(float(np.max(np.abs(enhanced))), 0.9701)
        telemetry = processor.telemetry()
        self.assertAlmostEqual(telemetry.processed_seconds, 0.1)
        self.assertGreater(telemetry.average_gain, 0.0)

    def test_asr_preprocessor_keeps_loudness_reference_through_pause(self) -> None:
        tracker = AudioQualityTracker(initial_noise_floor=0.002)
        processor = StreamingAsrPreprocessor(
            loudness_window_seconds=0.60,
            boost_rate=0.10,
            attenuation_rate=0.30,
        )
        timeline = np.arange(1600, dtype=np.float32) / 16_000
        speech = (0.03 * np.sin(2 * np.pi * 180 * timeline)).astype(
            np.float32
        )
        silence = np.zeros(1600, dtype=np.float32)
        processor.process(
            speech,
            quality=tracker.measure(speech, speech_active=True),
        )
        processor.process(
            silence,
            quality=tracker.measure(silence, speech_active=False),
        )
        telemetry = processor.telemetry()
        self.assertAlmostEqual(telemetry.voiced_seconds, 0.1)
        self.assertGreater(telemetry.average_gain, 0.0)

    def test_speaker_branch_keeps_clean_raw_windows(self) -> None:
        timeline = np.arange(4 * 16_000, dtype=np.float32) / 16_000
        clean = (0.04 * np.sin(2 * np.pi * 180 * timeline)).astype(
            np.float32
        )
        clipped = np.ones(4 * 16_000, dtype=np.float32)
        windows = select_speaker_windows(
            np.concatenate([clean, clipped]),
            minimum_seconds=2.5,
        )

        self.assertEqual(len(windows), 1)
        self.assertLess(float(np.max(np.abs(windows[0]))), 0.98)

    def test_speaker_windows_cover_tone_changes_over_time(self) -> None:
        timeline = np.arange(12 * 16_000, dtype=np.float32) / 16_000
        amplitude = np.select(
            [
                timeline < 4,
                timeline < 8,
            ],
            [0.02, 0.08],
            default=0.14,
        )
        audio = amplitude * np.sin(2 * np.pi * 180 * timeline)
        windows = select_speaker_windows(
            audio.astype(np.float32),
            minimum_seconds=2.5,
            max_windows=3,
        )
        rms_values = [
            float(np.sqrt(np.mean(np.square(window))))
            for window in windows
        ]
        self.assertEqual(len(windows), 3)
        self.assertLess(rms_values[0], rms_values[-1])

    def test_noise_floor_rejects_pre_vad_speech_and_bounds_snr(self) -> None:
        tracker = AudioQualityTracker(initial_noise_floor=0.003)
        silence = np.full(1600, 0.002, dtype=np.float32)
        for _ in range(20):
            tracker.measure(silence, speech_active=False)
        floor_before_speech = tracker.noise_floor

        leaked_speech = np.full(1600, 0.08, dtype=np.float32)
        for _ in range(8):
            measurement = tracker.measure(
                leaked_speech, speech_active=False
            )

        self.assertLessEqual(
            tracker.noise_floor, floor_before_speech * 1.05
        )
        self.assertGreater(measurement.snr_db, 20.0)

        digital_silence = np.zeros(1600, dtype=np.float32)
        measurement = tracker.measure(
            digital_silence, speech_active=True
        )
        self.assertEqual(measurement.snr_db, -20.0)

    def test_quality_summary_ignores_internal_silence(self) -> None:
        observations = [
            quality(rms=0.001, score=-20)
            for _ in range(6)
        ] + [
            quality(rms=0.06, score=22)
            for _ in range(4)
        ]
        summary = summarize_quality(observations)
        self.assertAlmostEqual(summary.rms, 0.06, places=4)

    def test_dynamic_enhancement_preserves_clean_and_blends_noise(
        self,
    ) -> None:
        class DelayedDenoiser:
            sample_rate = 16_000

            def __init__(self) -> None:
                self.pending = np.empty(0, dtype=np.float32)

            def run(self, samples, sample_rate):
                self.assert_sample_rate(sample_rate)
                combined = np.concatenate(
                    (self.pending, np.asarray(samples, dtype=np.float32))
                )
                self.pending = combined[-2:].copy()
                # A deliberately aggressive fake enhancement: it removes
                # all emitted speech, so the blend is easy to verify.
                return SimpleNamespace(
                    samples=np.zeros(
                        max(0, len(combined) - 2), dtype=np.float32
                    )
                )

            def flush(self):
                emitted = self.pending
                self.pending = np.empty(0, dtype=np.float32)
                return SimpleNamespace(samples=np.zeros_like(emitted))

            def reset(self) -> None:
                self.pending = np.empty(0, dtype=np.float32)

            @staticmethod
            def assert_sample_rate(sample_rate) -> None:
                if sample_rate != 16_000:
                    raise AssertionError("unexpected sample rate")

        controller = DynamicEnhancementController(
            bypass_snr_db=22,
            full_snr_db=7,
            maximum_mix=1.0,
            attack=1.0,
            release=1.0,
        )
        enhancer = StreamingDpdfNetEnhancer(
            model_path="unused.onnx",
            denoiser=DelayedDenoiser(),
            controller=controller,
        )
        source = np.ones(4, dtype=np.float32)

        clean = np.concatenate(
            (
                enhancer.process(
                    source, quality=quality(rms=0.1, score=20, snr=30)
                ),
                enhancer.flush(),
            )
        )
        self.assertTrue(np.allclose(clean, source))
        self.assertAlmostEqual(enhancer.telemetry().average_mix, 0.0)

        enhancer.reset()
        noisy = np.concatenate(
            (
                enhancer.process(
                    source, quality=quality(rms=0.02, score=4, snr=0)
                ),
                enhancer.flush(),
            )
        )
        self.assertEqual(len(noisy), len(source))
        self.assertTrue(np.allclose(noisy, 0.0))
        self.assertAlmostEqual(enhancer.telemetry().peak_mix, 1.0)

    def test_dpdfnet_frontend_receives_raw_then_conditions_output(
        self,
    ) -> None:
        class RecordingDenoiser:
            sample_rate = 16_000

            def __init__(self) -> None:
                self.received: list[np.ndarray] = []

            def run(self, samples, sample_rate):
                self.assert_sample_rate(sample_rate)
                frame = np.asarray(samples, dtype=np.float32).copy()
                self.received.append(frame)
                # Simulate a model that increases output level. The
                # post-conditioner must keep it finite and unclipped.
                return SimpleNamespace(samples=frame * 4.0)

            def flush(self):
                return SimpleNamespace(
                    samples=np.empty(0, dtype=np.float32)
                )

            def reset(self) -> None:
                self.received.clear()

            @staticmethod
            def assert_sample_rate(sample_rate) -> None:
                if sample_rate != 16_000:
                    raise AssertionError("unexpected sample rate")

        denoiser = RecordingDenoiser()
        frontend = StreamingGuardedEnhancementFrontend(
            model_path="unused.onnx",
            denoiser=denoiser,
            alignment_delay_samples=0,
            preservation_minimum_energy_ratio=0.1,
            preservation_maximum_energy_ratio=5.0,
            preservation_minimum_speech_band_ratio=0.0,
            preservation_maximum_speech_mix=1.0,
            preservation_maximum_noise_mix=1.0,
            preservation_crossfade_samples=0,
            target_rms=0.055,
            minimum_gain=0.75,
            maximum_gain=1.50,
        )
        timeline = np.arange(16_000, dtype=np.float32) / 16_000
        source = (
            0.15 + 0.25 * np.sin(2 * np.pi * 180 * timeline)
        ).astype(np.float32)
        measured = quality(rms=0.2, score=20, snr=25)

        output = np.concatenate(
            [
                frontend.process(
                    source[start : start + 1600],
                    quality=measured,
                )
                for start in range(0, len(source), 1600)
            ]
            + [frontend.flush()]
        )

        self.assertTrue(np.allclose(np.concatenate(denoiser.received), source))
        self.assertEqual(len(output), len(source))
        self.assertTrue(np.all(np.isfinite(output)))
        self.assertLess(abs(float(np.mean(output))), 0.01)
        self.assertLessEqual(float(np.max(np.abs(output))), 0.9701)
        post, gate = frontend.telemetry()
        self.assertAlmostEqual(gate.input_seconds, 1.0)
        # The preservation gate rejects the deliberately clipping candidate,
        # so the downstream limiter should not need to repair it.
        self.assertEqual(post.peak_limited_frames, 0)
        self.assertGreaterEqual(post.minimum_gain, 0.75)
        self.assertLessEqual(post.maximum_gain, 1.50)
        self.assertGreater(gate.fallback_speech_frames, 0)

    def test_guard_aligns_40ms_model_delay_without_smearing_voice(
        self,
    ) -> None:
        class DelayedIdentityDenoiser:
            sample_rate = 16_000

            def __init__(self, delay: int = 640) -> None:
                self.delay = delay
                self.pending = np.zeros(delay, dtype=np.float32)

            def run(self, samples, sample_rate):
                if sample_rate != self.sample_rate:
                    raise AssertionError("unexpected sample rate")
                samples = np.asarray(samples, dtype=np.float32)
                combined = np.concatenate((self.pending, samples))
                emitted = combined[: len(samples)]
                self.pending = combined[len(samples) :]
                return SimpleNamespace(samples=emitted)

            def flush(self):
                return SimpleNamespace(
                    samples=np.empty(0, dtype=np.float32)
                )

            def reset(self) -> None:
                self.pending = np.zeros(self.delay, dtype=np.float32)

        denoiser = DelayedIdentityDenoiser()
        frontend = StreamingGuardedEnhancementFrontend(
            model_path="unused.onnx",
            denoiser=denoiser,
            alignment_delay_samples=640,
            preservation_maximum_speech_mix=1.0,
            preservation_maximum_noise_mix=0.0,
            preservation_crossfade_samples=0,
            dc_block_hz=0,
            target_rms=0.05,
            minimum_gain=1.0,
            maximum_gain=1.0,
            peak_limit=1.0,
        )
        timeline = np.arange(4_800, dtype=np.float32) / 16_000
        source = (
            0.08 * np.sin(2 * np.pi * 230 * timeline)
            + 0.03 * np.sin(2 * np.pi * 1_700 * timeline)
        ).astype(np.float32)
        measured = quality(rms=0.06, score=18, snr=18)
        output = np.concatenate(
            [
                frontend.process(
                    source[start : start + 1600],
                    quality=measured,
                )
                for start in range(0, len(source), 1600)
            ]
            + [frontend.flush()]
        )

        expected = np.concatenate(
            (np.zeros(640, dtype=np.float32), source)
        )
        self.assertEqual(len(output), len(expected))
        self.assertTrue(np.allclose(output, expected, atol=1e-5))
        _, gate = frontend.telemetry()
        self.assertEqual(gate.fallback_speech_frames, 0)
        self.assertGreater(gate.accepted_speech_frames, 0)

    def test_guard_falls_back_to_aligned_raw_when_voice_is_erased(
        self,
    ) -> None:
        class DelayedDestructiveDenoiser:
            sample_rate = 16_000

            def run(self, samples, sample_rate):
                if sample_rate != self.sample_rate:
                    raise AssertionError("unexpected sample rate")
                return SimpleNamespace(
                    samples=np.zeros(len(samples), dtype=np.float32)
                )

            def flush(self):
                return SimpleNamespace(
                    samples=np.empty(0, dtype=np.float32)
                )

            def reset(self) -> None:
                return None

        frontend = StreamingGuardedEnhancementFrontend(
            model_path="unused.onnx",
            denoiser=DelayedDestructiveDenoiser(),
            alignment_delay_samples=640,
            preservation_maximum_speech_mix=1.0,
            preservation_maximum_noise_mix=0.0,
            preservation_crossfade_samples=0,
            dc_block_hz=0,
            target_rms=0.05,
            minimum_gain=1.0,
            maximum_gain=1.0,
            peak_limit=1.0,
        )
        timeline = np.arange(3_200, dtype=np.float32) / 16_000
        source = (
            0.10 * np.sin(2 * np.pi * 800 * timeline)
        ).astype(np.float32)
        measured = quality(rms=0.07, score=12, snr=10)
        output = np.concatenate(
            [
                frontend.process(
                    source[start : start + 1600],
                    quality=measured,
                )
                for start in range(0, len(source), 1600)
            ]
            + [frontend.flush()]
        )

        expected = np.concatenate(
            (np.zeros(640, dtype=np.float32), source)
        )
        self.assertTrue(np.allclose(output, expected, atol=1e-5))
        _, gate = frontend.telemetry()
        self.assertGreater(gate.fallback_speech_frames, 0)
        self.assertEqual(gate.accepted_speech_frames, 0)


class CoordinatedTimelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_vad_timeline_routes_only_clearer_leakage_mic(
        self,
    ) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0)
        timestamp = 50.0
        turn_a = timeline.speech_started("mic-a", timestamp=timestamp)
        turn_b = timeline.speech_started("mic-b", timestamp=timestamp + 0.1)
        self.assertEqual(turn_a, turn_b)

        timeline.note_frame(
            "mic-a",
            timestamp=timestamp + 0.2,
            quality=quality(rms=0.08, score=25),
            sequence=10,
        )
        timeline.note_frame(
            "mic-b",
            timestamp=timestamp + 0.2,
            quality=quality(rms=0.015, score=10),
            sequence=10,
        )
        self.assertTrue(
            timeline.should_route_asr(
                "mic-a", timestamp=timestamp + 0.2
            )
        )
        self.assertFalse(
            timeline.should_route_asr(
                "mic-b", timestamp=timestamp + 0.2
            )
        )

    async def test_similar_quality_mics_are_kept_for_real_overlap(
        self,
    ) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0)
        timestamp = 80.0
        timeline.speech_started("mic-a", timestamp=timestamp)
        timeline.speech_started("mic-b", timestamp=timestamp)
        timeline.note_frame(
            "mic-a",
            timestamp=timestamp,
            quality=quality(rms=0.07, score=22),
            sequence=1,
        )
        timeline.note_frame(
            "mic-b",
            timestamp=timestamp,
            quality=quality(rms=0.06, score=20),
            sequence=1,
        )
        self.assertTrue(
            timeline.should_route_asr("mic-a", timestamp=timestamp)
        )
        self.assertTrue(
            timeline.should_route_asr("mic-b", timestamp=timestamp)
        )

    async def test_soft_split_creates_a_new_shared_turn(self) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0)
        first = timeline.speech_started("mic-a", timestamp=10.0)
        timeline.speech_started("mic-b", timestamp=10.0)
        second_a = timeline.split_turn("mic-a", timestamp=25.0)
        second_b = timeline.split_turn("mic-b", timestamp=25.1)
        self.assertNotEqual(first, second_a)
        self.assertEqual(second_a, second_b)

    async def test_final_duplicate_uses_quality_not_raw_rms_only(
        self,
    ) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0.02)
        turn_id = timeline.speech_started("mic-a", timestamp=100.0)
        timeline.speech_started("mic-b", timestamp=100.1)
        envelope = speech_envelope(
            np.tile(
                np.concatenate(
                    [
                        np.full(1600, 0.02, dtype=np.float32),
                        np.full(1600, 0.06, dtype=np.float32),
                    ]
                ),
                10,
            )
        )
        now = time.monotonic()
        weak = FinalCandidate(
            candidate_id="weak",
            turn_id=turn_id,
            source_id="mic-a",
            raw_text="hệ thống phòng họp không giấy hôm nay",
            start_time=1.0,
            end_time=8.0,
            quality=quality(rms=0.06, score=8),
            created_at=now,
            fingerprint=envelope,
        )
        clear = FinalCandidate(
            candidate_id="clear",
            turn_id=turn_id,
            source_id="mic-b",
            raw_text="hệ thống phòng họp không giấy hôm nay",
            start_time=1.1,
            end_time=8.1,
            quality=quality(rms=0.04, score=18),
            created_at=now,
            fingerprint=envelope,
        )
        weak_result, clear_result = await asyncio.gather(
            timeline.select_final(weak),
            timeline.select_final(clear),
        )
        self.assertFalse(weak_result)
        self.assertTrue(clear_result)

    async def test_different_overlapping_speech_is_not_deduplicated(
        self,
    ) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0.02)
        turn_id = timeline.speech_started("mic-a", timestamp=100.0)
        timeline.speech_started("mic-b", timestamp=100.0)
        now = time.monotonic()
        left = FinalCandidate(
            "left",
            turn_id,
            "mic-a",
            "chúng ta thống nhất thời hạn triển khai",
            1.0,
            7.0,
            quality(rms=0.06, score=20),
            now,
            (1.0, 0.0, 0.0, 1.0),
        )
        right = FinalCandidate(
            "right",
            turn_id,
            "mic-b",
            "ngân sách của dự án cần được phê duyệt",
            1.0,
            7.0,
            quality(rms=0.06, score=20),
            now,
            (0.0, 1.0, 1.0, 0.0),
        )
        results = await asyncio.gather(
            timeline.select_final(left),
            timeline.select_final(right),
        )
        self.assertEqual(results, [True, True])

    async def test_weak_tail_with_different_asr_text_is_deduplicated(
        self,
    ) -> None:
        timeline = CoordinatedVadTimeline(final_settle_seconds=0.02)
        turn_id = timeline.speech_started("mic-a", timestamp=100.0)
        timeline.speech_started("mic-b", timestamp=100.0)
        now = time.monotonic()
        strong = FinalCandidate(
            "strong",
            turn_id,
            "mic-a",
            "người ta dùng tin học để làm được rất nhiều chuyện",
            15.0,
            22.0,
            quality(rms=0.04, score=18),
            now,
            (1.0, 0.5, 0.2, 0.1),
        )
        weak = FinalCandidate(
            "weak",
            turn_id,
            "mic-b",
            "hệ thống ứng dụng làm nhiều trường",
            15.2,
            21.8,
            quality(rms=0.008, score=5),
            now,
            (0.1, 0.2, 0.9, 0.1),
        )
        results = await asyncio.gather(
            timeline.select_final(strong),
            timeline.select_final(weak),
        )
        self.assertEqual(results, [True, False])


if __name__ == "__main__":
    unittest.main()
