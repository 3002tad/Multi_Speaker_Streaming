from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from meeting_ai.config import settings as ai_settings
from meeting_ai.core.operations import OperationTracker, cancel_and_wait, validate_secret
from meeting_service.app.config import settings as meeting_settings
from meeting_service.app.infrastructure.readiness import collect_readiness


class OperationConfigTests(unittest.TestCase):
    def test_strict_ai_config_rejects_placeholder_key(self) -> None:
        strict = replace(
            ai_settings,
            strict_config=True,
            internal_api_key="replace_me",
            meeting_service_key="replace_me",
        )
        with self.assertRaisesRegex(RuntimeError, "INTERNAL_API_KEY"):
            strict.validate_ai_api_startup()

    def test_strict_meeting_config_rejects_placeholder_runtime_token(self) -> None:
        strict = replace(
            meeting_settings,
            strict_config=True,
            service_key="a" * 32,
            runtime_token_secret="change-me-runtime-token-secret-32bytes",
            redis_url="redis://localhost:6379/0",
            livekit_url="wss://example.test",
            livekit_api_key="key",
            livekit_api_secret="b" * 32,
        )
        with self.assertRaisesRegex(RuntimeError, "MEETING_RUNTIME_TOKEN_SECRET"):
            strict.validate_startup()

    def test_validate_secret_accepts_random_value(self) -> None:
        validate_secret("TEST_SECRET", "s3cure-" + "x" * 32)

    def test_operation_tracker_reports_degraded_warmup(self) -> None:
        tracker = OperationTracker()
        tracker.start_warmup()
        tracker.finish_warmup(status="degraded", elapsed_ms=11, error="ConnectError")
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["ollama_warmup"]["status"], "degraded")
        self.assertEqual(snapshot["ollama_warmup"]["error"], "ConnectError")


class ReadinessTests(unittest.TestCase):
    def test_nonpersistent_ai_disabled_is_ready(self) -> None:
        report = collect_readiness(
            persistence_enabled=False,
            session_factory=None,
            redis_url="",
            object_storage=object(),
            ai_enabled=False,
            ai_base_url="http://unused",
            service_key="unused",
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["components"]["database"]["status"], "skipped")

    def test_invalid_redis_marks_readiness_degraded(self) -> None:
        report = collect_readiness(
            persistence_enabled=True,
            session_factory=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
            redis_url="redis://127.0.0.1:1/0",
            object_storage=object(),
            ai_enabled=False,
            ai_base_url="http://unused",
            service_key="unused",
            timeout_seconds=0.01,
        )
        self.assertEqual(report["status"], "degraded")
        self.assertIn("database", report["failed_components"])
        self.assertIn("redis", report["failed_components"])


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_and_wait_finishes_completed_work(self) -> None:
        completed = asyncio.create_task(asyncio.sleep(0))
        pending = await cancel_and_wait({completed}, timeout_seconds=0.1)
        self.assertEqual(pending, 0)

