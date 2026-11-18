from __future__ import annotations

import os
import threading
import time
import unittest

from comfy_updater.download_queue import (
    KEEP_FINISHED,
    MB,
    DownloadQueue,
    DownloadRequest,
    run_queue,
)
from comfy_updater.model_download import DownloadStopped


def _request(key, **fields):
    return DownloadRequest(
        key=key,
        source="updates",
        model_id="101",
        version_id=key,
        model_type="lora",
        **fields,
    )


class _Control:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled


def _noop_progress(current, total, message):
    pass


def _wait_for(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.005)
    return False


class DownloadQueueTests(unittest.TestCase):
    def test_requests_run_one_at_a_time_in_order(self):
        queue = DownloadQueue()
        a, first = queue.submit(_request("a"))
        b, second = queue.submit(_request("b"))
        c, third = queue.submit(_request("c"))
        self.assertEqual((first, second, third), (True, False, False))
        order = []
        running = []

        def work(request, report, should_stop):
            running.append(request.key)
            self.assertEqual(len(running), 1)
            order.append(request.key)
            running.pop()

        summary, items = run_queue(queue, work, progress=_noop_progress, control=_Control())
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual([r.state for r in (a, b, c)], ["done"] * 3)
        self.assertEqual(summary, {"downloaded": 3, "failed": 0, "stopped": 0})

    def test_the_same_release_twice_is_one_request(self):
        queue = DownloadQueue()
        a, first = queue.submit(_request("a"))
        again, second = queue.submit(_request("a"))
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(a.id, again.id)
        self.assertEqual(len(queue.snapshot()), 1)

    def test_after_the_queue_drains_the_next_click_needs_a_new_job(self):
        queue = DownloadQueue()
        queue.submit(_request("a"))
        run_queue(queue, lambda r, rep, stop: None, progress=_noop_progress, control=_Control())
        again, needs_job = queue.submit(_request("a"))
        self.assertTrue(needs_job)
        self.assertEqual(again.state, "waiting")

    def test_cancelling_a_waiting_request_drops_it(self):
        queue = DownloadQueue()
        queue.submit(_request("a"))
        b, _ = queue.submit(_request("b"))
        stopped = queue.stop(b.id)
        self.assertEqual(stopped.state, "stopped")
        seen = []

        def work(request, report, should_stop):
            seen.append(request.key)

        summary, _ = run_queue(queue, work, progress=_noop_progress, control=_Control())
        self.assertEqual(seen, ["a"])
        self.assertEqual(b.state, "stopped")
        self.assertEqual(summary["downloaded"], 1)

    def test_stopping_the_running_request_moves_on(self):
        queue = DownloadQueue()
        a, _ = queue.submit(_request("a"))
        b, _ = queue.submit(_request("b"))

        def work(request, report, should_stop):
            if request.key == "a":
                deadline = time.time() + 5
                while not should_stop():
                    if time.time() > deadline:
                        raise RuntimeError("never stopped")
                    time.sleep(0.005)
                raise DownloadStopped()

        def stopper():
            if _wait_for(lambda: a.state == "downloading"):
                queue.stop(a.id)

        thread = threading.Thread(target=stopper, daemon=True)
        thread.start()
        summary, _ = run_queue(queue, work, progress=_noop_progress, control=_Control())
        thread.join(5)
        self.assertEqual(a.state, "stopped")
        self.assertEqual(b.state, "done")
        self.assertEqual(summary, {"downloaded": 1, "failed": 0, "stopped": 1})

    def test_stopping_the_job_stops_every_waiting_request(self):
        queue = DownloadQueue()
        a, _ = queue.submit(_request("a"))
        b, _ = queue.submit(_request("b"))
        c, _ = queue.submit(_request("c"))
        control = _Control()

        def work(request, report, should_stop):
            control.cancel()
            if should_stop():
                raise DownloadStopped()

        summary, _ = run_queue(queue, work, progress=_noop_progress, control=control)
        self.assertEqual([r.state for r in (a, b, c)], ["stopped"] * 3)
        self.assertEqual(summary, {"downloaded": 0, "failed": 0, "stopped": 3})
        _, needs_job = queue.submit(_request("d"))
        self.assertTrue(needs_job)

    def test_a_failure_is_recorded_and_the_next_one_runs(self):
        queue = DownloadQueue()
        a, _ = queue.submit(_request("a"))
        b, _ = queue.submit(_request("b"))

        def work(request, report, should_stop):
            if request.key == "a":
                raise RuntimeError("boom")

        summary, _ = run_queue(queue, work, progress=_noop_progress, control=_Control())
        self.assertEqual(a.state, "error")
        self.assertEqual(a.error, "boom")
        self.assertEqual(b.state, "done")
        self.assertEqual(summary, {"downloaded": 1, "failed": 1, "stopped": 0})

    def test_a_long_error_is_cut(self):
        queue = DownloadQueue()
        a, _ = queue.submit(_request("a"))

        def work(request, report, should_stop):
            raise RuntimeError("x" * 1000)

        run_queue(queue, work, progress=_noop_progress, control=_Control())
        self.assertEqual(len(a.error), 300)

    def test_the_job_shows_megabytes_and_how_many_wait(self):
        queue = DownloadQueue()
        queue.submit(_request("a"))
        queue.submit(_request("b"))
        calls = []

        def work(request, report, should_stop):
            if request.key == "a":
                report(5 * MB, 10 * MB, "Downloading glowV2.safetensors")
                self.assertEqual((request.written, request.total), (5 * MB, 10 * MB))

        run_queue(queue, work, progress=lambda *args: calls.append(args), control=_Control())
        self.assertEqual(calls, [(5, 10, "Downloading glowV2.safetensors \u00b7 1 waiting")])

    def test_holds_names_the_files_a_replace_will_remove(self):
        queue = DownloadQueue()
        path = os.path.join("models", "loras", "glow.safetensors")
        request, _ = queue.submit(_request("a", replace_path=path))
        self.assertTrue(queue.holds(os.path.join("models", "loras", ".", "glow.safetensors")))
        self.assertTrue(queue.holds(path.upper() if os.name == "nt" else path))
        self.assertFalse(queue.holds(os.path.join("models", "loras", "other.safetensors")))
        self.assertFalse(queue.holds(""))
        queue.take_next()
        self.assertTrue(queue.holds(path))
        queue.finish(request, "done")
        self.assertFalse(queue.holds(path))

    def test_snapshot_keeps_the_last_fifty_finished(self):
        queue = DownloadQueue()
        for index in range(KEEP_FINISHED + 5):
            request, _ = queue.submit(_request(f"r{index}"))
            queue.take_next()
            queue.finish(request, "done")
        waiting, _ = queue.submit(_request("waiting"))
        snapshot = queue.snapshot()
        self.assertEqual(len(snapshot), KEEP_FINISHED + 1)
        self.assertEqual(snapshot[0]["key"], "r5")
        self.assertEqual(snapshot[-1]["id"], waiting.id)

    def test_as_dict_carries_no_paths(self):
        request = _request(
            "a",
            name="glow",
            reference_path="C:/secret/ref.safetensors",
            replace_path="C:/secret/old.safetensors",
            check_item={"modelPath": "C:/secret/ref.safetensors"},
        )
        data = request.as_dict()
        self.assertEqual(
            set(data),
            {
                "id",
                "key",
                "source",
                "modelId",
                "versionId",
                "name",
                "replace",
                "state",
                "written",
                "total",
                "fileName",
                "error",
                "warnings",
            },
        )
        self.assertIs(data["replace"], True)
        self.assertNotIn("secret", repr(data))

    def test_abandon_marks_the_request_failed_and_the_queue_idle(self):
        queue = DownloadQueue()
        request, needs_job = queue.submit(_request("a"))
        self.assertTrue(needs_job)
        queue.abandon(request, "no job")
        self.assertEqual((request.state, request.error), ("error", "no job"))
        _, needs_job = queue.submit(_request("b"))
        self.assertTrue(needs_job)

    def test_an_emptied_queue_goes_idle_in_take_next(self):
        queue = DownloadQueue()
        queue.submit(_request("a"))
        self.assertEqual(queue.take_next().key, "a")
        self.assertIsNone(queue.take_next())
        _, needs_job = queue.submit(_request("b"))
        self.assertTrue(needs_job)
        self.assertEqual(queue.waiting(), 1)


if __name__ == "__main__":
    unittest.main()
