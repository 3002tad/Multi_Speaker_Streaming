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
            await asyncio.wait_for(self.scheduler.run(fail), timeout=1)
        self.assertEqual(
            await asyncio.wait_for(
                self.scheduler.run(lambda: "recovered"), timeout=1
            ),
            "recovered",
        )

    async def test_round_robins_backlogged_microphones(self):
        """A follow-up from mic A must yield to mic B after A's first turn."""
        order = []
        first_started = threading.Event()
        release_first = threading.Event()

        def operation(label: str, *, wait: bool = False) -> str:
            if wait:
                first_started.set()
                release_first.wait(timeout=1)
            order.append(label)
            return label

        first = asyncio.create_task(
            self.scheduler.run(
                lambda: operation("a-1", wait=True),
                stream_key="mic-a",
            )
        )
        await asyncio.to_thread(first_started.wait, 1)
        queued = [
            asyncio.create_task(
                self.scheduler.run(
                    lambda: operation("a-2"), stream_key="mic-a"
                )
            ),
            asyncio.create_task(
                self.scheduler.run(
                    lambda: operation("b-1"), stream_key="mic-b"
                )
            ),
            asyncio.create_task(
                self.scheduler.run(
                    lambda: operation("b-2"), stream_key="mic-b"
                )
            ),
        ]
        await asyncio.sleep(0)
        release_first.set()
        self.assertEqual(
            await asyncio.gather(first, *queued),
            ["a-1", "a-2", "b-1", "b-2"],
        )
        self.assertEqual(order, ["a-1", "b-1", "a-2", "b-2"])
        telemetry = self.scheduler.telemetry()
        self.assertEqual(telemetry["submitted"], 4)
        self.assertEqual(telemetry["completed"], 4)
        self.assertEqual(telemetry["pending"], 0)
        self.assertIn("max_queue_wait_ms", telemetry)
        self.assertIn("mean_operation_ms", telemetry)
        self.assertIn("max_operation_ms", telemetry)


if __name__ == "__main__":
    unittest.main()
