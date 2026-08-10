"""LiveKit regression for multiple people speaking at the same time.

The locked sequential regression verifies cross-mic leakage.  This companion
test publishes distinct clips from ``truth_1.csv`` at one shared timestamp and
requires every LiveKit source to produce both realtime and final transcript
events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.evaluation import (
    character_error_rate,
    load_transcript_truth,
    word_error_rate,
)
from scripts.streaming_regression import (
    _start_demo,
    _merge_segment_text,
    _stop_demo,
    _wait_for_services,
)
from tests.livekit_dual_mic_probe import (
    PROJECT_ROOT as PROBE_ROOT,
    SAMPLE_RATE,
    load_audio,
    publish_source,
)


MEETING_WS_URL = "ws://127.0.0.1:8000/ws/meeting"


def _load_cases(limit: int) -> list[dict[str, Any]]:
    truth = load_transcript_truth(PROJECT_ROOT / "audio" / "truth_1.csv")
    cases = []
    for index, row in enumerate(truth[:limit]):
        if row.start_seconds is None or row.end_seconds is None:
            raise ValueError("truth_1.csv requires start/end timestamps")
        start = int(round(row.start_seconds * SAMPLE_RATE))
        end = int(round(row.end_seconds * SAMPLE_RATE))
        cases.append(
            {
                "voice": row.voice,
                "name": f"Concurrent Mic {index + 1} ({row.voice})",
                "audio": load_audio(f"{row.voice}.wav")[start:end],
            }
        )
    if len(cases) != limit:
        raise ValueError(
            f"truth_1.csv only provides {len(cases)} cases; requested {limit}"
        )
    return cases


def _speaker_voice(speaker: Any) -> str:
    value = str(speaker or "")
    if "(" not in value or not value.endswith(")"):
        return ""
    return value.rsplit("(", 1)[1][:-1].strip()


def _evaluate_stored_finals(
    stored: list[dict[str, Any]],
    truth: list[Any],
) -> dict[str, Any]:
    """Score persisted finals per source without mixing concurrent tracks."""
    finals = [
        item
        for item in stored
        if item.get("type", "transcript.final") == "transcript.final"
        and item.get("text")
    ]
    by_voice = {
        row.voice: sorted(
            [
                item
                for item in finals
                if _speaker_voice(item.get("speaker")) == row.voice
            ],
            key=lambda item: float(item.get("start_time", 0.0)),
        )
        for row in truth
    }
    matches = []
    for row in truth:
        hypothesis = _merge_segment_text(by_voice[row.voice])
        matches.append(
            {
                "voice": row.voice,
                "segment_count": len(by_voice[row.voice]),
                "reference": row.transcript,
                "hypothesis": hypothesis,
                "wer": round(word_error_rate(row.transcript, hypothesis), 4),
                "cer": round(character_error_rate(row.transcript, hypothesis), 4),
            }
        )
    segment_ids = [str(item.get("segment_id") or "") for item in finals]
    duplicate_segment_ids = len(segment_ids) - len(set(segment_ids))
    mean_wer = sum(item["wer"] for item in matches) / max(len(matches), 1)
    mean_cer = sum(item["cer"] for item in matches) / max(len(matches), 1)
    return {
        "final_count": len(finals),
        "duplicate_segment_ids": duplicate_segment_ids,
        "all_sources_have_final": all(by_voice.values()),
        "mean_wer": round(mean_wer, 4),
        "mean_cer": round(mean_cer, 4),
        "matches": matches,
    }


def _baseline_for_truth(truth: list[Any]) -> dict[str, Any]:
    """Select the locked per-voice baseline matching this probe's sources."""
    manifest_path = PROJECT_ROOT / "baseline" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["regression"]["audio/truth_1.csv"]["items"]
    by_voice = {str(item["voice"]): item for item in items}
    selected = [by_voice[row.voice] for row in truth]
    return {
        "mean_wer": round(
            sum(float(item["wer"]) for item in selected) / len(selected), 4
        ),
        "mean_cer": round(
            sum(float(item["cer"]) for item in selected) / len(selected), 4
        ),
        "items": selected,
    }


def _streaming_control_for_truth(
    path: Path,
    truth: list[Any],
) -> dict[str, Any]:
    """Load a sequential LiveKit control and align its scores by voice."""
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schedule") != "sequential":
        raise ValueError("reference report must use --schedule sequential")
    by_voice = {
        str(item.get("voice")): item
        for item in report.get("quality", {}).get("matches", [])
    }
    selected = [by_voice[row.voice] for row in truth]
    return {
        "report": str(path),
        "mean_wer": round(
            sum(float(item["wer"]) for item in selected) / len(selected), 4
        ),
        "mean_cer": round(
            sum(float(item["cer"]) for item in selected) / len(selected), 4
        ),
        "items": selected,
    }


async def _collect_events(
    events: list[dict[str, Any]],
    connected: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    async with websockets.connect(MEETING_WS_URL, ping_interval=10) as websocket:
        connected.set()
        while not stop.is_set():
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=1)
            except asyncio.TimeoutError:
                continue
            payload = json.loads(message)
            payload["observed_at"] = time.time()
            events.append(payload)



async def _wait_for_decoder_ready_health(
    identities: dict[str, str],
    timeout: float,
    expected_count: int,
) -> None:
    """Poll AI readiness until the requested sources own ASR streams."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=2) as client:
        while True:
            expected = {value for value in identities.values() if value}
            try:
                response = await client.get(
                    "http://127.0.0.1:8001/health/ready"
                )
                response.raise_for_status()
                active = {
                    str(value)
                    for value in response.json().get("active_streams", [])
                }
            except (httpx.HTTPError, ValueError):
                active = set()
            if len(expected) >= expected_count and expected.issubset(active):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "ASR decoder ready timeout: "
                    f"expected={sorted(expected)}, active={sorted(active)}"
                )
            await asyncio.sleep(min(0.1, remaining))


async def _run_probe(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.mics)
    events: list[dict[str, Any]] = []
    identities: dict[str, str] = {}
    connected = asyncio.Event()
    stop = asyncio.Event()
    collector = asyncio.create_task(
        _collect_events(
            events,
            connected,
            stop,
        )
    )
    await asyncio.wait_for(connected.wait(), timeout=10)

    started_at = time.time()
    if args.schedule == "simultaneous":
        start_audio = asyncio.Event()
        decoder_ready = asyncio.Event()
        published_events = [asyncio.Event() for _ in cases]
        tasks = [
            asyncio.create_task(
                publish_source(
                    case["name"],
                    case["audio"],
                    start_audio,
                    identity_sink=identities,
                    published_event=published_events[index],
                    decoder_ready=decoder_ready,
                    pre_roll_seconds=args.decoder_pre_roll,
                    finalization_wait_seconds=args.finalization_wait,
                )
            )
            for index, case in enumerate(cases)
        ]
        await asyncio.gather(*(event.wait() for event in published_events))
        start_audio.set()
        await _wait_for_decoder_ready_health(
            identities,
            args.connection_warmup,
            len(cases),
        )
        decoder_ready.set()
        await asyncio.gather(*tasks)
    else:
        # Control run for the same LiveKit/VAD/finalization path. Each source
        # completes before the next starts, so its score is comparable with a
        # simultaneous run without confusing offline and streaming metrics.
        for case in cases:
            start_audio = asyncio.Event()
            decoder_ready = asyncio.Event()
            published_event = asyncio.Event()
            current_identity: dict[str, str] = {}
            task = asyncio.create_task(
                publish_source(
                    case["name"],
                    case["audio"],
                    start_audio,
                    identity_sink=current_identity,
                    published_event=published_event,
                    decoder_ready=decoder_ready,
                    pre_roll_seconds=args.decoder_pre_roll,
                    finalization_wait_seconds=args.finalization_wait,
                )
            )
            await published_event.wait()
            start_audio.set()
            await _wait_for_decoder_ready_health(
                current_identity,
                args.connection_warmup,
                1,
            )
            decoder_ready.set()
            await task
            identities.update(current_identity)
    await asyncio.sleep(1)
    stop.set()
    await asyncio.wait_for(collector, timeout=3)

    by_source: dict[str, dict[str, Any]] = {}
    for case in cases:
        source_id = identities.get(case["name"], "")
        source_events = [
            event
            for event in events
            if event.get("source_id") == source_id
        ]
        partials = [
            event
            for event in source_events
            if event.get("type") == "transcript.partial"
            and event.get("text")
        ]
        finals = [
            event
            for event in source_events
            if event.get("type") == "transcript.final"
            and event.get("text")
        ]
        by_source[case["voice"]] = {
            "source_id": source_id,
            "partial_count": len(partials),
            "final_count": len(finals),
            "first_partial_seconds": (
                round(partials[0]["observed_at"] - started_at, 3)
                if partials
                else None
            ),
            "first_final_seconds": (
                round(finals[0]["observed_at"] - started_at, 3)
                if finals
                else None
            ),
            "final_texts": [event.get("text", "") for event in finals],
        }

    all_sources_have_partial = all(
        item["partial_count"] > 0 for item in by_source.values()
    )
    all_sources_have_final = all(
        item["final_count"] > 0 for item in by_source.values()
    )
    return {
        "status": (
            "passed"
            if all_sources_have_partial and all_sources_have_final
            else "failed"
        ),
        "mics": args.mics,
        "schedule": args.schedule,
        "connection_warmup": args.connection_warmup,
        "finalization_wait": args.finalization_wait,
        "all_sources_have_partial": all_sources_have_partial,
        "all_sources_have_final": all_sources_have_final,
        "sources": by_source,
        "event_count": len(events),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if PROBE_ROOT != PROJECT_ROOT:
        raise RuntimeError("Probe and regression project roots differ")
    truth = load_transcript_truth(PROJECT_ROOT / "audio" / "truth_1.csv")
    base_url = args.backend_url.rstrip("/")
    log_path = PROJECT_ROOT / "output" / "concurrent-streaming-demo.log"
    demo_process: subprocess.Popen | None = None
    if args.start_demo:
        demo_process = _start_demo(log_path)
    try:
        with httpx.Client(timeout=15) as client:
            _wait_for_services(client, base_url, timeout=args.start_timeout)
            # Use the real meeting-create lifecycle so every regression starts
            # with a clean adaptive dictionary/topic window. Calling join only
            # would retain terms from a previous meeting and make ASR results
            # depend on test order.
            created = client.post(
                f"{base_url}/api/meeting/create",
                json={"host_name": "P0-07 Regression"},
            )
            created.raise_for_status()
            client.delete(f"{base_url}/api/transcripts").raise_for_status()
        selected_truth = truth[:args.mics]
        report = asyncio.run(_run_probe(args))
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{base_url}/api/transcripts")
            response.raise_for_status()
            stored = response.json().get("items", [])
            scheduler = client.get("http://127.0.0.1:8001/health/ready")
            scheduler.raise_for_status()
        report["stored_final_count"] = len(stored)
        report["meeting_dictionary_reset"] = True
        report["quality"] = _evaluate_stored_finals(stored, selected_truth)
        # Keep the locked offline benchmark as diagnostic evidence. A
        # sequential LiveKit report is the acceptance control for this
        # streaming harness because it includes VAD and turn finalization.
        report["offline_baseline"] = _baseline_for_truth(selected_truth)
        if args.reference_report:
            control = _streaming_control_for_truth(
                args.reference_report,
                selected_truth,
            )
            tolerance = 0.01
            report["streaming_control"] = control
            report["quality"]["within_streaming_tolerance"] = (
                report["quality"]["all_sources_have_final"]
                and report["quality"]["mean_wer"]
                <= control["mean_wer"] + tolerance
                and report["quality"]["mean_cer"]
                <= control["mean_cer"] + tolerance
            )
        report["zipformer_scheduler"] = scheduler.json().get(
            "zipformer_scheduler", {}
        )
        report["demo_log"] = str(log_path) if args.start_demo else None
        return report
    finally:
        if demo_process is not None and not args.keep_demo:
            _stop_demo(demo_process)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mics", type=int, choices=(2, 3, 4), default=4)
    parser.add_argument(
        "--schedule",
        choices=("simultaneous", "sequential"),
        default="simultaneous",
        help="simultaneous is the target; sequential is its streaming control",
    )
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--start-demo", action="store_true")
    parser.add_argument("--keep-demo", action="store_true")
    parser.add_argument("--start-timeout", type=float, default=300)
    parser.add_argument("--connection-warmup", type=float, default=15)
    parser.add_argument(
        "--decoder-pre-roll",
        type=float,
        default=5.0,
        help="Silence sent before measured speech while allocating ASR.",
    )
    parser.add_argument("--finalization-wait", type=float, default=30)
    parser.add_argument(
        "--reference-report",
        type=Path,
        help="sequential report used as a same-path streaming control",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
