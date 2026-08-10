"""Fair, async-safe scheduling for the shared Zipformer recognizer.

Sherpa-ONNX online streams are independent, but their recognizer/model object
is shared and must be called from one worker thread.  A plain
``ThreadPoolExecutor(max_workers=1)`` happens to use FIFO submission order;
when a busy microphone immediately submits another decode, that can let its
backlog dominate the other microphones.  This scheduler keeps serialization,
but dispatches one operation per stream key in round-robin order.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Deque, TypeVar


T = TypeVar("T")


@dataclass
class _ScheduledOperation:
    operation: Callable[[], object]
    future: asyncio.Future[object]
    submitted_at: float


class ZipformerDecodeScheduler:
    """Serialize recognizer work fairly without blocking the asyncio loop.

    Calls sharing a ``stream_key`` remain strictly ordered.  Across keys,
    dispatch is round-robin, so a temporarily backlogged mic cannot starve a
    second active mic.  The recognizer is still invoked by exactly one worker
    thread; this is a scheduling change, not parallel model access.
    """

    _DEFAULT_STREAM_KEY = "__shared__"

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="zipformer-decode",
        )
        self._pending: dict[str, Deque[_ScheduledOperation]] = defaultdict(deque)
        self._ready_keys: asyncio.Queue[str] = asyncio.Queue()
        self._queued_keys: set[str] = set()
        self._active_key: str | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._closed = False
        self._submitted = 0
        self._completed = 0
        self._maximum_pending = 0
        self._maximum_wait_seconds = 0.0
        self._maximum_queue_wait_seconds = 0.0
        self._total_operation_seconds = 0.0
        self._maximum_operation_seconds = 0.0

    async def run(
        self,
        operation: Callable[[], T],
        *,
        stream_key: str | None = None,
    ) -> T:
        """Queue ``operation`` and return its result after its fair turn."""
        if self._closed:
            raise RuntimeError("Zipformer decode scheduler is closed")

        loop = asyncio.get_running_loop()
        key = str(stream_key or self._DEFAULT_STREAM_KEY)
        future: asyncio.Future[object] = loop.create_future()
        self._pending[key].append(
            _ScheduledOperation(
                operation=operation,
                future=future,
                submitted_at=loop.time(),
            )
        )
        self._submitted += 1
        pending_count = sum(len(items) for items in self._pending.values())
        self._maximum_pending = max(self._maximum_pending, pending_count)
        # Do not put the currently executing stream back in the ready deque
        # yet.  Its queued follow-up must yield to peers that arrive while its
        # current decode is running.
        if key not in self._queued_keys and key != self._active_key:
            self._ready_keys.put_nowait(key)
            self._queued_keys.add(key)
        self._ensure_dispatcher()
        return await future  # type: ignore[return-value]

    def telemetry(self) -> dict[str, int | float]:
        """Return lightweight scheduler pressure data for streaming probes."""
        return {
            "submitted": self._submitted,
            "completed": self._completed,
            "pending": sum(len(items) for items in self._pending.values()),
            "active_streams": len(self._pending),
            "max_pending": self._maximum_pending,
            "max_wait_ms": round(self._maximum_wait_seconds * 1000, 1),
            "max_queue_wait_ms": round(
                self._maximum_queue_wait_seconds * 1000, 1
            ),
            "mean_operation_ms": round(
                self._total_operation_seconds
                * 1000
                / max(self._completed, 1),
                1,
            ),
            "max_operation_ms": round(
                self._maximum_operation_seconds * 1000, 1
            ),
        }

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher_task is None or self._dispatcher_task.done():
            self._dispatcher_task = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._closed:
            key = await self._ready_keys.get()
            self._queued_keys.discard(key)
            queue = self._pending.get(key)
            if not queue:
                self._pending.pop(key, None)
                self._ready_keys.task_done()
                continue
            item = queue.popleft()
            if item.future.cancelled():
                if self._pending.get(key):
                    self._ready_keys.put_nowait(key)
                    self._queued_keys.add(key)
                else:
                    self._pending.pop(key, None)
                self._ready_keys.task_done()
                continue
            self._active_key = key
            operation_started_at = loop.time()
            self._maximum_queue_wait_seconds = max(
                self._maximum_queue_wait_seconds,
                operation_started_at - item.submitted_at,
            )
            try:
                result = await loop.run_in_executor(self._executor, item.operation)
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.set_exception(asyncio.CancelledError())
                raise
            except Exception as exc:
                if not item.future.done():
                    # Do not retain the worker coroutine's traceback on the
                    # client future. It can otherwise retain/cancel an
                    # unrelated dispatcher task when the caller handles a
                    # decode exception.
                    item.future.set_exception(exc.with_traceback(None))
            else:
                operation_seconds = loop.time() - operation_started_at
                self._total_operation_seconds += operation_seconds
                self._maximum_operation_seconds = max(
                    self._maximum_operation_seconds,
                    operation_seconds,
                )
                self._maximum_wait_seconds = max(
                    self._maximum_wait_seconds,
                    loop.time() - item.submitted_at,
                )
                if not item.future.done():
                    item.future.set_result(result)
            finally:
                self._completed += 1
                self._active_key = None
                if self._pending.get(key):
                    # Re-append only after the operation ends. A stream that
                    # submitted follow-up work while active therefore yields
                    # to any peer stream that arrived in the same interval.
                    if key not in self._queued_keys:
                        self._ready_keys.put_nowait(key)
                        self._queued_keys.add(key)
                else:
                    self._pending.pop(key, None)
                self._ready_keys.task_done()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for queue in self._pending.values():
            while queue:
                item = queue.popleft()
                if not item.future.done():
                    item.future.set_exception(
                        RuntimeError("Zipformer decode scheduler is closed")
                    )
        self._pending.clear()
        while not self._ready_keys.empty():
            self._ready_keys.get_nowait()
            self._ready_keys.task_done()
        self._queued_keys.clear()
        self._active_key = None
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            await asyncio.gather(self._dispatcher_task, return_exceptions=True)
        # Waiting for the one active recognizer call must not freeze FastAPI's
        # shutdown loop or prevent the other services from handling SIGTERM.
        await asyncio.to_thread(
            self._executor.shutdown,
            wait=True,
            cancel_futures=True,
        )
