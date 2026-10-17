"""A backend restart must not lose a job (2026-10-17, job gbRVE5w_9kCwfMDt: the backend restarted one second into
a round-badge build, JOBS was memory only, GET /etch/job answered 404 and the page showed "Done in 3m 08s" over an
empty 3D view). Jobs are journalled; restore_jobs() brings them back after a restart."""

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
ASK = "build a Round PCB badge with 60mm circle with a ring of 12 LEDs around the edge, a CR2032 holder on the back and an ATtiny85."


class Restore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = etch_public.ThreadingHTTPServer(("127.0.0.1", 0), etch_public.H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patch = mock.patch.object(etch_public, "JOURNAL", self.dir)
        self.patch.start()
        etch_public.JOBS.clear()

    def tearDown(self):
        self.patch.stop()
        etch_public.JOBS.clear()
        self.tmp.cleanup()

    def get(self, jid: str, since: int = 0):
        c = http.client.HTTPConnection(
            "127.0.0.1", self.srv.server_address[1], timeout=30
        )
        c.request("GET", f"/etch/job?id={jid}&since={since}")
        r = c.getresponse()
        d = json.loads(r.read())
        c.close()
        return r.status, d

    def cut_off(self, created_ago: float = 5.0) -> etch_public.Job:
        """A job the old backend was running when it died: journal written, no end row."""
        j = etch_public.Job(["true"], "1.2.3.4", "design", ASK)
        j.created = time.time() - created_ago
        j.open_journal(self.dir)
        j.emit({"agent": "lead", "type": "queued", "position": 1, "t": 0})
        j.emit(
            {
                "agent": "lead",
                "type": "start",
                "run": "live-1792274101041",
                "ask": ASK,
                "t": 0,
            }
        )
        j.emit(
            {"agent": "picker", "type": "start", "run": "live-1792274101041", "t": 0.7}
        )
        etch_public.JOBS.clear()  # the restart
        return j

    def test_cut_off_job_is_followed_and_started_again(self):
        old = self.cut_off()
        started = []
        resumed = etch_public.restore_jobs(start=started.append)
        self.assertEqual([j.id for j in resumed], [old.id])
        self.assertEqual(started[0].token, old.token)  # the page can still cancel it
        status, d = self.get(
            old.id, since=3
        )  # the page had read 3 events before the restart
        self.assertEqual(status, 200)
        self.assertFalse(d["done"])
        self.assertEqual(d["events"][0]["type"], "say")
        self.assertIn("starting the build again", d["events"][0]["text"])
        self.assertEqual(d["next"], 4)

    def test_resumed_job_runs_to_a_board_under_the_same_id(self):
        old = self.cut_off()
        resumed = etch_public.restore_jobs(start=lambda j: None)
        j = resumed[0]
        done = "import json; print(json.dumps({'agent': 'lead', 'type': 'done', 'ok': True, 'run': 'live-1792274999999'}))"
        j.args = [PY, "-c", done]
        etch_public.worker(j)
        status, d = self.get(old.id, since=3)
        self.assertEqual(status, 200)
        self.assertTrue(d["done"])
        self.assertEqual(d["state"], "done")
        self.assertEqual(d["run"], "live-1792274999999")
        self.assertTrue(
            any(
                e["agent"] == "lead" and e["type"] == "done" and e["ok"]
                for e in d["events"]
            )
        )
        # and a second restart now finds it finished, not cut off
        etch_public.JOBS.clear()
        self.assertEqual(
            etch_public.restore_jobs(
                start=lambda j: self.fail("restarted a finished job")
            ),
            [],
        )
        _, d = self.get(old.id)
        self.assertTrue(d["done"])
        self.assertEqual(d["state"], "done")

    def test_too_old_job_ends_with_an_error_not_done(self):
        old = self.cut_off(created_ago=etch_public.RESUME_WINDOW_S + 60)
        self.assertEqual(
            etch_public.restore_jobs(
                start=lambda j: self.fail("restarted a stale job")
            ),
            [],
        )
        status, d = self.get(old.id)
        self.assertEqual(status, 200)
        self.assertTrue(d["done"])
        self.assertEqual(d["state"], "failed")
        last = d["events"][-1]
        self.assertEqual((last["agent"], last["type"]), ("lead", "error"))

    def test_restart_loop_stops_resuming(self):
        old = self.cut_off()
        for _ in range(etch_public.RESUME_MAX):
            etch_public.JOBS.clear()
            self.assertEqual(len(etch_public.restore_jobs(start=lambda j: None)), 1)
        etch_public.JOBS.clear()
        self.assertEqual(
            etch_public.restore_jobs(start=lambda j: self.fail("resumed forever")), []
        )
        _, d = self.get(old.id)
        self.assertEqual(d["state"], "failed")

    def test_torn_last_line_is_skipped(self):
        old = self.cut_off()
        with old.journal.open("a") as f:
            f.write('{"ev": {"agent": "pick')
        self.assertEqual(len(etch_public.restore_jobs(start=lambda j: None)), 1)

    def test_start_build_journals_the_job(self):
        with mock.patch.object(etch_public, "worker", lambda j: None):
            c = http.client.HTTPConnection(
                "127.0.0.1", self.srv.server_address[1], timeout=30
            )
            h = etch_public.H.__new__(etch_public.H)
            sent = {}
            h.send_json = lambda obj, code=200: sent.update(obj=obj, code=code)
            h.start_build("9.9.9.9", ["true"], "design", ASK)
            c.close()
        jid = sent["obj"]["job"]
        rows = [
            json.loads(x) for x in (self.dir / f"{jid}.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[0]["meta"]["text"], ASK)


if __name__ == "__main__":
    unittest.main()
