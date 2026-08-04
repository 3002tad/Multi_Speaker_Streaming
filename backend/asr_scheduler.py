"""Async-safe serialization for the shared Zipformer recognizer.

Sherpa-ONNX exposes independent online streams, but the recognizer/model
object behind those streams is shared.  All recognizer calls therefore run on
one dedicated worker thread.  Awaiting a submitted operation yields to the
asyncio event loop instead of blocking every microphone connection on a
``threading.Lock``.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar


T = TypeVar("T")


class ZipformerDecodeScheduler:
    """Run shared-recognizer operations sequentially off the event loop."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="zipformer-decode",
        )
        self._closed = False

    async def run(self, operation: Callable[[], T]) -> T:
        if self._closed:
            raise RuntimeError("Zipformer decode scheduler is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, operation)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Waiting for the one active recognizer call must not freeze FastAPI's
        # shutdown loop or prevent the other services from handling SIGTERM.
        await asyncio.to_thread(
            self._executor.shutdown,
            wait=True,
            cancel_futures=True,
        )

