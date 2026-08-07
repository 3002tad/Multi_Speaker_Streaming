from __future__ import annotations

import unittest
from datetime import datetime, timezone

import jwt

from meeting_service.app.config import settings
from meeting_service.app.infrastructure.livekit_tokens import LiveKitConfigurationError, issue_livekit_token


class LiveKitTokenServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = (settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)

    def tearDown(self) -> None:
        for name, value in zip(("livekit_url", "livekit_api_key", "livekit_api_secret"), self.original):
            object.__setattr__(settings, name, value)

    def test_requires_server_credentials(self) -> None:
        with self.assertRaises(LiveKitConfigurationError):
            issue_livekit_token(room="meeting-demo", identity="ecabinet-user", name="User")

    def test_issues_room_join_publish_subscribe_claims(self) -> None:
        object.__setattr__(settings, "livekit_url", "wss://livekit.example.test")
        object.__setattr__(settings, "livekit_api_key", "api-key")
        secret = "api-secret-0123456789-0123456789-012345"
        object.__setattr__(settings, "livekit_api_secret", secret)
        result = issue_livekit_token(room="meeting-demo", identity="ecabinet-user", name="User")
        claims = jwt.decode(result["token"], secret, algorithms=["HS256"], options={"verify_exp": False})
        self.assertEqual(result["livekit_url"], "wss://livekit.example.test")
        self.assertEqual(claims["sub"], "ecabinet-user")
        self.assertTrue(claims["video"]["roomJoin"])
        self.assertTrue(claims["video"]["canPublish"])
        self.assertTrue(claims["video"]["canSubscribe"])
        self.assertGreater(claims["exp"], int(datetime.now(timezone.utc).timestamp()))


if __name__ == "__main__":
    unittest.main()
