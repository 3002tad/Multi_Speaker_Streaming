"""Deterministic fixture checks for P0-08 global-turn arbitration."""

from __future__ import annotations

import unittest

import numpy as np

from scripts.concurrent_streaming_regression import _load_cases
from tests.livekit_dual_mic_probe import (
    build_sequential_cross_mic_audio,
)


class OverlapFixtureTests(unittest.TestCase):
    def test_crosstalk_fixture_reuses_each_speaker_on_both_mics(self) -> None:
        first = np.full(16_000, 0.10, dtype=np.float32)
        second = np.full(16_000, -0.08, dtype=np.float32)
        mic_a, mic_b = build_sequential_cross_mic_audio(
            first,
            second,
            cross_mic_gain=0.20,
            turn_seconds=1.0,
            inter_turn_silence_seconds=0.25,
            trailing_silence_seconds=0.0,
        )
        second_start = 16_000 + 4_000
        self.assertTrue(np.allclose(mic_b[:16_000], first * 0.20))
        self.assertTrue(
            np.allclose(
                mic_a[second_start : second_start + 16_000], second * 0.20
            )
        )
        self.assertTrue(np.allclose(mic_a[:16_000], first))
        self.assertTrue(
            np.allclose(
                mic_b[second_start : second_start + 16_000], second
            )
        )

    def test_true_overlap_fixture_uses_distinct_recordings(self) -> None:
        cases = _load_cases(2)
        self.assertNotEqual(cases[0]["voice"], cases[1]["voice"])
        self.assertFalse(
            np.array_equal(cases[0]["audio"], cases[1]["audio"])
        )
        self.assertGreater(len(cases[0]["audio"]), 16_000)
        self.assertGreater(len(cases[1]["audio"]), 16_000)


if __name__ == "__main__":
    unittest.main()
