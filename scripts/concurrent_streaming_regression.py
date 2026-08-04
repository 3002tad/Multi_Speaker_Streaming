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

from backend.evaluation import load_transcript_truth
from scripts.streaming_regression import (
    _start_demo,
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


async def _run_probe(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.mics)
    events: list[dict[str, Any]] = []
    identities: dict[str, str] = {}
    connected = asyncio.Event()
    stop = asyncio.Event()
    collector = asyncio.create_task(
        _collect_events(events, connected, stop)
    )
    await asyncio.wait_for(connected.wait(), timeout=10)

    start_audio = asyncio.Event()
    tasks = [
        asyncio.create_task(
            publish_source(
                case["name"],
                case["audio"],
                start_audio,
                identity_sink=identities,
                finalization_wait_seconds=args.finalization_wait,
            )
        )
        for case in cases
    ]
    await asyncio.sleep(args.connection_warmup)
    started_at = time.time()
    start_audio.set()
    await asyncio.gather(*tasks)
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
    base_url = args.backend_url.rstrip("/")
    log_path = PROJECT_ROOT / "output" / "concurrent-streaming-demo.log"
    demo_process: subprocess.Popen | None = None
    if args.start_demo:
        demo_process = _start_demo(log_path)
    try:
        with httpx.Client(timeout=15) as client:
            _wait_for_services(client, base_url, timeout=args.start_timeout)
            client.delete(f"{base_url}/api/transcripts").raise_for_status()
        report = asyncio.run(_run_probe(args))
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{base_url}/api/transcripts")
            response.raise_for_status()
            stored = response.json().get("items", [])
        report["stored_final_count"] = len(stored)
        report["demo_log"] = str(log_path) if args.start_demo else None
        return report
    finally:
        if demo_process is not None and not args.keep_demo:
            _stop_demo(demo_process)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mics", type=int, choices=(2, 3, 4), default=4)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--start-demo", action="store_true")
    parser.add_argument("--keep-demo", action="store_true")
    parser.add_argument("--start-timeout", type=float, default=300)
    parser.add_argument("--connection-warmup", type=float, default=15)
    parser.add_argument("--finalization-wait", type=float, default=30)
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
