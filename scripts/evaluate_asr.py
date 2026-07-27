"""Evaluate Zipformer against audio/truth.csv without starting the demo.

Examples:
    venv_linux/bin/python -B scripts/evaluate_asr.py
    venv_linux/bin/python -B scripts/evaluate_asr.py --mode light
    venv_linux/bin/python -B scripts/evaluate_asr.py --mode light --enhancer dpdfnet_baseline
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
    DynamicEnhancementController,
    StreamingDpdfNetEnhancer,
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
    enhancer_name: str,
) -> tuple[str, dict[str, float] | None]:
    stream = recognizer.create_stream()
    tracker = AudioQualityTracker()
    processor = StreamingAsrPreprocessor(
        high_pass_hz=settings.asr_high_pass_hz,
        target_rms=settings.asr_target_rms,
    )
    enhancer = None
    if enhancer_name == "dpdfnet_baseline":
        enhancer = StreamingDpdfNetEnhancer(
            model_path=str(settings.asr_enhancer_model),
            num_threads=settings.asr_enhancer_threads,
            controller=DynamicEnhancementController(
                bypass_snr_db=settings.asr_enhancer_bypass_snr_db,
                full_snr_db=settings.asr_enhancer_full_snr_db,
                maximum_mix=settings.asr_enhancer_max_mix,
                attack=settings.asr_enhancer_attack,
                release=settings.asr_enhancer_release,
            ),
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
        if enhancer is not None:
            frame = enhancer.process(frame, quality=measured)
        if frame.size:
            stream.accept_waveform(SAMPLE_RATE, frame)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
    enhancement = None
    if enhancer is not None:
        tail = enhancer.flush()
        if tail.size:
            stream.accept_waveform(SAMPLE_RATE, tail)
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
        telemetry = enhancer.telemetry()
        enhancement = {
            "average_mix": round(telemetry.average_mix, 4),
            "peak_mix": round(telemetry.peak_mix, 4),
        }
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result(stream)
    text = (
        result.text.strip()
        if hasattr(result, "text")
        else str(result).strip()
    )
    return text, enhancement


def evaluate(mode: str, *, enhancer_name: str) -> dict:
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
        hypothesis, enhancement = decode(
            recognizer,
            audio,
            use_light_preprocessing=mode == "light",
            enhancer_name=enhancer_name,
        )
        elapsed = time.perf_counter() - item_started
        item = {
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
        if enhancement is not None:
            item["enhancement"] = enhancement
        rows.append(item)
    elapsed = time.perf_counter() - started
    return {
        "mode": mode,
        "enhancer": enhancer_name,
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
        "--enhancer",
        choices=("none", "dpdfnet_baseline"),
        default="none",
        help="Optional ASR-only neural enhancer for A/B comparison.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the JSON report",
    )
    args = parser.parse_args()
    modes = ("raw", "light") if args.mode == "both" else (args.mode,)
    report = {
        "results": [
            evaluate(mode, enhancer_name=args.enhancer) for mode in modes
        ]
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
