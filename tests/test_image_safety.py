import asyncio
import threading
import time
import unittest

from gardenops.services.image_safety import process_image_in_thread


class TestImageProcessingThread(unittest.IsolatedAsyncioTestCase):
    async def test_work_is_offloaded_and_bounded(self) -> None:
        lock = threading.Lock()
        active = 0
        peak = 0
        caller_thread = threading.get_ident()

        def process() -> int:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return threading.get_ident()

        worker_threads = await asyncio.gather(*(process_image_in_thread(process) for _ in range(4)))
        self.assertTrue(all(thread_id != caller_thread for thread_id in worker_threads))
        self.assertLessEqual(peak, 2)
