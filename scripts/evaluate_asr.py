"""Evaluate Zipformer against audio/truth.csv without starting the demo.

Examples:
    venv_linux/bin/python -B scripts/evaluate_asr.py
    venv_linux/bin/python -B scripts/evaluate_asr.py --mode light
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from backend.audio_pipeline import (
    AudioQualityTracker,
    StreamingAsrPreprocessor,
)
from backend.config import PROJECT_ROOT, settings
from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_rate,
)


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_600


def create_recognizer() -> sherpa_onnx.OnlineRecognizer:
    model_dir = PROJECT_ROOT / "Zipformer-30M-RNNT-Streaming-6000h"
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model_dir / "config.json"),
        encoder=str(
            model_dir
            / "encoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx"
        ),
        decoder=str(
            model_dir
            / "decoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx"
        ),
        joiner=str(
            model_dir
            / "joiner-epoch-31-avg-11-chunk-16-left-128.fp16.onnx"
        ),
        num_threads=1,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="modified_beam_search",
        max_active_paths=4,
        provider="cpu",
    )


def load_audio(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        output_length = round(len(audio) * SAMPLE_RATE / sample_rate)
        old_axis = np.linspace(0.0, 1.0, len(audio), endpoint=False)
        new_axis = np.linspace(
            0.0, 1.0, output_length, endpoint=False
        )
        audio = np.interp(new_axis, old_axis, audio).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


def decode(
    recognizer: sherpa_onnx.OnlineRecognizer,
    audio: np.ndarray,
    *,
    use_light_preprocessing: bool,
) -> str:
    stream = recognizer.create_stream()
    tracker = AudioQualityTracker()
    processor = StreamingAsrPreprocessor(
        high_pass_hz=settings.asr_high_pass_hz,
        target_rms=settings.asr_target_rms,
    )
    for start in range(0, len(audio), FRAME_SAMPLES):
        frame = audio[start : start + FRAME_SAMPLES]
        if len(frame) < FRAME_SAMPLES:
            frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
        rms = float(np.sqrt(np.mean(np.square(frame))))
        speech_active = rms >= max(0.008, tracker.noise_floor * 2.5)
        measured = tracker.measure(
            frame, speech_active=speech_active
        )
        if use_light_preprocessing:
            frame = processor.process(frame, quality=measured)
        stream.accept_waveform(SAMPLE_RATE, frame)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result(stream)
    return (
        result.text.strip()
        if hasattr(result, "text")
        else str(result).strip()
    )


def evaluate(mode: str) -> dict:
    truth_path = PROJECT_ROOT / "audio" / "truth.csv"
    truth_rows = load_transcript_truth(truth_path)
    recognizer = create_recognizer()
    rows = []
    started = time.perf_counter()
    total_audio_seconds = 0.0
    for truth in truth_rows:
        path = PROJECT_ROOT / "audio" / f"{truth.voice}.wav"
        audio = load_audio(path)
        if truth.start_seconds is not None:
            start = int(truth.start_seconds * SAMPLE_RATE)
            end = (
                int(truth.end_seconds * SAMPLE_RATE)
                if truth.end_seconds is not None
                else len(audio)
            )
            audio = audio[start:end]
        total_audio_seconds += len(audio) / SAMPLE_RATE
        item_started = time.perf_counter()
        hypothesis = decode(
            recognizer,
            audio,
            use_light_preprocessing=mode == "light",
        )
        elapsed = time.perf_counter() - item_started
        rows.append(
            {
                "voice": truth.voice,
                "reference": truth.transcript,
                "hypothesis": hypothesis,
                "wer": round(
                    word_error_rate(truth.transcript, hypothesis), 4
                ),
                "cer": round(
                    character_error_rate(truth.transcript, hypothesis), 4
                ),
                "audio_seconds": round(len(audio) / SAMPLE_RATE, 3),
                "processing_seconds": round(elapsed, 3),
            }
        )
    elapsed = time.perf_counter() - started
    return {
        "mode": mode,
        "mean_wer": round(
            float(np.mean([item["wer"] for item in rows])), 4
        ),
        "mean_cer": round(
            float(np.mean([item["cer"] for item in rows])), 4
        ),
        "realtime_factor": round(
            elapsed / max(total_audio_seconds, 1e-6), 4
        ),
        "items": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("raw", "light", "both"),
        default="both",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the JSON report",
    )
    args = parser.parse_args()
    modes = ("raw", "light") if args.mode == "both" else (args.mode,)
    report = {"results": [evaluate(mode) for mode in modes]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
