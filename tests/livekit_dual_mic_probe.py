"""Publish the two synchronized cross-mic recordings through LiveKit."""

from __future__ import annotations

import argparse
import asyncio
import csv
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
TURN_SECONDS = 15.0
INTER_TURN_SILENCE_SECONDS = 1.25
CONNECTION_WARMUP_SECONDS = 15.0


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


def load_truth_segment(voice: str) -> np.ndarray:
    truth_path = PROJECT_ROOT / "audio" / "truth.csv"
    with truth_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row.get("voice") != voice:
            continue
        start_seconds = float(row.get("start_seconds") or 0.0)
        end_seconds = float(row.get("end_seconds") or start_seconds)
        start_sample = int(round(start_seconds * SAMPLE_RATE))
        end_sample = int(round(end_seconds * SAMPLE_RATE))
        return load_audio(f"{voice}.wav")[start_sample:end_sample]
    raise ValueError(f"Không tìm thấy voice={voice!r} trong audio/truth.csv")


def build_sequential_cross_mic_audio(
    first_speaker: np.ndarray,
    second_speaker: np.ndarray,
    *,
    cross_mic_gain: float,
    turn_seconds: float = TURN_SECONDS,
    inter_turn_silence_seconds: float = INTER_TURN_SILENCE_SECONDS,
    trailing_silence_seconds: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the two-mic scenario described by ``audio/truth.csv``.

    The ground truth contains two *sequential* 15-second turns.  Publishing
    those recordings concurrently used to create an artificial overlap, while
    the regression runner compared it against the sequential timestamps.  In
    a physical room each microphone receives both speakers, but one is the
    stronger source for a given turn.  This helper models that condition.
    """
    turn_samples = int(round(turn_seconds * SAMPLE_RATE))
    if len(first_speaker) > turn_samples or len(second_speaker) > turn_samples:
        raise ValueError(
            "Audio fixture dài hơn khoảng turn đã khai báo; "
            "hãy cập nhật TURN_SECONDS và truth.csv cùng lúc."
        )

    first = np.pad(
        np.asarray(first_speaker, dtype=np.float32),
        (0, turn_samples - len(first_speaker)),
    )
    second = np.pad(
        np.asarray(second_speaker, dtype=np.float32),
        (0, turn_samples - len(second_speaker)),
    )
    inter_turn_silence_samples = int(
        round(inter_turn_silence_seconds * SAMPLE_RATE)
    )
    second_turn_start = turn_samples + inter_turn_silence_samples
    total_samples = second_turn_start + turn_samples + int(
        round(trailing_silence_seconds * SAMPLE_RATE)
    )
    mic_a = np.zeros(total_samples, dtype=np.float32)
    mic_b = np.zeros(total_samples, dtype=np.float32)

    # Turn 1: speaker A is closest to mic A. Turn 2 is the reverse.
    mic_a[:turn_samples] = first
    mic_b[:turn_samples] = first * cross_mic_gain
    mic_a[second_turn_start : second_turn_start + turn_samples] = (
        second * cross_mic_gain
    )
    mic_b[second_turn_start : second_turn_start + turn_samples] = second

    return (
        np.clip(mic_a, -1.0, 1.0),
        np.clip(mic_b, -1.0, 1.0),
    )


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
    *,
    identity_sink: dict[str, str] | None = None,
    finalization_wait_seconds: float = 12.0,
) -> None:
    credentials = await get_credentials(name)
    if identity_sink is not None:
        identity_sink[name] = str(credentials["identity"])
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
        await asyncio.sleep(finalization_wait_seconds)
    finally:
        await room.disconnect()


async def main(
    *,
    cross_mic_gain: float,
    inter_turn_silence_seconds: float,
    connection_warmup_seconds: float,
) -> None:
    mic_a, mic_b = build_sequential_cross_mic_audio(
        load_truth_segment("thayDung_noi"),
        load_truth_segment("thayPhuoc_noi"),
        cross_mic_gain=cross_mic_gain,
        inter_turn_silence_seconds=inter_turn_silence_seconds,
    )
    sample_count = len(mic_a)

    ready = asyncio.Event()
    tasks = [
        asyncio.create_task(publish_source("Mic A", mic_a, ready)),
        asyncio.create_task(publish_source("Mic B", mic_b, ready)),
    ]
    # Publishing the track triggers one Zipformer stream allocation per mic.
    # That allocation is still synchronous in the locked baseline and can
    # take more than ten seconds on a cold CPU process. Starting fixture speech
    # earlier measures initialization loss instead of streaming ASR quality.
    await asyncio.sleep(connection_warmup_seconds)
    ready.set()
    await asyncio.gather(*tasks)
    print(
        "DUAL_MIC_PROBE_OK",
        round(sample_count / SAMPLE_RATE, 2),
        f"cross_mic_gain={cross_mic_gain:.2f}",
        f"inter_turn_silence={inter_turn_silence_seconds:.2f}",
        f"connection_warmup={connection_warmup_seconds:.2f}",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cross-mic-gain",
        type=float,
        default=0.18,
        help="Tỷ lệ tiếng lọt từ người nói xa tới mic còn lại (0..1).",
    )
    parser.add_argument(
        "--inter-turn-silence",
        type=float,
        default=INTER_TURN_SILENCE_SECONDS,
        help="Silence between the fixture turns in seconds.",
    )
    parser.add_argument(
        "--connection-warmup",
        type=float,
        default=CONNECTION_WARMUP_SECONDS,
        help="Wait after publishing tracks before sending measured speech.",
    )
    args = parser.parse_args()
    if not 0.0 <= args.cross_mic_gain <= 1.0:
        parser.error("--cross-mic-gain phải nằm trong khoảng 0..1")
    if args.inter_turn_silence < 0.0:
        parser.error("--inter-turn-silence must not be negative")
    if args.connection_warmup < 0.0:
        parser.error("--connection-warmup must not be negative")
    asyncio.run(
        main(
            cross_mic_gain=args.cross_mic_gain,
            inter_turn_silence_seconds=args.inter_turn_silence,
            connection_warmup_seconds=args.connection_warmup,
        )
    )
