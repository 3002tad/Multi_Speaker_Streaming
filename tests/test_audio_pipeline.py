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
    StreamingDpdfNetEnhancer,
    StreamingAsrPreprocessor,
    pack_audio_packet,
    select_speaker_windows,
    speech_envelope,
    summarize_quality,
    unpack_audio_packet,
)


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
