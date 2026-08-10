"""Exercise one dynamic Meeting Service -> AI -> LiveKit audio session.

This probe intentionally uses a fresh external meeting UUID and never touches
the eCabinet database. It is the smallest repeatable check for the streaming
vertical slice while the browser UI remains focused on the same contract.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from livekit import rtc


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320
ROOT = Path(__file__).resolve().parents[1]


async def publish_audio(
    *,
    livekit_url: str,
    token: str,
    audio: np.ndarray,
) -> None:
    room = rtc.Room()
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("runtime-probe-mic", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    try:
        await room.connect(livekit_url, token)
        await room.local_participant.publish_track(track, options)
        # Give the dynamic Agent a short window to subscribe before speech.
        await asyncio.sleep(5.0)
        for start in range(0, len(audio), FRAME_SAMPLES):
            samples = audio[start : start + FRAME_SAMPLES]
            if len(samples) < FRAME_SAMPLES:
                samples = np.pad(samples, (0, FRAME_SAMPLES - len(samples)))
            pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
            await source.capture_frame(
                rtc.AudioFrame(
                    data=pcm.tobytes(),
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=FRAME_SAMPLES,
                )
            )
            await asyncio.sleep(FRAME_SAMPLES / SAMPLE_RATE)
        await asyncio.sleep(15.0)
    finally:
        await room.disconnect()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audio",
        default=str(ROOT / "audio" / "thayDung_noi.wav"),
        help="16 kHz WAV fixture to publish",
    )
    parser.add_argument("--seconds", type=float, default=12.0)
    args = parser.parse_args()

    audio, sample_rate = sf.read(args.audio, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"probe expects 16 kHz WAV, got {sample_rate}")
    audio = np.asarray(audio[: int(args.seconds * SAMPLE_RATE)], dtype=np.float32)
    audio = np.concatenate([audio, np.zeros(SAMPLE_RATE, dtype=np.float32)])

    meeting_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    service_url = "http://127.0.0.1:8002"
    service_headers = {"X-Service-Key": os.getenv("MEETING_SERVICE_KEY", "local-meeting-service-key")}
    async with httpx.AsyncClient(timeout=30.0) as client:
        runtime = await client.post(
            f"{service_url}/internal/v1/meetings/{meeting_id}/runtime",
            json={
                "meeting": {"status": "APPROVED", "title": "Runtime probe"},
                "participants": (
                    []
                    if os.getenv("RUNTIME_PROBE_NO_PARTICIPANT") == "1"
                    else [{"user_id": user_id, "display_name": "Audio Probe"}]
                ),
            },
            headers={**service_headers, "Idempotency-Key": f"probe-start-{meeting_id}"},
        )
        runtime.raise_for_status()
        runtime_payload = runtime.json()
        runtime_id = runtime_payload["runtime_session_id"]
        deadline = time.monotonic() + 30.0
        while runtime_payload.get("status") not in {"READY", "RECORDING"}:
            if time.monotonic() > deadline:
                raise RuntimeError(f"runtime did not become ready: {runtime_payload}")
            await asyncio.sleep(0.5)
            status = await client.get(
                f"{service_url}/internal/v1/meetings/{meeting_id}/status"
            )
            status.raise_for_status()
            runtime_payload = status.json()

        token_response = await client.post(
            f"{service_url}/internal/v1/meetings/{meeting_id}/tokens",
            json={
                "runtime_session_id": runtime_id,
                "user_id": user_id,
                "device_id": "runtime-probe",
            },
            headers=service_headers,
        )
        token_response.raise_for_status()
        credentials = token_response.json()

        await publish_audio(
            livekit_url=credentials["livekit_url"],
            token=credentials["token"],
            audio=audio,
        )

        stopped = await client.post(
            f"{service_url}/internal/v1/runtimes/{runtime_id}/stop",
            headers={**service_headers, "Idempotency-Key": f"probe-stop-{runtime_id}"},
        )
        stopped.raise_for_status()
        await asyncio.sleep(1.0)
        transcript = await client.get(
            f"{service_url}/internal/v1/meetings/{meeting_id}/transcript",
            headers=service_headers,
        )
        transcript.raise_for_status()

    segments = transcript.json().get("segments", [])
    print("RUNTIME_PROBE_MEETING_ID", meeting_id)
    print("RUNTIME_PROBE_STATUS", stopped.json().get("status"))
    print("RUNTIME_PROBE_SEGMENTS", len(segments))
    for segment in segments:
        print("RUNTIME_PROBE_TEXT", segment.get("text") or segment.get("content_text"))


if __name__ == "__main__":
    asyncio.run(main())
