"""Benchmark an offline Fun-ASR GGUF worker on labelled meeting turns.

The CLI is intentionally invoked per labelled turn.  This measures the
worst-case process/model-load cost that the current full-turn queue would pay;
it does not pretend that an offline model is a realtime decoder.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf


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
_TEXT_LINE = re.compile(r"^text:\s*(.*)$", flags=re.MULTILINE)
_RTF_LINE = re.compile(r"^\s*realtime:\s*(.*)$", flags=re.MULTILINE)


def load_turn_audio(path: Path, start_seconds: float | None, end_seconds: float | None) -> np.ndarray:
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


def run_cli(
    cli: Path,
    model: Path,
    audio_path: Path,
    *,
    language: str,
    threads: int,
    itn: bool,
    extra_args: tuple[str, ...],
    timeout_seconds: float,
) -> tuple[str, str, float]:
    command = [
        str(cli),
        "--quiet",
        "--threads",
        str(max(1, threads)),
        "--language",
        language,
        "-m",
        str(model),
    ]
    if itn:
        command.append("--itn")
    command.extend(extra_args)
    command.append(str(audio_path))
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    elapsed = time.perf_counter() - started
    output = "\n".join(
        value for value in (completed.stdout, completed.stderr) if value
    )
    if completed.returncode:
        raise RuntimeError(
            f"transcribe-cli exit={completed.returncode}: {output[-1200:]}"
        )
    text_match = _TEXT_LINE.search(output)
    if text_match is None:
        raise RuntimeError(f"Cannot parse transcript: {output[-1200:]}")
    rtf_match = _RTF_LINE.search(output)
    return text_match.group(1).strip(), (rtf_match.group(1).strip() if rtf_match else ""), elapsed


def evaluate(
    *,
    cli: Path,
    model: Path,
    truth_paths: list[Path],
    language: str,
    threads: int,
    itn: bool,
    extra_args: tuple[str, ...],
    timeout_seconds: float,
) -> dict[str, object]:
    rows = []
    total_audio_seconds = 0.0
    started = time.perf_counter()
    with TemporaryDirectory(prefix="funasr-gguf-turns-") as temporary:
        temporary_path = Path(temporary)
        for truth_path in truth_paths:
            for index, truth in enumerate(load_transcript_truth(truth_path)):
                source_path = PROJECT_ROOT / "audio" / f"{truth.voice}.wav"
                audio = load_turn_audio(
                    source_path, truth.start_seconds, truth.end_seconds
                )
                if not audio.size:
                    raise ValueError(f"Empty truth interval: {truth.voice}")
                turn_path = temporary_path / f"{truth_path.stem}-{index}.wav"
                sf.write(turn_path, audio, SAMPLE_RATE, subtype="PCM_16")
                hypothesis, engine_realtime, elapsed = run_cli(
                    cli,
                    model,
                    turn_path,
                    language=language,
                    threads=threads,
                    itn=itn,
                    extra_args=extra_args,
                    timeout_seconds=timeout_seconds,
                )
                duration = len(audio) / SAMPLE_RATE
                total_audio_seconds += duration
                breakdown = word_error_breakdown(truth.transcript, hypothesis)
                rows.append({
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
                    "engine_realtime": engine_realtime,
                })
    elapsed = time.perf_counter() - started
    return {
        "engine": "transcribe.cpp",
        "model": str(model),
        "language": language,
        "itn": itn,
        "extra_args": list(extra_args),
        "threads": threads,
        "mean_wer": round(float(np.mean([row["wer"] for row in rows])), 4),
        "mean_cer": round(float(np.mean([row["cer"] for row in rows])), 4),
        "realtime_factor_including_model_load": round(
            elapsed / max(total_audio_seconds, 1e-6), 4
        ),
        "items": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cli",
        type=Path,
        default=Path("/home/ntd/meeting_runtime/tools/transcribe.cpp/build/bin/transcribe-cli"),
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("/home/ntd/meeting_runtime/models/fun-asr-mlt-nano-2512-q6/Fun-ASR-MLT-Nano-2512-Q6_K.gguf"),
    )
    parser.add_argument(
        "--truth",
        type=Path,
        action="append",
        default=[],
        help="CSV truth file; may be provided more than once.",
    )
    parser.add_argument("--language", default="vi")
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--no-itn", action="store_true")
    parser.add_argument(
        "--extra-arg",
        action="append",
        default=[],
        help=(
            "Extra transcribe-cli argument; repeat for its value, e.g. "
            "--extra-arg=--stream-chunk-ms --extra-arg=1120."
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    truth_paths = args.truth or [PROJECT_ROOT / "audio" / "truth.csv"]
    report = evaluate(
        cli=args.cli,
        model=args.model,
        truth_paths=truth_paths,
        language=args.language,
        threads=args.threads,
        itn=not args.no_itn,
        extra_args=tuple(args.extra_arg),
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
