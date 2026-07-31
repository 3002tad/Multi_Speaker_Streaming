from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.topic_discovery import TopicDiscoveryWindow


class TopicDiscoveryWindowTests(unittest.TestCase):
    def make_window(self, state_path: Path) -> TopicDiscoveryWindow:
        return TopicDiscoveryWindow(
            state_path=state_path,
            bootstrap_seconds=10,
            refresh_seconds=5,
            minimum_turns=2,
            minimum_evidence_turns=2,
            minimum_topic_confidence=0.65,
            minimum_term_confidence=0.88,
            term_ttl_hours=1,
            maximum_terms=10,
        )

    def test_bootstrap_requires_time_and_independent_turns(self) -> None:
        with TemporaryDirectory() as temporary:
            window = self.make_window(Path(temporary) / "topic.json")
            window.reset(started_at=100, participant_names=("Anh Nam",))

            self.assertFalse(
                window.observe(
                    turn_id="t1",
                    raw_text="h d f s lưu dữ liệu",
                    speaker="Anh Nam",
                    timestamp=105,
                )
            )
            self.assertTrue(
                window.observe(
                    turn_id="t2",
                    raw_text="hát đê ép ét là hệ thống tập tin",
                    speaker="Chị Hoa",
                    timestamp=111,
                )
            )

    def test_accepts_only_terms_with_literal_raw_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            window = self.make_window(Path(temporary) / "topic.json")
            window.reset(started_at=0)
            window.observe(
                turn_id="t1",
                raw_text="h d f s lưu dữ liệu phân tán",
                speaker="A",
                timestamp=11,
            )
            window.observe(
                turn_id="t2",
                raw_text="hát đê ép ét là hệ thống tập tin",
                speaker="B",
                timestamp=12,
            )
            snapshot = window.accept_model_response(
                {
                    "topic": "Lưu trữ dữ liệu phân tán",
                    "topic_confidence": 0.9,
                    "terms": [
                        {
                            "canonical": "HDFS",
                            "observed_variants": [
                                "h d f s",
                                "hát đê ép ét",
                            ],
                            "evidence_turn_ids": ["t1", "t2"],
                            "confidence": 0.96,
                        },
                        {
                            "canonical": "HBase",
                            "observed_variants": ["h base"],
                            "evidence_turn_ids": ["t1", "t2"],
                            "confidence": 0.99,
                        },
                    ],
                },
                now=datetime(2026, 1, 1, tzinfo=UTC),
            )

            self.assertEqual(snapshot.topic, "Lưu trữ dữ liệu phân tán")
            self.assertEqual(tuple(entry.canonical for entry in snapshot.entries), ("HDFS",))
            self.assertEqual(snapshot.entries[0].source, "topic_discovery")
            self.assertEqual(snapshot.entries[0].confidence, 0.92)

    def test_same_global_turn_does_not_count_twice(self) -> None:
        with TemporaryDirectory() as temporary:
            window = self.make_window(Path(temporary) / "topic.json")
            window.reset(started_at=0)
            window.observe(
                turn_id="turn-1",
                raw_text="bản từ mic yếu",
                speaker="A",
                timestamp=11,
            )
            ready = window.observe(
                turn_id="turn-1",
                raw_text="bản thay thế từ mic mạnh",
                speaker="A",
                timestamp=12,
            )

            self.assertFalse(ready)
            self.assertEqual(window.status()["turn_count"], 1)


if __name__ == "__main__":
    unittest.main()
