"""Job transport: long-poll on GET /etch/job, runs in parallel with no queue, the cap, and the timing event."""

from __future__ import annotations

import http.client
import json
import subprocess
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
    def setUp(self):
        etch_public.RUNNING.update(full=0, fast=0)

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

    def test_fast_job_runs_alongside_full_builds(self):
        etch_public.JOBS.clear()
        full = etch_public.Job([PY, "-c", SLOW], "ip", "design", "slow")
        fast = etch_public.Job([PY, "-c", DONE], "ip", "design", "quick")
        fast.lane = "fast"
        self.assertTrue(etch_public.reserve("full"))
        self.assertTrue(etch_public.reserve("fast"))
        with mock.patch.object(etch_public.CLASSIFIER, "classify", return_value=None):
            tf = threading.Thread(target=etch_public.worker, args=(full,), daemon=True)
            tq = threading.Thread(target=etch_public.worker, args=(fast,), daemon=True)
            tf.start()
            tq.start()
            tq.join(20)
        self.assertTrue(fast.done)
        self.assertEqual(fast.state, "done")
        self.assertFalse(full.done)
        self.assertEqual(full.state, "running")
        tm = [e for e in fast.events if e.get("type") == "timing"]
        self.assertEqual(len(tm), 1)
        self.assertEqual(tm[0]["lane"], "fast")
        self.assertIsNotNone(tm[0]["spawn_to_first_event_s"])
        self.assertGreaterEqual(tm[0]["total_s"], tm[0]["run_s"])
        tf.join(20)
        self.assertTrue(full.done)
        self.assertEqual(full.state, "done")
        self.assertEqual(etch_public.RUNNING, {"full": 0, "fast": 0})


SLOW = "import json, time; time.sleep(1.5); print(json.dumps({'agent': 'lead', 'type': 'done', 'ok': True}))"


class NoQueue(unittest.TestCase):
    """Every POST /etch/live starts its own engine process at once; above ETCH_MAX_RUNS a clear 503, not a line."""

    @classmethod
    def setUpClass(cls):
        cls.srv = etch_public.ThreadingHTTPServer(("127.0.0.1", 0), etch_public.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        etch_public.JOBS.clear()
        etch_public.LIM.hits.clear()
        etch_public.RUNNING.update(full=0, fast=0)
        real = subprocess.Popen
        # the engine command is swapped for a 1.5 s stand-in; everything else (process group, pipes) is real
        self.popen = mock.patch.object(
            etch_public.subprocess, "Popen", side_effect=lambda args, **kw: real([PY, "-c", SLOW], **kw)
        )
        for p in (
            self.popen,
            mock.patch.object(etch_public, "lane_for", return_value="full"),
            mock.patch.object(etch_public.CLASSIFIER, "classify", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def call(self, method: str, path: str, body: dict | None = None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=30)
        c.request(method, path, body=json.dumps(body) if body is not None else None,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def start(self, text: str):
        return self.call("POST", "/etch/live", {"mode": "design", "text": text})

    def wait_all(self, jobs, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout and not all(etch_public.JOBS[j].done for j in jobs):
            time.sleep(0.05)

    def test_three_runs_start_at_once(self):
        t0 = time.time()
        jobs = []
        for text in ("a led blinker", "night light that turns on when dark", "a thermometer with an oled"):
            code, res = self.start(text)
            self.assertEqual(code, 200, res)
            jobs.append(res["job"])
        time.sleep(0.4)
        self.assertEqual([etch_public.JOBS[j].state for j in jobs], ["running"] * 3)
        self.assertTrue(all(etch_public.JOBS[j].proc is not None for j in jobs))
        _, h = self.call("GET", "/etch/health")
        self.assertEqual((h["running"], h["queue"], h["busy"]), (3, 0, False))
        self.wait_all(jobs)
        wall = time.time() - t0
        self.assertEqual([etch_public.JOBS[j].state for j in jobs], ["done"] * 3)
        self.assertLess(wall, 3 * 1.5)  # in parallel: well under three sequential runs
        self.assertFalse(any(e.get("type") == "queued" for j in jobs for e in etch_public.JOBS[j].events))
        _, h = self.call("GET", "/etch/health")
        self.assertEqual(h["running"], 0)
        for k in ("busy", "queue", "fast_busy", "fast_queue", "stt", "max_runs", "heavy_slots", "heavy_in_use"):
            self.assertIn(k, h)

    def test_cap_is_a_503_not_a_queue(self):
        with mock.patch.object(etch_public, "MAX_RUNS", 2):
            ok = [self.start(f"board {i}") for i in range(2)]
            self.assertEqual([c for c, _ in ok], [200, 200])
            code, res = self.start("one too many")
            self.assertEqual(code, 503)
            self.assertIn("2 builds are running", res["error"])
            _, h = self.call("GET", "/etch/health")
            self.assertTrue(h["busy"])
            self.assertEqual(h["queue"], 0)
            self.wait_all([r["job"] for _, r in ok])
            code, res = self.start("room again")
            self.assertEqual(code, 200, res)
            self.wait_all([res["job"]])
        self.assertEqual(etch_public.RUNNING["full"], 0)

    def test_cancel_still_stops_its_own_group(self):
        code, res = self.start("cancel me")
        self.assertEqual(code, 200)
        time.sleep(0.3)
        code, out = self.call("POST", "/etch/cancel", {"job": res["job"], "token": res["token"]})
        self.assertEqual(code, 200, out)
        self.wait_all([res["job"]], 15)
        self.assertEqual(etch_public.JOBS[res["job"]].state, "cancelled")
        self.assertEqual(etch_public.RUNNING["full"], 0)


if __name__ == "__main__":
    unittest.main()
