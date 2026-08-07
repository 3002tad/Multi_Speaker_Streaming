from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import jwt

from meeting_service.app.infrastructure.token_verifier import RuntimeTokenVerifier
from meeting_service.app.domain.permissions import claims_can_join, claims_match_runtime


class RuntimeTokenVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = RuntimeTokenVerifier()
        self.key = "change-me-runtime-token-secret-32bytes"

    def _token(self, **overrides: object) -> str:
        claims = {
            "sub": "user-1",
            "meeting_id": "meeting-1",
            "runtime_session_id": "runtime-1",
            "iss": "ecabinet",
            "aud": "meeting-service",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="HS256")

    def test_accepts_signed_token(self) -> None:
        claims = self.verifier.verify(self._token())
        self.assertEqual(claims["meeting_id"], "meeting-1")

    def test_rejects_wrong_audience(self) -> None:
        with self.assertRaises(ValueError):
            self.verifier.verify(self._token(aud="other-service"))

    def test_rejects_expired_token(self) -> None:
        with self.assertRaises(ValueError):
            self.verifier.verify(self._token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)))

    def test_keeps_internal_claim_adapter_for_unit_callers(self) -> None:
        claims = self.verifier.verify({"sub": "user-1", "meeting_id": "meeting-1"})
        self.assertEqual(claims["sub"], "user-1")

    def test_join_claim_requires_permission_and_runtime_binding(self) -> None:
        claims = {"sub": "user-1", "meeting_id": "meeting-1", "runtime_session_id": "runtime-1", "permissions": ["JOIN", "VIEW"]}
        self.assertTrue(claims_can_join(claims, "meeting-1"))
        self.assertTrue(claims_match_runtime(claims, "runtime-1"))
        self.assertFalse(claims_match_runtime(claims, "runtime-2"))
        self.assertFalse(claims_can_join({**claims, "permissions": ["VIEW"]}, "meeting-1"))


if __name__ == "__main__":
    unittest.main()
