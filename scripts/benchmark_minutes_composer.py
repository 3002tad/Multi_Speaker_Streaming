"""Benchmark the evidence-backed Minutes Composer with a local Ollama model.

This exercises the exact incremental-composition path used by the API, but
does not start the meeting backend or touch the meeting SQLite database.

Example (from WSL):
    venv_linux/bin/python -B scripts/benchmark_minutes_composer.py \
        --model qwen2.5:3b --threads 12 --output output/minutes-qwen25-3b.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any


SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PROJECT_ROOT))

from backend.config import settings
from backend.minutes_composer import (
    MinutesCompositionError,
    OllamaMinutesComposer,
)


MEETING_TITLE = "Triển khai phần mềm AI của VNPT cho các cơ quan doanh nghiệp"

# The facts are deliberately explicit.  This is a semantic/grounding test of
# the minutes model, not an ASR benchmark.  The real ASR pipeline is evaluated
# separately using audio/truth.csv.
BATCHES: tuple[list[dict[str, Any]], ...] = (
    [
        {
            "segment_id": "seg-001",
            "speaker": "Chị Lan",
            "start_time": 0.0,
            "end_time": 11.0,
            "text": (
                "Mục tiêu cuộc họp là thống nhất phạm vi thí điểm phần mềm AI "
                "của VNPT cho ba cơ quan doanh nghiệp trong quý ba."
            ),
        },
        {
            "segment_id": "seg-002",
            "speaker": "Anh Minh",
            "start_time": 11.2,
            "end_time": 24.0,
            "text": (
                "Tôi đề xuất thí điểm cổng trợ lý AI tại ba đơn vị, ưu tiên "
                "tra cứu văn bản và tổng hợp báo cáo nội bộ."
            ),
        },
    ],
    [
        {
            "segment_id": "seg-003",
            "speaker": "Chị Lan",
            "start_time": 24.2,
            "end_time": 38.0,
            "text": (
                "Cuộc họp thống nhất chọn phương án thí điểm cổng trợ lý AI. "
                "Anh Minh phụ trách lập kế hoạch chi tiết và gửi trước ngày 15 tháng 8."
            ),
        },
        {
            "segment_id": "seg-004",
            "speaker": "Chị Hoa",
            "start_time": 38.2,
            "end_time": 48.0,
            "text": (
                "Tôi sẽ chuẩn bị danh sách ba đơn vị tham gia thí điểm trước "
                "ngày 10 tháng 8 để Anh Minh hoàn thiện kế hoạch."
            ),
        },
    ],
)


def _source_ids(document: dict[str, Any]) -> list[str]:
    result: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            ids = value.get("source_segment_ids")
            if isinstance(ids, list):
                result.extend(item for item in ids if isinstance(item, str))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return result


def _semantic_checks(document: dict[str, Any]) -> dict[str, bool]:
    serialized = json.dumps(document, ensure_ascii=False).casefold()
    actions = [
        action
        for topic in document.get("topics", [])
        if isinstance(topic, dict)
        for action in topic.get("actions", [])
        if isinstance(action, dict)
    ]
    return {
        "mentions_pilot": "thí điểm" in serialized,
        "has_decision": bool(
            [
                item
                for topic in document.get("topics", [])
                if isinstance(topic, dict)
                for item in topic.get("decisions", [])
            ]
        ),
        "has_action": bool(actions),
        "has_owner": any(action.get("owner") for action in actions),
        "has_deadline": any(action.get("deadline") for action in actions),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    runtime = replace(
        settings,
        minutes_composer_enabled=True,
        minutes_composer_model=args.model,
        minutes_composer_timeout_seconds=args.timeout,
        minutes_composer_num_threads=args.threads,
        minutes_composer_temperature=args.temperature,
        minutes_composer_max_output_tokens=args.max_output_tokens,
        minutes_composer_max_context_chars=args.max_context_chars,
        minutes_composer_context_window=args.context_window,
        minutes_composer_keep_alive=args.keep_alive,
    )
    composer = OllamaMinutesComposer(runtime)
    document: dict[str, Any] | None = None
    processed_ids: list[str] = []
    updates: list[dict[str, Any]] = []
    all_segments = [segment for batch in BATCHES for segment in batch]
    batches = [
        all_segments[start : start + args.batch_size]
        for start in range(0, len(all_segments), args.batch_size)
    ]

    for index, batch in enumerate(batches, start=1):
        try:
            document, metadata = await composer.compose(
                meeting_title=MEETING_TITLE,
                existing_document=document,
                segments=batch,
                started_at=0.0,
            )
        except MinutesCompositionError as exc:
            updates.append({"batch": index, "status": "error", "error": str(exc)})
            break
        processed_ids.extend(item["segment_id"] for item in batch)
        used_ids = _source_ids(document)
        updates.append(
            {
                "batch": index,
                "status": "ok",
                "metadata": metadata,
                "document": document,
                "checks": {
                    "only_valid_source_ids": all(
                        source_id in processed_ids for source_id in used_ids
                    ),
                    "all_processed_ids_marked": all(
                        source_id in document["source_segment_ids"]
                        for source_id in processed_ids
                    ),
                    **_semantic_checks(document),
                },
            }
        )

    final_document = document or {}
    return {
        "model": args.model,
        "runtime": {
            "threads": args.threads,
            "temperature": args.temperature,
            "batch_size": args.batch_size,
            "timeout_seconds": args.timeout,
            "max_output_tokens": args.max_output_tokens,
            "max_context_chars": args.max_context_chars,
            "context_window": args.context_window,
        },
        "meeting_title": MEETING_TITLE,
        "updates": updates,
        "final_document": final_document,
        "final_checks": {
            "completed_all_batches": len(updates) == len(batches)
            and all(item["status"] == "ok" for item in updates),
            "all_sources_preserved": set(processed_ids)
            <= set(final_document.get("source_segment_ids", [])),
            **_semantic_checks(final_document),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--batch-size",
        type=int,
        choices=(1, 2, 3, 4),
        default=2,
        help="How many final transcript segments share one Qwen request.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-output-tokens", type=int, default=120)
    parser.add_argument("--max-context-chars", type=int, default=3200)
    parser.add_argument("--context-window", type=int, default=2048)
    parser.add_argument("--keep-alive", default="15m")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if not report["final_checks"]["completed_all_batches"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
