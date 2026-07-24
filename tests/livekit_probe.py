"""Join the configured room once without publishing media."""

from __future__ import annotations

import asyncio

import httpx
from livekit import rtc


async def main() -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://meet.simplething.id.vn/api/meeting/create",
            json={"host_name": "Connectivity Probe"},
        )
        response.raise_for_status()
        credentials = response.json()

    room = rtc.Room()
    try:
        await room.connect(
            credentials["livekit_url"],
            credentials["token"],
        )
        print(
            "LIVEKIT_PROBE_OK",
            room.name,
            room.local_participant.identity,
        )
    finally:
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
