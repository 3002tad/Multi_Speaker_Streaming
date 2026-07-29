"""End-to-end LiveKit streaming regression test.

With an already running demo:
    venv_linux/bin/python -B scripts/streaming_regression.py

Start and safely stop the WSL demo automatically:
    venv_linux/bin/python -B scripts/streaming_regression.py --start-demo
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import PROJECT_ROOT as CONFIG_ROOT, settings
from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_rate,
)


def _overlap_score(
    item: dict[str, Any],
    *,
    expected_start: float,
    expected_end: float,
) -> float:
    start = float(item.get("start_time", 0.0))
    end = float(item.get("end_time", 0.0))
    overlap = max(
        0.0,
        min(end, expected_end) - max(start, expected_start),
    )
    duration = max(expected_end - expected_start, 1e-6)
    return overlap / duration


def _overlap_seconds(
    item: dict[str, Any],
    *,
    expected_start: float,
    expected_end: float,
) -> float:
    return max(
        0.0,
        min(float(item.get("end_time", 0.0)), expected_end)
        - max(float(item.get("start_time", 0.0)), expected_start),
    )


def _assign_segments(
    items: list[dict[str, Any]],
    truth: list[Any],
    *,
    base_time: float,
    boundary_slack_seconds: float = 1.25,
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Assign every final to at most one truth interval by maximum overlap."""
    groups: list[list[dict[str, Any]]] = [[] for _ in truth]
    unassigned = []
    for item in items:
        overlaps = [
            _overlap_seconds(
                item,
                expected_start=(
                    base_time
                    + float(row.start_seconds)
                    - boundary_slack_seconds
                ),
                expected_end=(
                    base_time
                    + float(row.end_seconds)
                    + boundary_slack_seconds
                ),
            )
            for row in truth
        ]
        best_index = max(range(len(overlaps)), key=overlaps.__getitem__)
        if overlaps[best_index] <= 0:
            unassigned.append(item)
            continue
        groups[best_index].append(item)
    for group in groups:
        group.sort(key=lambda item: float(item.get("start_time", 0.0)))
    return groups, unassigned


def _coverage_score(
    items: list[dict[str, Any]],
    *,
    expected_start: float,
    expected_end: float,
) -> float:
    intervals = []
    for item in items:
        start = max(float(item.get("start_time", 0.0)), expected_start)
        end = min(float(item.get("end_time", 0.0)), expected_end)
        if end > start:
            intervals.append((start, end))
    covered = 0.0
    current_start = current_end = None
    for start, end in sorted(intervals):
        if current_start is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            covered += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None:
        covered += current_end - current_start
    return covered / max(expected_end - expected_start, 1e-6)


def _merge_segment_text(items: list[dict[str, Any]]) -> str:
    """Join consecutive ASR finals while removing exact boundary repeats."""
    merged: list[str] = []
    for item in items:
        text = str(item.get("raw_text") or item.get("text", "")).strip()
        words = text.split()
        if not words:
            continue
        normalized_merged = [word.casefold() for word in merged]
        normalized_words = [word.casefold() for word in words]
        duplicate = 0
        for length in range(
            min(12, len(merged), len(words)),
            0,
            -1,
        ):
            if normalized_merged[-length:] == normalized_words[:length]:
                duplicate = length
                break
        merged.extend(words[duplicate:])
    return " ".join(merged)


def _start_demo(log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    command = ["bash", "scripts/run_demo.sh"]
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )


def _stop_demo(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    print("[streaming-test] Dừng run_demo.sh an toàn...")
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        print("[streaming-test] Quá hạn; gửi SIGTERM...")
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print("[streaming-test] Buộc kết thúc run_demo.sh.")
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def _wait_for_services(
    client: httpx.Client,
    base_url: str,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = "chưa có phản hồi"
    while time.monotonic() < deadline:
        try:
            health = client.get(f"{base_url}/api/health")
            ai = client.get("http://127.0.0.1:8001/")
            if health.status_code == 200 and ai.status_code == 200:
                return
            last_error = f"backend={health.status_code}, ai={ai.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(
        f"Backend/AI chưa sẵn sàng sau {timeout:.0f}s: {last_error}"
    )


def _fetch_transcripts(
    client: httpx.Client,
    base_url: str,
    *,
    truth: list[Any],
    min_overlap: float,
    timeout: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    latest: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        response = client.get(f"{base_url}/api/transcripts")
        response.raise_for_status()
        latest = response.json().get("items", [])
        if _covers_truth(latest, truth, min_overlap=min_overlap):
            return latest
        time.sleep(0.5)
    return latest


def _covers_truth(
    items: list[dict[str, Any]],
    truth: list[Any],
    *,
    min_overlap: float,
) -> bool:
    if not items:
        return False
    base_time = min(float(item.get("start_time", 0.0)) for item in items)
    groups, _ = _assign_segments(items, truth, base_time=base_time)
    for row, group in zip(truth, groups):
        if row.start_seconds is None or row.end_seconds is None:
            return False
        expected_start = base_time + float(row.start_seconds)
        expected_end = base_time + float(row.end_seconds)
        if _coverage_score(
            group,
            expected_start=expected_start,
            expected_end=expected_end,
        ) < min_overlap:
            return False
    return True


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.backend_url.rstrip("/")
    truth = load_transcript_truth(CONFIG_ROOT / "audio" / "truth.csv")
    if any(
        row.start_seconds is None or row.end_seconds is None
        for row in truth
    ):
        raise ValueError(
            "Streaming regression cần start_seconds/end_seconds trong truth.csv"
        )

    demo_process: subprocess.Popen | None = None
    log_path = PROJECT_ROOT / "output" / "streaming-regression-demo.log"
    if args.start_demo:
        print("[streaming-test] Khởi động run_demo.sh...")
        demo_process = _start_demo(log_path)

    try:
        with httpx.Client(timeout=15.0) as client:
            _wait_for_services(
                client,
                base_url,
                timeout=args.start_timeout,
            )
            client.delete(f"{base_url}/api/transcripts").raise_for_status()

            print("[streaming-test] Chạy dual-mic LiveKit probe...")
            probe = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "tests/livekit_dual_mic_probe.py",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=args.probe_timeout,
                check=False,
            )
            print(probe.stdout.rstrip())
            if probe.stderr:
                print(probe.stderr.rstrip(), file=sys.stderr)

            items = _fetch_transcripts(
                client,
                base_url,
                truth=truth,
                min_overlap=args.min_overlap,
                timeout=args.final_timeout,
            )
    finally:
        if demo_process is not None and not args.keep_demo:
            _stop_demo(demo_process)

    all_items = sorted(
        [
            item
            for item in items
            if item.get("text") and item.get("type", "transcript.final")
            == "transcript.final"
        ],
        key=lambda item: float(item.get("start_time", 0.0)),
    )
    if not all_items:
        raise RuntimeError(
            "Streaming probe không tạo được transcript.final. "
            f"Xem log: {log_path}"
        )

    base_time = min(
        float(item.get("start_time", 0.0)) for item in all_items
    )
    groups, unassigned = _assign_segments(
        all_items,
        truth,
        base_time=base_time,
    )
    items = [item for group in groups for item in group]
    matches = []
    for row, group in zip(truth, groups):
        expected_start = base_time + float(row.start_seconds)
        expected_end = base_time + float(row.end_seconds)
        overlap = _coverage_score(
            group,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        hypothesis = _merge_segment_text(group)
        primary = max(
            group,
            key=lambda item: (
                _overlap_seconds(
                    item,
                    expected_start=expected_start,
                    expected_end=expected_end,
                ),
                float(item.get("signal_rms") or 0.0),
            ),
            default={},
        )
        matches.append(
            {
                "voice": row.voice,
                "expected_range": [
                    row.start_seconds,
                    row.end_seconds,
                ],
                "overlap": round(overlap, 4),
                "reference": row.transcript,
                "hypothesis": hypothesis,
                "segment_count": len(group),
                "wer": round(
                    word_error_rate(row.transcript, hypothesis), 4
                ),
                "cer": round(
                    character_error_rate(row.transcript, hypothesis), 4
                ),
                "source_id": primary.get("source_id"),
                "speaker": primary.get("speaker"),
                "global_turn_ids": list(
                    dict.fromkeys(
                        item.get("global_turn_id")
                        for item in group
                        if item.get("global_turn_id")
                    )
                ),
                "signal_snr_db": primary.get("signal_snr_db"),
                "clipping_ratio": primary.get("clipping_ratio"),
                "identity_method": primary.get("identity_method"),
                "speaker_id_ms": primary.get("speaker_id_ms"),
                "pipeline_ms": primary.get("pipeline_ms"),
                "refinement_ms": primary.get("refinement_ms"),
                "refinement": primary.get("refinement"),
                "phonetic_replacements": primary.get(
                    "phonetic_replacements", []
                ),
            }
        )

    global_turns = [
        item.get("global_turn_id")
        for item in items
        if item.get("global_turn_id")
    ]
    checks = {
        "probe_ok": "DUAL_MIC_PROBE_OK" in probe.stdout,
        "transcript_count": len(items),
        "all_intervals_have_segments": all(groups),
        "unassigned_tail_count": len(unassigned),
        "all_matches_overlap": all(
            item["overlap"] >= args.min_overlap for item in matches
        ),
        "wer_threshold": all(
            item["wer"] <= args.max_wer for item in matches
        ),
        "cer_threshold": all(
            item["cer"] <= args.max_cer for item in matches
        ),
        "global_turn_metadata": len(global_turns) == len(items),
        "quality_metadata": all(
            item["signal_snr_db"] is not None
            and item["clipping_ratio"] is not None
            for item in matches
        ),
    }
    passed = all(
        value
        for key, value in checks.items()
        if key not in {"transcript_count", "unassigned_tail_count"}
    )
    return {
        "status": "passed" if passed else "failed",
        "backend_url": base_url,
        "probe_exit_code": probe.returncode,
        "checks": checks,
        "matches": matches,
        "transcripts": all_items,
        "demo_log": str(log_path) if args.start_demo else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend-url",
        default=os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--start-demo", action="store_true")
    parser.add_argument("--keep-demo", action="store_true")
    parser.add_argument("--start-timeout", type=float, default=300)
    parser.add_argument("--probe-timeout", type=float, default=180)
    parser.add_argument("--final-timeout", type=float, default=75)
    parser.add_argument("--min-overlap", type=float, default=0.35)
    parser.add_argument("--max-wer", type=float, default=0.65)
    parser.add_argument("--max-cer", type=float, default=0.60)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = run(args)
    except Exception as exc:
        print(f"[streaming-test] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
