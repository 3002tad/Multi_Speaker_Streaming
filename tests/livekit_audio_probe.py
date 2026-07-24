"""Publish a short WAV sample through LiveKit to exercise the full AI path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import torch
import torchaudio
from livekit import rtc


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320
PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    audio, sample_rate = sf.read(
        PROJECT_ROOT / "audio" / "thayDung_noi.wav",
        dtype="float32",
    )
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        waveform = torch.from_numpy(audio).unsqueeze(0)
        audio = (
            torchaudio.functional.resample(
                waveform, sample_rate, SAMPLE_RATE
            )
            .squeeze(0)
            .numpy()
        )

    # Twelve seconds is enough to trigger partial and final transcript.
    audio = audio[: SAMPLE_RATE * 12]
    audio = np.concatenate(
        [audio, np.zeros(SAMPLE_RATE * 2, dtype=np.float32)]
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "http://127.0.0.1:8000/api/meeting/join",
            json={
                "display_name": "Audio Probe",
                "meeting_code": "DEMO-001",
            },
        )
        response.raise_for_status()
        credentials = response.json()

    room = rtc.Room()
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("probe-microphone", source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE

    try:
        await room.connect(
            credentials["livekit_url"],
            credentials["token"],
        )
        await room.local_participant.publish_track(track, options)
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

        await asyncio.sleep(8)
        print("AUDIO_PROBE_PUBLISHED_SECONDS", len(audio) / SAMPLE_RATE)
    finally:
        await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
