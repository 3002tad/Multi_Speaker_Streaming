from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

from meeting_ai.application.minutes_worker import MinutesWorker, MinutesWorkerError
from meeting_ai.config import settings


class MinutesWorkerTests(unittest.TestCase):
    def _evidence(self) -> dict:
        return {
            "schema_version": 1,
            "analysis_id": "11111111-1111-4111-8111-111111111111",
            "meeting_id": "22222222-2222-4222-8222-222222222222",
            "runtime_session_id": "33333333-3333-4333-8333-333333333333",
            "generation_id": "44444444-4444-4444-8444-444444444444",
            "base_transcript_revision": 17,
            "meeting": {
                "title": "Họp triển khai",
                "started_at": "2026-08-12T01:00:00+00:00",
            },
            "segments": [
                {
                    "segment_id": "seg-1",
                    "speaker_label": "Dat",
                    "speaker_user_id": None,
                    "content_text": "Thống nhất kế hoạch triển khai.",
                    "started_at": "2026-08-12T01:00:01+00:00",
                    "ended_at": "2026-08-12T01:00:03+00:00",
                }
            ],
            "previous_document": None,
        }

    def test_run_composes_and_publishes_minutes_updated(self) -> None:
        captured: dict[str, object] = {}

        class FakeComposer:
            def __init__(self, _settings) -> None:
                pass

            async def compose(self, **kwargs):
                captured["compose"] = kwargs
                return (
                    {
                        "schema_version": 1,
                        "meeting": {
                            "title": "Họp triển khai",
                            "started_at": "2026-08-12T01:00:00+00:00",
                        },
                        "summary": [
                            {
                                "content": "Thống nhất kế hoạch triển khai.",
                                "source_segment_ids": ["seg-1"],
                            }
                        ],
                        "topics": [],
                        "source_segment_ids": ["seg-1"],
                    },
                    {"model": "qwen2.5:3b", "mode": "incremental_delta"},
                )

        class FakeResponse:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"status": "accepted"}

        class FakeClient:
            def __init__(self, **kwargs) -> None:
                captured["timeout"] = kwargs["timeout"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args) -> None:
                return None

            async def post(self, url: str, **kwargs):
                captured["url"] = url
                captured["event"] = kwargs["json"]
                captured["headers"] = kwargs["headers"]
                return FakeResponse()

        runtime = replace(
            settings,
            minutes_composer_enabled=True,
            minutes_composer_mode="llm",
            meeting_service_key="test-meeting-key",
        )
        with patch(
            "meeting_ai.application.minutes_worker.OllamaMinutesComposer",
            FakeComposer,
        ):
            event = asyncio.run(
                MinutesWorker(runtime, http_client_factory=FakeClient).run(
                    self._evidence(),
                    callback_url="http://meeting-service/internal/v1/ai-events",
                    sequence=123,
                )
            )
        self.assertEqual(event["type"], "minutes.updated")
        self.assertEqual(event["sequence"], 123)
        self.assertEqual(event["payload"]["analysis_id"], self._evidence()["analysis_id"])
        self.assertEqual(captured["headers"]["X-Service-Key"], "test-meeting-key")
        self.assertEqual(captured["compose"]["segments"][0]["text"], "Thống nhất kế hoạch triển khai.")

    def test_timeline_mode_cannot_silently_skip_qwen_in_p1_02(self) -> None:
        runtime = replace(settings, minutes_composer_enabled=True, minutes_composer_mode="timeline")
        with self.assertRaises(MinutesWorkerError):
            asyncio.run(MinutesWorker(runtime).compose(self._evidence()))


if __name__ == "__main__":
    unittest.main()
