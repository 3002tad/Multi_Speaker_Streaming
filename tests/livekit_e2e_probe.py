"""Run one fixture through the actual local Meeting Service/LiveKit/AI path."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from livekit import rtc


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320


def _service_key() -> str:
    key = os.getenv("MEETING_SERVICE_KEY", "").strip()
    if len(key) < 24:
        raise RuntimeError("MEETING_SERVICE_KEY must be configured for the E2E probe")
    return key


async def _publish_audio(credentials: dict[str, object], audio: np.ndarray) -> None:
    room = rtc.Room()
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("e2e-fixture-mic", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    try:
        await asyncio.wait_for(
            room.connect(str(credentials["livekit_url"]), str(credentials["token"])),
            timeout=20.0,
        )
        await room.local_participant.publish_track(track, options)
        # Let the dynamic Agent join before measurement begins.
        await asyncio.sleep(8.0)
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
        # The silence above is actual LiveKit media and lets VAD finalize.
        await asyncio.sleep(15.0)
    finally:
        try:
            await asyncio.wait_for(room.disconnect(), timeout=5.0)
        except Exception:
            pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", default=str(ROOT / "audio" / "thayDung_noi.wav"))
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--service-url", default="http://127.0.0.1:8002")
    parser.add_argument("--minutes-timeout", type=float, default=75.0)
    args = parser.parse_args()

    audio, sample_rate = sf.read(args.audio, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"expected 16 kHz WAV, got {sample_rate} Hz")
    audio = np.asarray(audio[: int(args.seconds * SAMPLE_RATE)], dtype=np.float32)
    # Send endpointing silence as frames; waiting without frames is not enough.
    audio = np.concatenate([audio, np.zeros(4 * SAMPLE_RATE, dtype=np.float32)])

    meeting_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    runtime_id: str | None = None
    key = _service_key()
    headers = {"X-Service-Key": key}
    service_url = args.service_url.rstrip("/")

    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            runtime_response = await client.post(
                f"{service_url}/internal/v1/meetings/{meeting_id}/runtime",
                headers={**headers, "Idempotency-Key": f"e2e-start-{meeting_id}"},
                json={
                    "meeting": {"status": "APPROVED", "title": "E2E audio fixture"},
                    "participants": [{"user_id": user_id, "display_name": "E2E Audio"}],
                },
            )
            runtime_response.raise_for_status()
            runtime_id = str(runtime_response.json()["runtime_session_id"])

            token_response = await client.post(
                f"{service_url}/internal/v1/meetings/{meeting_id}/tokens",
                headers=headers,
                json={
                    "runtime_session_id": runtime_id,
                    "user_id": user_id,
                    "device_id": "e2e-fixture",
                    "permissions": ["JOIN", "PUBLISH_AUDIO"],
                },
            )
            token_response.raise_for_status()
            await _publish_audio(token_response.json(), audio)

            transcript_response = await client.get(
                f"{service_url}/internal/v1/meetings/{meeting_id}/transcript",
                headers=headers,
            )
            transcript_response.raise_for_status()
            segments = transcript_response.json().get("segments", [])
            if not segments:
                raise RuntimeError("E2E expected at least one persisted final transcript segment")

            analysis_response = await client.post(
                f"{service_url}/internal/v1/meetings/{meeting_id}/minutes/analyze",
                headers={**headers, "Idempotency-Key": f"e2e-analyze-{meeting_id}"},
            )
            analysis_response.raise_for_status()
            analysis = analysis_response.json()
            deadline = asyncio.get_running_loop().time() + args.minutes_timeout
            while analysis.get("status") not in {"SUCCEEDED", "FAILED"}:
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError("E2E minutes analysis timed out")
                await asyncio.sleep(2.0)
                status_response = await client.get(
                    f"{service_url}/internal/v1/meetings/{meeting_id}/minutes/analyze",
                    headers=headers,
                )
                status_response.raise_for_status()
                analysis = status_response.json()
            if analysis.get("status") != "SUCCEEDED":
                raise RuntimeError(f"E2E minutes analysis failed: {analysis.get('error_message')}")

            minutes_response = await client.get(
                f"{service_url}/internal/v1/meetings/{meeting_id}/minutes",
                headers=headers,
            )
            minutes_response.raise_for_status()
            minutes = minutes_response.json()
            if int(minutes.get("revision", 0)) < 1:
                raise RuntimeError("E2E expected a persisted minutes revision")
            print(
                "E2E_OK",
                f"meeting_id={meeting_id}",
                f"segments={len(segments)}",
                f"minutes_revision={minutes['revision']}",
            )
        finally:
            if runtime_id:
                response = await client.post(
                    f"{service_url}/internal/v1/runtimes/{runtime_id}/stop",
                    headers={**headers, "Idempotency-Key": f"e2e-stop-{runtime_id}"},
                )
                response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
