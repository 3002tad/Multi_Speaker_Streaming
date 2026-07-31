from __future__ import annotations

import unittest

import numpy as np

from tests.livekit_dual_mic_probe import (
    SAMPLE_RATE,
    build_sequential_cross_mic_audio,
)


class SequentialCrossMicFixtureTests(unittest.TestCase):
    def test_each_turn_has_one_primary_microphone(self) -> None:
        turn = np.full(SAMPLE_RATE, 0.5, dtype=np.float32)
        mic_a, mic_b = build_sequential_cross_mic_audio(
            turn,
            -turn,
            cross_mic_gain=0.2,
            turn_seconds=1.0,
            inter_turn_silence_seconds=0.25,
            trailing_silence_seconds=0.25,
        )

        self.assertEqual(len(mic_a), int(2.5 * SAMPLE_RATE))
        self.assertEqual(len(mic_b), len(mic_a))
        self.assertTrue(np.allclose(mic_a[:SAMPLE_RATE], 0.5))
        self.assertTrue(np.allclose(mic_b[:SAMPLE_RATE], 0.1))
        self.assertTrue(
            np.allclose(
                mic_a[int(1.25 * SAMPLE_RATE) : int(2.25 * SAMPLE_RATE)],
                -0.1,
            )
        )
        self.assertTrue(
            np.allclose(
                mic_b[int(1.25 * SAMPLE_RATE) : int(2.25 * SAMPLE_RATE)],
                -0.5,
            )
        )
        self.assertFalse(mic_a[SAMPLE_RATE : int(1.25 * SAMPLE_RATE)].any())
        self.assertFalse(mic_b[SAMPLE_RATE : int(1.25 * SAMPLE_RATE)].any())
        self.assertFalse(mic_a[int(2.25 * SAMPLE_RATE) :].any())
        self.assertFalse(mic_b[int(2.25 * SAMPLE_RATE) :].any())


if __name__ == "__main__":
    unittest.main()
