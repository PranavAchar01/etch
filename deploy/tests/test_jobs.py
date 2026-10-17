"""Job transport: long-poll on GET /etch/job, the fast lane outside RUN_LOCK, and the server's timing event."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etch_public  # noqa: E402

PY = sys.executable
DONE = "import json; print(json.dumps({'agent': 'lead', 'type': 'done', 'ok': True, 'run': 'live-1234567'}))"


class LongPoll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = etch_public.ThreadingHTTPServer(("127.0.0.1", 0), etch_public.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        etch_public.JOBS.clear()
        self.job = etch_public.Job(["true"], "1.2.3.4", "design", "x")
        etch_public.JOBS[self.job.id] = self.job

    def get(self, q: str):
        c = http.client.HTTPConnection(
            "127.0.0.1", self.srv.server_address[1], timeout=30
        )
        c.request("GET", f"/etch/job?id={self.job.id}&{q}")
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_plain_polling_returns_at_once(self):
        t0 = time.time()
        status, d = self.get("since=0")
        self.assertEqual(status, 200)
        self.assertEqual(d["events"], [])
        self.assertLess(time.time() - t0, 1.0)

    def test_long_poll_wakes_on_the_next_event(self):
        threading.Timer(
            0.3, lambda: self.job.emit({"agent": "lead", "type": "say", "text": "hi"})
        ).start()
        t0 = time.time()
        _, d = self.get("since=0&wait=10")
        took = time.time() - t0
        self.assertEqual([e["type"] for e in d["events"]], ["say"])
        self.assertEqual(d["next"], 1)
        self.assertGreater(took, 0.2)
        self.assertLess(took, 3.0)

    def test_long_poll_times_out_empty(self):
        self.job.emit({"agent": "lead", "type": "say", "text": "old"})
        t0 = time.time()
        _, d = self.get("since=1&wait=0.4")
        self.assertEqual(d["events"], [])
        self.assertGreaterEqual(time.time() - t0, 0.35)

    def test_long_poll_wakes_on_finish(self):
        threading.Timer(0.2, self.job.finish).start()
        _, d = self.get("since=0&wait=10")
        self.assertTrue(d["done"])

    def test_bad_wait_is_400(self):
        status, _ = self.get("since=0&wait=abc")
        self.assertEqual(status, 400)


class Lanes(unittest.TestCase):
    def test_lane_guess(self):
        self.assertEqual(
            etch_public.lane_for("design", "a 555 timer led flasher", None), "fast"
        )
        self.assertEqual(
            etch_public.lane_for(
                "design", "a laptop motherboard with ddr5 and a 16 core cpu", None
            ),
            "full",
        )
        tmp = Path(tempfile.mkdtemp())
        base = {
            "parts": [
                {
                    "ref": "J1",
                    "piece": "kicad:Connector_Generic:Conn_01x02@PinHeader_1x02_P2.54mm_Vertical",
                    "pins": {"1": "VCC", "2": "GND"},
                },
            ],
            "benchmarks": [],
        }
        (tmp / "design.json").write_text(json.dumps(base))
        self.assertEqual(
            etch_public.lane_for("modify", "make the board black", tmp), "fast"
        )
        self.assertEqual(
            etch_public.lane_for("modify", "add a lidar and a gps", tmp), "full"
        )
        self.assertEqual(
            etch_public.lane_for("modify", "make it black", tmp / "nope"), "full"
        )

    def test_fast_job_runs_while_a_full_build_holds_the_lock(self):
        etch_public.JOBS.clear()
        self.assertTrue(etch_public.RUN_LOCK.acquire(timeout=5))
        try:
            full = etch_public.Job([PY, "-c", DONE], "ip", "design", "slow")
            fast = etch_public.Job([PY, "-c", DONE], "ip", "design", "quick")
            fast.lane = "fast"
            with mock.patch.object(
                etch_public.CLASSIFIER, "classify", return_value=None
            ):
                tf = threading.Thread(
                    target=etch_public.worker, args=(full,), daemon=True
                )
                tq = threading.Thread(
                    target=etch_public.worker, args=(fast,), daemon=True
                )
                tf.start()
                tq.start()
                tq.join(20)
            self.assertTrue(fast.done)
            self.assertEqual(fast.state, "done")
            self.assertFalse(full.done)
            self.assertEqual(full.state, "queued")
            tm = [e for e in fast.events if e.get("type") == "timing"]
            self.assertEqual(len(tm), 1)
            self.assertEqual(tm[0]["lane"], "fast")
            self.assertIsNotNone(tm[0]["spawn_to_first_event_s"])
            self.assertGreaterEqual(tm[0]["total_s"], tm[0]["run_s"])
        finally:
            etch_public.RUN_LOCK.release()
        tf.join(20)
        self.assertTrue(full.done)
        self.assertEqual(full.state, "done")


if __name__ == "__main__":
    unittest.main()
