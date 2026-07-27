from __future__ import annotations

import unittest

from backend.evaluation import TranscriptTruth
from scripts.streaming_regression import (
    _assign_segments,
    _coverage_score,
    _merge_segment_text,
)


class StreamingAggregationTests(unittest.TestCase):
    def test_consecutive_segments_are_joined_per_truth_interval(self) -> None:
        truth = [
            TranscriptTruth("a", "alpha beta gamma", 0, 10),
            TranscriptTruth("b", "delta epsilon zeta eta", 10, 20),
        ]
        items = [
            {
                "start_time": 100.0,
                "end_time": 109.8,
                "raw_text": "alpha beta gamma",
            },
            {
                "start_time": 110.0,
                "end_time": 115.0,
                "raw_text": "delta epsilon",
            },
            {
                "start_time": 114.8,
                "end_time": 120.0,
                "raw_text": "epsilon zeta eta",
            },
        ]

        groups, unassigned = _assign_segments(
            items, truth, base_time=100.0
        )

        self.assertEqual([len(group) for group in groups], [1, 2])
        self.assertFalse(unassigned)
        self.assertEqual(
            _merge_segment_text(groups[1]),
            "delta epsilon zeta eta",
        )
        self.assertEqual(
            _coverage_score(
                groups[1],
                expected_start=110.0,
                expected_end=120.0,
            ),
            1.0,
        )

    def test_segment_crossing_boundary_is_assigned_only_once(self) -> None:
        truth = [
            TranscriptTruth("a", "one", 0, 10),
            TranscriptTruth("b", "two", 10, 20),
        ]
        item = {
            "start_time": 109.0,
            "end_time": 114.0,
            "raw_text": "two",
        }
        groups, _ = _assign_segments([item], truth, base_time=100.0)
        self.assertEqual([len(group) for group in groups], [0, 1])


if __name__ == "__main__":
    unittest.main()
