import asyncio
import threading
import time
import unittest

from backend.asr_scheduler import ZipformerDecodeScheduler


class ZipformerDecodeSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.scheduler = ZipformerDecodeScheduler()

    async def asyncTearDown(self) -> None:
        await self.scheduler.close()

    async def test_serializes_recognizer_operations_in_submission_order(self):
        active = 0
        maximum_active = 0
        order = []
        state_lock = threading.Lock()

        def operation(value: int) -> int:
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            order.append(value)
            with state_lock:
                active -= 1
            return value

        tasks = [
            asyncio.create_task(
                self.scheduler.run(lambda value=value: operation(value))
            )
            for value in range(4)
        ]

        self.assertEqual(await asyncio.gather(*tasks), [0, 1, 2, 3])
        self.assertEqual(order, [0, 1, 2, 3])
        self.assertEqual(maximum_active, 1)

    async def test_slow_decode_does_not_block_asyncio_event_loop(self):
        started = threading.Event()
        release = threading.Event()
        heartbeat_seen = asyncio.Event()

        def slow_decode() -> str:
            started.set()
            release.wait(timeout=2)
            return "done"

        decode_task = asyncio.create_task(self.scheduler.run(slow_decode))
        await asyncio.to_thread(started.wait, 1)

        async def heartbeat() -> None:
            await asyncio.sleep(0.01)
            heartbeat_seen.set()

        heartbeat_task = asyncio.create_task(heartbeat())
        await asyncio.wait_for(heartbeat_seen.wait(), timeout=0.2)
        release.set()

        self.assertEqual(await decode_task, "done")
        await heartbeat_task

    async def test_failed_operation_does_not_stop_following_decode(self):
        def fail() -> None:
            raise ValueError("bad stream")

        with self.assertRaisesRegex(ValueError, "bad stream"):
            await self.scheduler.run(fail)
        self.assertEqual(await self.scheduler.run(lambda: "recovered"), "recovered")


if __name__ == "__main__":
    unittest.main()
