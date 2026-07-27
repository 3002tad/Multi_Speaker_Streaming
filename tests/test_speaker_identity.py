from __future__ import annotations

import unittest

import numpy as np

from backend.speaker_identity import (
    adaptive_absolute_threshold,
    build_enrollment_profile,
    can_early_accept_speaker,
    decide_open_set_speaker,
)


class EnrollmentProfileTests(unittest.TestCase):
    def test_profile_rejects_too_few_windows(self) -> None:
        with self.assertRaises(ValueError):
            build_enrollment_profile(
                [np.array([1.0, 0.0]), np.array([0.99, 0.01])]
            )

    def test_profile_discards_a_clear_outlier(self) -> None:
        embeddings = [
            np.array([1.0, 0.02, 0.0]),
            np.array([0.99, -0.01, 0.01]),
            np.array([0.98, 0.03, -0.01]),
            np.array([1.0, -0.02, 0.0]),
            np.array([0.0, 1.0, 0.0]),
        ]

        profile = build_enrollment_profile(embeddings)

        self.assertEqual(profile.total_embeddings, 5)
        self.assertEqual(profile.retained_embeddings, 4)
        self.assertGreater(profile.centroid[0], 0.99)
        self.assertGreaterEqual(len(profile.prototypes), 2)


class OpenSetDecisionTests(unittest.TestCase):
    def test_early_accept_requires_extra_score_and_margin(self) -> None:
        strong = decide_open_set_speaker(
            [{"Dung": 0.96, "Phuoc": 0.88}],
            absolute_threshold=0.88,
            margin_threshold=0.035,
            consensus_threshold=0.67,
        )
        borderline = decide_open_set_speaker(
            [{"Dung": 0.92, "Phuoc": 0.88}],
            absolute_threshold=0.88,
            margin_threshold=0.035,
            consensus_threshold=0.67,
        )
        self.assertTrue(
            can_early_accept_speaker(
                strong,
                score_buffer=0.025,
                margin_threshold=0.035,
                margin_buffer=0.015,
            )
        )
        self.assertFalse(
            can_early_accept_speaker(
                borderline,
                score_buffer=0.025,
                margin_threshold=0.035,
                margin_buffer=0.015,
            )
        )

    def test_adaptive_gate_raises_for_similar_enrolled_voices(self) -> None:
        threshold = adaptive_absolute_threshold(
            base_floor=0.86,
            single_profile_threshold=0.90,
            profile_count=3,
            max_profile_similarity=0.91,
            margin_threshold=0.035,
        )
        self.assertAlmostEqual(threshold, 0.945)

    def test_adaptive_gate_keeps_floor_for_dissimilar_cohort(self) -> None:
        threshold = adaptive_absolute_threshold(
            base_floor=0.86,
            single_profile_threshold=0.90,
            profile_count=3,
            max_profile_similarity=0.70,
            margin_threshold=0.035,
        )
        self.assertAlmostEqual(threshold, 0.864)

    def test_accepts_stable_multi_window_match(self) -> None:
        decision = decide_open_set_speaker(
            [
                {"An": 0.89, "Binh": 0.73},
                {"An": 0.87, "Binh": 0.72},
                {"An": 0.90, "Binh": 0.75},
            ],
            absolute_threshold=0.82,
            margin_threshold=0.035,
            consensus_threshold=0.67,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.label, "An")

    def test_rejects_ambiguous_top_two(self) -> None:
        decision = decide_open_set_speaker(
            [
                {"An": 0.87, "Binh": 0.85},
                {"An": 0.86, "Binh": 0.84},
                {"An": 0.88, "Binh": 0.85},
            ],
            absolute_threshold=0.82,
            margin_threshold=0.035,
            consensus_threshold=0.67,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "ambiguous_top_two")

    def test_rejects_inconsistent_windows(self) -> None:
        decision = decide_open_set_speaker(
            [
                {"An": 0.90, "Binh": 0.72},
                {"An": 0.84, "Binh": 0.91},
                {"An": 0.89, "Binh": 0.75},
                {"An": 0.83, "Binh": 0.92},
            ],
            absolute_threshold=0.82,
            margin_threshold=0.01,
            consensus_threshold=0.67,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "inconsistent_windows")

    def test_single_known_profile_requires_stronger_score(self) -> None:
        decision = decide_open_set_speaker(
            [{"An": 0.83}, {"An": 0.84}, {"An": 0.83}],
            absolute_threshold=0.82,
            margin_threshold=0.035,
            consensus_threshold=0.67,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.required_score, 0.84)

    def test_unknown_does_not_take_single_nearest_profile(self) -> None:
        decision = decide_open_set_speaker(
            [{"Dat": 0.843}, {"Dat": 0.841}, {"Dat": 0.844}],
            absolute_threshold=0.82,
            margin_threshold=0.035,
            consensus_threshold=0.67,
            single_profile_threshold=0.90,
        )

        self.assertFalse(decision.accepted)
        self.assertIsNone(decision.label)
        self.assertEqual(decision.reason, "score_below_threshold")
        self.assertEqual(decision.required_score, 0.90)

    def test_stable_single_profile_match_can_still_be_accepted(self) -> None:
        decision = decide_open_set_speaker(
            [{"Dung": 0.93}, {"Dung": 0.92}, {"Dung": 0.94}],
            absolute_threshold=0.82,
            margin_threshold=0.035,
            consensus_threshold=0.67,
            single_profile_threshold=0.90,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.label, "Dung")


if __name__ == "__main__":
    unittest.main()
