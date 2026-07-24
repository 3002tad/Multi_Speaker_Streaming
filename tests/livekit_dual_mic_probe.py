"""Publish the two synchronized cross-mic recordings through LiveKit."""

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


def load_audio(filename: str) -> np.ndarray:
    audio, sample_rate = sf.read(
        PROJECT_ROOT / "audio" / filename,
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
    return audio


async def get_credentials(name: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "http://127.0.0.1:8000/api/meeting/join",
            json={"display_name": name, "meeting_code": "DEMO-001"},
        )
        response.raise_for_status()
        return response.json()


async def publish_source(
    name: str,
    audio: np.ndarray,
    ready: asyncio.Event,
) -> None:
    credentials = await get_credentials(name)
    room = rtc.Room()
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track(
        f"{name}-microphone", source
    )
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE

    try:
        await room.connect(
            credentials["livekit_url"],
            credentials["token"],
        )
        await room.local_participant.publish_track(track, options)
        await ready.wait()

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

        # Keep the participant connected while VAD, WavLM and Qwen finalize.
        await asyncio.sleep(12)
    finally:
        await room.disconnect()


async def main() -> None:
    mic_a = load_audio("thayDung_noi.wav")
    mic_b = load_audio("thayPhuoc_noi.wav")
    sample_count = max(len(mic_a), len(mic_b)) + SAMPLE_RATE * 2
    mic_a = np.pad(mic_a, (0, sample_count - len(mic_a)))
    mic_b = np.pad(mic_b, (0, sample_count - len(mic_b)))

    ready = asyncio.Event()
    tasks = [
        asyncio.create_task(publish_source("Mic A", mic_a, ready)),
        asyncio.create_task(publish_source("Mic B", mic_b, ready)),
    ]
    await asyncio.sleep(2)
    ready.set()
    await asyncio.gather(*tasks)
    print("DUAL_MIC_PROBE_OK", round(sample_count / SAMPLE_RATE, 2))


if __name__ == "__main__":
    asyncio.run(main())
