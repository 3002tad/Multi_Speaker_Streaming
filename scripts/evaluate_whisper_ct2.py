"""Benchmark a CTranslate2 Whisper model on the labelled meeting turns.

The model is loaded once and each labelled interval is decoded independently.
This keeps the measurement comparable with the final-turn ASR use case and
prevents one turn's language-model context from leaking into the next one.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_breakdown,
    word_error_rate,
)


SAMPLE_RATE = 16_000


def load_turn_audio(
    path: Path, start_seconds: float | None, end_seconds: float | None
) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        target_length = round(len(audio) * SAMPLE_RATE / sample_rate)
        source_axis = np.linspace(0.0, 1.0, len(audio), endpoint=False)
        target_axis = np.linspace(0.0, 1.0, target_length, endpoint=False)
        audio = np.interp(target_axis, source_axis, audio).astype(np.float32)
    start = max(0, int((start_seconds or 0.0) * SAMPLE_RATE))
    end = (
        min(len(audio), int(end_seconds * SAMPLE_RATE))
        if end_seconds is not None
        else len(audio)
    )
    return np.asarray(audio[start:end], dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--truth", action="append", type=Path, default=[])
    parser.add_argument("--language", default="vi")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--cpu-threads", type=int, default=12)
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    truth_paths = args.truth or [PROJECT_ROOT / "audio" / "truth.csv"]
    load_started = time.perf_counter()
    model = WhisperModel(
        str(args.model),
        device="cpu",
        compute_type=args.compute_type,
        cpu_threads=max(1, args.cpu_threads),
        num_workers=1,
    )
    model_load_seconds = time.perf_counter() - load_started

    rows: list[dict[str, object]] = []
    total_audio_seconds = 0.0
    decode_started = time.perf_counter()
    with TemporaryDirectory(prefix="whisper-ct2-turns-") as temporary:
        temporary_path = Path(temporary)
        for truth_path in truth_paths:
            for index, truth in enumerate(load_transcript_truth(truth_path)):
                audio = load_turn_audio(
                    PROJECT_ROOT / "audio" / f"{truth.voice}.wav",
                    truth.start_seconds,
                    truth.end_seconds,
                )
                if not audio.size:
                    raise ValueError(f"Empty truth interval: {truth.voice}")
                turn_path = temporary_path / f"{truth_path.stem}-{index}.wav"
                sf.write(turn_path, audio, SAMPLE_RATE, subtype="PCM_16")
                started = time.perf_counter()
                segments, _ = model.transcribe(
                    str(turn_path),
                    language=args.language,
                    task="transcribe",
                    beam_size=max(1, args.beam_size),
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    word_timestamps=False,
                )
                hypothesis = " ".join(segment.text.strip() for segment in segments).strip()
                elapsed = time.perf_counter() - started
                duration = len(audio) / SAMPLE_RATE
                total_audio_seconds += duration
                breakdown = word_error_breakdown(truth.transcript, hypothesis)
                rows.append(
                    {
                        "truth_file": truth_path.name,
                        "voice": truth.voice,
                        "reference": truth.transcript,
                        "hypothesis": hypothesis,
                        "wer": round(word_error_rate(truth.transcript, hypothesis), 4),
                        "cer": round(character_error_rate(truth.transcript, hypothesis), 4),
                        "deletions": breakdown["deletions"],
                        "insertions": breakdown["insertions"],
                        "substitutions": breakdown["substitutions"],
                        "audio_seconds": round(duration, 3),
                        "processing_seconds": round(elapsed, 3),
                    }
                )
    decode_seconds = time.perf_counter() - decode_started
    report = {
        "engine": "faster-whisper / CTranslate2",
        "model": str(args.model),
        "language": args.language,
        "task": "transcribe",
        "beam_size": args.beam_size,
        "compute_type": args.compute_type,
        "cpu_threads": args.cpu_threads,
        "model_load_seconds": round(model_load_seconds, 3),
        "mean_wer": round(float(np.mean([row["wer"] for row in rows])), 4),
        "mean_cer": round(float(np.mean([row["cer"] for row in rows])), 4),
        "realtime_factor_warm": round(
            decode_seconds / max(total_audio_seconds, 1e-6), 4
        ),
        "items": rows,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
