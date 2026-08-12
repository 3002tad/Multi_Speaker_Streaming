"""Operational state kept inside Meeting AI without touching AI decisions."""

from __future__ import annotations

import asyncio
import resource
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized in {"changeme", "replace_me", "replace-before-public-deploy"}
        or normalized.startswith(("replace", "change-me", "local-", "example"))
    )


def validate_secret(name: str, value: str, *, minimum_length: int = 24) -> None:
    if len(value.strip()) < minimum_length or is_placeholder_secret(value):
        raise RuntimeError(
            f"{name} must be a non-placeholder random value of at least "
            f"{minimum_length} characters"
        )


@dataclass
class OperationTracker:
    """Small in-memory operational record for health and shutdown reporting."""

    started_at_monotonic: float = field(default_factory=time.monotonic)
    started_cpu_seconds: float = field(default_factory=time.process_time)
    warmup_status: str = "not-required"
    warmup_error: str | None = None
    warmup_elapsed_ms: int | None = None
    shutdown_status: str = "running"
    shutdown_elapsed_ms: int | None = None

    def start_warmup(self) -> None:
        self.warmup_status = "warming"
        self.warmup_error = None

    def finish_warmup(self, *, status: str, elapsed_ms: int, error: str | None = None) -> None:
        self.warmup_status = status
        self.warmup_elapsed_ms = elapsed_ms
        self.warmup_error = error

    def finish_shutdown(self, elapsed_ms: int) -> None:
        self.shutdown_status = "flushed"
        self.shutdown_elapsed_ms = elapsed_ms

    def snapshot(self) -> dict[str, Any]:
        try:
            # Linux reports KiB; macOS reports bytes. The demo runs on Linux,
            # but normalize defensively for local development/tests.
            rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
            peak_rss_mb = round(rss / divisor, 2)
        except (AttributeError, ValueError, OSError):  # pragma: no cover - platform guard
            peak_rss_mb = None
        return {
            "cold_start_elapsed_ms": round(
                (time.monotonic() - self.started_at_monotonic) * 1000
            ),
            "process_cpu_ms": round(
                (time.process_time() - self.started_cpu_seconds) * 1000
            ),
            "peak_rss_mb": peak_rss_mb,
            "ollama_warmup": {
                "status": self.warmup_status,
                "elapsed_ms": self.warmup_elapsed_ms,
                "error": self.warmup_error,
            },
            "shutdown": {
                "status": self.shutdown_status,
                "elapsed_ms": self.shutdown_elapsed_ms,
            },
        }


async def warm_ollama_model(
    *,
    tracker: OperationTracker,
    base_url: str,
    model: str,
    keep_alive: str | int,
    timeout_seconds: float,
) -> None:
    """Warm one Ollama model in the background; never hold the ASR event loop."""
    started = time.monotonic()
    tracker.start_warmup()
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": keep_alive,
                    "options": {"num_predict": 1},
                },
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        tracker.finish_warmup(
            status="degraded",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error=type(exc).__name__,
        )
        return
    tracker.finish_warmup(
        status="ready",
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )


async def cancel_and_wait(tasks: set[asyncio.Task], timeout_seconds: float) -> int:
    """Give callbacks a bounded chance to finish before cancelling leftovers."""
    pending = tuple(task for task in tasks if not task.done())
    if not pending:
        return 0
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=timeout_seconds
        )
        return 0
    except asyncio.TimeoutError:
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return sum(not task.cancelled() for task in pending)
