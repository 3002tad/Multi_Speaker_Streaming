from __future__ import annotations

from typing import Any

import httpx


class MeetingAIClient:
    """Internal contract client; calls AI only through its versioned API."""

    def __init__(self, base_url: str, service_key: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Internal-Api-Key": service_key}
        self.timeout = timeout

    async def create_session(self, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as client:
            response = await client.post("/internal/v1/sessions", json=payload, headers={"Idempotency-Key": idempotency_key})
            response.raise_for_status()
            return response.json()

    async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as client:
            response = await client.post(f"/internal/v1/sessions/{runtime_session_id}/stop", headers={"Idempotency-Key": idempotency_key})
            response.raise_for_status()
            return response.json()

    async def create_enrollment(
        self,
        user_id: str,
        display_name: str,
        audio: bytes,
        filename: str = "enrollment.wav",
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=90.0) as client:
            response = await client.post(
                f"/internal/v1/enrollments/{user_id}",
                data={"display_name": display_name},
                files={"audio": (filename, audio, "audio/wav")},
            )
            response.raise_for_status()
            return response.json()

    async def get_enrollment(self, user_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as client:
            response = await client.get(f"/internal/v1/enrollments/{user_id}")
            response.raise_for_status()
            return response.json()

    async def delete_enrollment(self, user_id: str) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, headers=self.headers, timeout=self.timeout) as client:
            response = await client.delete(f"/internal/v1/enrollments/{user_id}")
            response.raise_for_status()
