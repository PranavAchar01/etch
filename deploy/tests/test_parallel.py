"""Parallel live runs: the cross-process heavy slots, locked shared-file writes, the Crusoe 429 backoff and
in-flight limiter, and engine.err on a crash."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))

import etch_public  # noqa: E402
from etchlib import slots  # noqa: E402

from agent import config, llm  # noqa: E402

PY = sys.executable


def _child(code: str, slot_dir: Path, n: int) -> subprocess.Popen:
    env = dict(
        os.environ,
        ETCH_HEAVY_SLOTS=str(n),
        ETCH_HEAVY_SLOT_DIR=str(slot_dir),
        PYTHONPATH=str(ROOT / "engine"),
    )
    return subprocess.Popen(
        [PY, "-c", code], env=env, stdout=subprocess.PIPE, text=True
    )


class HeavySlots(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_n_slots_bound_concurrency_across_processes(self):
        hold = (
            "import time, json\nfrom etchlib.slots import heavy_slot\n"
            "with heavy_slot():\n  a = time.time(); time.sleep(0.6); b = time.time()\n"
            "print(json.dumps([a, b]))"
        )
        procs = [_child(hold, self.dir / "s", 2) for _ in range(4)]
        spans = [json.loads(p.communicate(timeout=30)[0]) for p in procs]
        edges = sorted(
            [(a, 1) for a, _ in spans] + [(b, -1) for _, b in spans],
            key=lambda e: (e[0], e[1]),
        )
        cur = peak = 0
        for _, d in edges:
            cur += d
            peak = max(peak, cur)
        self.assertEqual(
            peak, 2
        )  # never more than ETCH_HEAVY_SLOTS heavy steps at once, and both slots used

    def test_killed_holder_frees_its_slot(self):
        p = _child(
            "from etchlib.slots import heavy_slot\nimport time\nwith heavy_slot():\n print('in', flush=True)\n"
            " time.sleep(60)",
            self.dir / "k",
            2,
        )
        self.assertEqual(p.stdout.readline().strip(), "in")
        with mock.patch.object(slots, "SLOT_DIR", self.dir / "k"):
            self.assertEqual(slots.slots_in_use(2), 1)
            p.kill()
            p.wait()
            self.assertEqual(
                slots.slots_in_use(2), 0
            )  # flock dies with the process: no stale slot

    def test_one_process_holds_one_slot_reentrantly(self):
        with (
            mock.patch.object(slots, "SLOT_DIR", self.dir / "r"),
            mock.patch.dict(os.environ, {"ETCH_HEAVY_SLOTS": "2"}),
        ):
            with slots.heavy_slot():
                with slots.heavy_slot():
                    self.assertEqual(slots.slots_in_use(), 1)
            self.assertEqual(slots.slots_in_use(), 0)

    def test_wait_times_out(self):
        with (
            mock.patch.object(slots, "SLOT_DIR", self.dir / "t"),
            mock.patch.dict(os.environ, {"ETCH_HEAVY_SLOTS": "1"}),
        ):
            p = _child(
                "from etchlib.slots import heavy_slot\nimport time\nwith heavy_slot():\n print('in', flush=True)\n"
                " time.sleep(30)",
                self.dir / "t",
                1,
            )
            try:
                self.assertEqual(p.stdout.readline().strip(), "in")
                with self.assertRaises(TimeoutError):
                    with slots.heavy_slot(wait_s=0.3, poll_s=0.05):
                        pass
            finally:
                p.kill()
                p.wait()


class SharedFiles(unittest.TestCase):
    def test_parallel_update_json_loses_nothing(self):
        f = Path(tempfile.mkdtemp()) / "cache.json"
        code = (
            "import sys\nfrom etchlib.slots import update_json\nw = sys.argv[1]\n"
            "for i in range(25):\n"
            "    update_json(__import__('pathlib').Path(sys.argv[2]), lambda d: {**d, f'{w}-{i}': i}, dict)"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT / "engine"))
        procs = [
            subprocess.Popen([PY, "-c", code, str(w), str(f)], env=env)
            for w in range(6)
        ]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        self.assertEqual(
            len(json.loads(f.read_text())), 150
        )  # 6 writers x 25 keys, no lost update
        self.assertEqual([x.name for x in f.parent.iterdir() if x.suffix == ".tmp"], [])


class Err429(Exception):
    def __init__(self, retry_after: str | None = None):
        super().__init__("Error code: 429 - {'error': {'code': 'max_requests_limit'}}")
        self.status_code = 429
        self.response = SimpleNamespace(
            headers={"retry-after": retry_after} if retry_after else {}
        )


class FakeClient:
    def __init__(self, fails: int = 0, hold_s: float = 0.0):
        self.fails, self.hold_s, self.calls = fails, hold_s, 0
        self.inflight = self.peak = 0
        self.mu = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        with self.mu:
            self.calls += 1
            n = self.calls
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        try:
            time.sleep(self.hold_s)
            if n <= self.fails:
                raise Err429()
            return "ok"
        finally:
            with self.mu:
                self.inflight -= 1


CRUSOE = "https://managed-inference-api-proxy.crusoecloud.com/v1/"


class Crusoe429(unittest.TestCase):
    def setUp(self):
        self.p = [
            mock.patch.object(config, "RATE_PER_MIN", 0)
        ]  # no shared rate window in tests
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_backoff_is_jittered_exponential_with_retry_after_floor(self):
        self.assertEqual(llm._backoff(Err429(), 1, rand=lambda a, b: b), 1.0)
        self.assertEqual(llm._backoff(Err429(), 3, rand=lambda a, b: b), 4.0)
        self.assertEqual(
            llm._backoff(Err429(), 9, rand=lambda a, b: b), llm.BACKOFF_CAP_S
        )
        self.assertEqual(llm._backoff(Err429("3"), 1, rand=lambda a, b: a), 3.0)
        waits = {llm._backoff(Err429(), 2) for _ in range(50)}
        self.assertGreater(len(waits), 5)  # spread out, not lockstep
        self.assertTrue(all(0 <= w <= 2.0 for w in waits))

    def test_429_is_retried_then_answers(self):
        c, slept = FakeClient(fails=2), []
        with mock.patch.object(config, "LLM_429_TRIES", 4):
            self.assertEqual(llm._send(c, CRUSOE, "k", {}, sleep=slept.append), "ok")
        self.assertEqual((c.calls, len(slept)), (3, 2))

    def test_429_gives_up_after_the_tries(self):
        c = FakeClient(fails=99)
        with mock.patch.object(config, "LLM_429_TRIES", 2), self.assertRaises(Err429):
            llm._send(c, CRUSOE, "k", {}, sleep=lambda s: None)
        self.assertEqual(c.calls, 3)

    def test_openai_is_not_retried_here(self):
        c = FakeClient(fails=1)
        with self.assertRaises(Err429):
            llm._send(c, "https://api.openai.com/v1", "k", {}, sleep=lambda s: None)
        self.assertEqual(c.calls, 1)

    def test_in_flight_limiter(self):
        c = FakeClient(hold_s=0.15)
        with mock.patch.object(llm, "_inflight", threading.BoundedSemaphore(2)):
            ts = [
                threading.Thread(target=llm._send, args=(c, CRUSOE, "k", {}))
                for _ in range(6)
            ]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual((c.calls, c.peak), (6, 2))


class EngineErr(unittest.TestCase):
    def run_job(self, code: str, out: Path):
        job = etch_public.Job([PY, "-c", code], "1.2.3.4", "modify", "x")
        etch_public.RUNNING["full"] += 1
        with mock.patch.object(etch_public, "OUT", out):
            etch_public.worker(job)
        return job

    def test_crash_saves_full_stderr_in_the_run_dir(self):
        out = Path(tempfile.mkdtemp())
        code = (
            "import json, sys\nprint(json.dumps({'agent': 'lead', 'type': 'start', 'run': 'live-1234567'}), flush=True)\n"
            "sys.stderr.write('Traceback (most recent call last):\\n  frame one\\nValueError: boom\\n'); sys.exit(3)"
        )
        job = self.run_job(code, out)
        self.assertEqual(job.state, "failed")
        err = (out / "live-1234567" / "engine.err").read_text()
        self.assertIn("exit 3", err)
        self.assertIn("frame one", err)  # the whole stderr, not just the last line
        ev = next(e for e in job.events if e.get("type") == "error")
        self.assertEqual(ev["error"], "ValueError: boom")
        self.assertTrue(ev["engine_err"].endswith("live-1234567/engine.err"))

    def test_crash_before_a_run_dir_goes_to_crashes(self):
        out = Path(tempfile.mkdtemp())
        job = self.run_job(
            "import sys; sys.stderr.write('ImportError: x\\n'); sys.exit(1)", out
        )
        self.assertTrue((out / "_crashes" / f"{job.id}.engine.err").is_file())

    def test_exit_0_without_done_is_a_failed_run_with_a_terminal_status(self):
        out = Path(tempfile.mkdtemp())
        (out / "live-7654321").mkdir()
        code = "import json; print(json.dumps({'agent': 'lead', 'type': 'start', 'run': 'live-7654321'}))"
        job = self.run_job(code, out)
        self.assertEqual(job.state, "failed")
        t = json.loads((out / "live-7654321" / "terminal.json").read_text())
        self.assertEqual((t["state"], t["rc"]), ("failed", 0))

    def test_finished_run_records_done(self):
        out = Path(tempfile.mkdtemp())
        (out / "live-7654322").mkdir()
        code = "import json; print(json.dumps({'agent': 'lead', 'type': 'done', 'ok': True, 'run': 'live-7654322'}))"
        self.assertEqual(self.run_job(code, out).state, "done")
        self.assertEqual(json.loads((out / "live-7654322" / "terminal.json").read_text())["state"], "done")

    def test_clean_exit_saves_nothing(self):
        out = Path(tempfile.mkdtemp())
        self.run_job(
            "import json, sys; sys.stderr.write('warning only\\n'); print(json.dumps({'agent': 'lead', 'type': 'done'}))",
            out,
        )
        self.assertEqual(list(out.rglob("*.err")), [])


if __name__ == "__main__":
    unittest.main()
