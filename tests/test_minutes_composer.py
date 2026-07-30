from __future__ import annotations

import unittest
import asyncio
from dataclasses import replace
from unittest.mock import patch

from backend.config import settings
from backend.minutes_composer import (
    OllamaMinutesComposer,
    _expand_compact_delta,
    empty_minutes_document,
    merge_minutes_delta,
    normalize_minutes_document,
    transcript_timeline_document,
)


class MinutesDocumentTests(unittest.TestCase):
    def test_empty_document_has_stable_schema(self) -> None:
        document = empty_minutes_document("Họp triển khai", started_at=12.5)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["meeting"]["title"], "Họp triển khai")
        self.assertEqual(document["summary"], [])
        self.assertEqual(document["topics"], [])

    def test_transcript_timeline_is_evidence_only_and_sentence_cased(self) -> None:
        document = transcript_timeline_document(
            meeting_title="Họp data realtime",
            started_at=100.0,
            segments=[
                {
                    "segment_id": "seg-1",
                    "speaker": "Dat",
                    "text": "HỆ THỐNG THU THẬP DATA REALTIME",
                    "start_time": 2.0,
                },
                {
                    "segment_id": "seg-2",
                    "speaker": "Huy",
                    "text": "Chúng ta dùng DataPulse Realtime.",
                    "start_time": 3.0,
                },
            ],
        )
        topic = document["topics"][0]
        self.assertEqual(topic["title"], "Nội dung theo timeline")
        self.assertEqual(
            topic["details"][0]["content"],
            "Hệ thống thu thập data realtime.",
        )
        self.assertEqual(
            topic["details"][1]["content"],
            "Chúng ta dùng DataPulse Realtime.",
        )
        self.assertEqual(topic["proposals"], [])
        self.assertEqual(topic["decisions"], [])
        self.assertEqual(topic["actions"], [])
        self.assertEqual(document["source_segment_ids"], ["seg-1", "seg-2"])

    def test_filters_unproven_items_and_keeps_evidence(self) -> None:
        document = normalize_minutes_document(
            {
                "meeting": {"title": "Tên do model tự đổi", "started_at": 0},
                "summary": [
                    {
                        "content": "Thống nhất rà soát kế hoạch triển khai.",
                        "source_segment_ids": ["seg-1"],
                    },
                    {
                        "content": "Một nội dung không có bằng chứng.",
                        "source_segment_ids": ["made-up"],
                    },
                ],
                "topics": [
                    {
                        "title": "Kế hoạch triển khai",
                        "details": [
                            {
                                "speaker": "Anh A",
                                "content": "Cần rà soát mốc triển khai.",
                                "source_segment_ids": ["seg-1"],
                            }
                        ],
                        "proposals": [],
                        "decisions": [],
                        "actions": [
                            {
                                "task": "Rà soát kế hoạch",
                                "owner": "Anh A",
                                "deadline": "Thứ Sáu",
                                "source_segment_ids": ["seg-2"],
                            }
                        ],
                        "source_segment_ids": ["seg-1", "seg-2"],
                    }
                ],
            },
            meeting_title="Họp triển khai AI",
            valid_source_ids=["seg-1", "seg-2"],
            started_at=100.0,
        )
        self.assertEqual(document["meeting"]["title"], "Họp triển khai AI")
        self.assertEqual(document["meeting"]["started_at"], 100.0)
        self.assertEqual(len(document["summary"]), 1)
        self.assertEqual(len(document["topics"]), 1)
        self.assertEqual(
            document["topics"][0]["actions"][0]["owner"], "Anh A"
        )
        self.assertEqual(document["source_segment_ids"], ["seg-1", "seg-2"])

    def test_incremental_delta_preserves_old_content_and_marks_source_processed(self) -> None:
        existing = normalize_minutes_document(
            {
                "summary": [
                    {
                        "content": "Đã thống nhất chủ trương triển khai.",
                        "source_segment_ids": ["seg-old"],
                    }
                ],
                "topics": [],
            },
            meeting_title="Họp demo",
            valid_source_ids=["seg-old"],
            started_at=1,
        )
        document = merge_minutes_delta(
            existing,
            {
                "topics": [
                    {
                        "title": "Kế hoạch triển khai",
                        "actions": [
                            {
                                "task": "Hoàn thành bản demo",
                                "owner": "Anh Nam",
                                "deadline": "Thứ Sáu",
                                "source_segment_ids": ["seg-new"],
                            }
                        ],
                    }
                ]
            },
            meeting_title="Họp demo",
            new_source_ids=["seg-new"],
            started_at=1,
        )
        self.assertEqual(len(document["summary"]), 1)
        self.assertEqual(document["topics"][0]["actions"][0]["owner"], "Anh Nam")
        self.assertEqual(document["source_segment_ids"], ["seg-old", "seg-new"])

    def test_compact_delta_expands_only_known_evidence_indexes(self) -> None:
        expanded = _expand_compact_delta(
            {
                "s": [{"c": "Thống nhất thí điểm.", "e": [0, 99]}],
                "n": "Thí điểm",
                "a": [
                    {
                        "c": "Lập kế hoạch",
                        "o": "Anh Minh",
                        "l": "15 tháng 8",
                        "e": [1],
                    }
                ],
            },
            [
                {"segment_id": "seg-a"},
                {"segment_id": "seg-b"},
            ],
        )
        self.assertEqual(
            expanded["summary"][0]["source_segment_ids"], ["seg-a"]
        )
        self.assertEqual(
            expanded["topics"][0]["actions"][0]["source_segment_ids"],
            ["seg-b"],
        )

    def test_fact_delta_classifies_actions_and_corrects_obvious_source_drift(self) -> None:
        expanded = _expand_compact_delta(
            {
                "n": "Thí điểm",
                "facts": [
                    {
                        "k": "Q",
                        "c": "Thống nhất chọn phương án thí điểm",
                        "e": [0],
                    },
                    {
                        "k": "A",
                        "c": "Chuẩn bị danh sách tham gia",
                        "o": "Chị Hoa",
                        "l": "10 tháng 8",
                        # A small model accidentally reused the preceding
                        # index; lexical grounding can prove this is seg-b.
                        "e": [0],
                    },
                ],
            },
            [
                {
                    "segment_id": "seg-a",
                    "speaker": "Chị Lan",
                    "text": "Thống nhất chọn phương án thí điểm.",
                },
                {
                    "segment_id": "seg-b",
                    "speaker": "Chị Hoa",
                    "text": "Tôi sẽ chuẩn bị danh sách tham gia trước ngày 10 tháng 8.",
                },
            ],
        )
        topic = expanded["topics"][0]
        self.assertEqual(len(topic["decisions"]), 1)
        self.assertEqual(topic["actions"][0]["owner"], "Chị Hoa")
        self.assertEqual(
            topic["actions"][0]["source_segment_ids"], ["seg-b"]
        )

    def test_fact_delta_repairs_misclassified_explicit_assignment(self) -> None:
        expanded = _expand_compact_delta(
            {
                "n": "Thí điểm",
                "facts": [
                    {
                        "k": "P",
                        "c": "Anh Minh phụ trách lập kế hoạch",
                        "e": [0],
                    }
                ],
            },
            [
                {
                    "segment_id": "seg-action",
                    "speaker": "Chị Lan",
                    "text": (
                        "Anh Minh phụ trách lập kế hoạch chi tiết và gửi "
                        "trước ngày 15 tháng 8."
                    ),
                }
            ],
        )
        topic = expanded["topics"][0]
        self.assertEqual(topic["proposals"], [])
        self.assertEqual(topic["actions"][0]["owner"], "Anh Minh")
        self.assertEqual(topic["actions"][0]["deadline"], "trước ngày 15 tháng 8")

    def test_fact_delta_recovers_a_missing_citation_only_when_grounded(self) -> None:
        expanded = _expand_compact_delta(
            {
                "n": "Thí điểm",
                "facts": [
                    {
                        "k": "Q",
                        "c": "Thống nhất thí điểm cổng trợ lý AI",
                    }
                ],
            },
            [
                {
                    "segment_id": "seg-grounded",
                    "speaker": "Chị Lan",
                    "text": "Cuộc họp thống nhất thí điểm cổng trợ lý AI.",
                },
                {
                    "segment_id": "seg-other",
                    "speaker": "Anh Minh",
                    "text": "Hệ thống cần thêm báo cáo tiến độ.",
                },
            ],
        )
        self.assertEqual(
            expanded["topics"][0]["decisions"][0]["source_segment_ids"],
            ["seg-grounded"],
        )

    def test_explicit_assignment_replaces_duplicate_model_actions(self) -> None:
        expanded = _expand_compact_delta(
            {
                "n": "Kế hoạch",
                "facts": [
                    {
                        "k": "A",
                        "c": "Lập kế hoạch chi tiết",
                        "o": "Người không có trong câu",
                        "l": "Ngày không có trong câu",
                        "e": [0],
                    },
                    {
                        "k": "A",
                        "c": "Gửi kế hoạch",
                        "e": [0],
                    },
                ],
            },
            [
                {
                    "segment_id": "seg-assigned",
                    "speaker": "Chị Lan",
                    "text": "Anh Minh phụ trách lập kế hoạch chi tiết trước ngày 15 tháng 8.",
                }
            ],
        )
        actions = expanded["topics"][0]["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["owner"], "Anh Minh")
        self.assertEqual(actions[0]["deadline"], "trước ngày 15 tháng 8")

    def test_merge_keeps_processed_sources_without_visible_bullets(self) -> None:
        document = merge_minutes_delta(
            {
                **empty_minutes_document("Họp demo", started_at=1),
                "source_segment_ids": ["seg-old"],
            },
            {},
            meeting_title="Họp demo",
            new_source_ids=["seg-new"],
            started_at=1,
        )
        self.assertEqual(
            document["source_segment_ids"], ["seg-old", "seg-new"]
        )

    def test_ollama_request_disables_thinking_and_returns_valid_document(self) -> None:
        response_document = {
            "meeting": {"title": "ignored by backend", "started_at": 0},
            "summary": [
                {
                    "content": "Thống nhất hoàn thành bản demo.",
                    "source_segment_ids": ["seg-1"],
                }
            ],
            "topics": [],
        }
        captured: dict[str, object] = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"message": {"content": __import__("json").dumps(response_document)}}

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                captured["timeout"] = kwargs.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                captured["url"] = url
                captured["request"] = kwargs["json"]
                return FakeResponse()

        runtime = replace(
            settings,
            minutes_composer_model="qwen2.5:3b",
            minutes_composer_mode="llm",
        )
        with patch("backend.minutes_composer.httpx.AsyncClient", FakeClient):
            document, metadata = asyncio.run(
                OllamaMinutesComposer(runtime).compose(
                    meeting_title="Họp demo",
                    existing_document=None,
                    segments=[
                        {
                            "segment_id": "seg-1",
                            "speaker": "Anh A",
                            "start_time": 1,
                            "end_time": 4,
                            "text": "Thống nhất hoàn thành bản demo.",
                        }
                    ],
                    started_at=1,
                )
            )
        request = captured["request"]
        self.assertIsInstance(request, dict)
        self.assertFalse(request["think"])
        self.assertEqual(request["model"], "qwen2.5:3b")
        self.assertEqual(document["meeting"]["title"], "Họp demo")
        self.assertEqual(document["summary"][0]["source_segment_ids"], ["seg-1"])
        self.assertFalse(metadata["think"])


if __name__ == "__main__":
    unittest.main()
