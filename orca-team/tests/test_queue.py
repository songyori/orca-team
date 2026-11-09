import json
import os
import threading
import time
import unittest

from helpers import ProjectCase
from orch import comm, queue
from orch.paths import OrchError, Project
from orch.util import now, write_text_atomic


class QueueTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.p = Project(self.root)
        self.p.ensure_state()

    def make_alive(self):
        self.p.drive_dir.mkdir(parents=True, exist_ok=True)
        self.p.drive_pid.write_text(f"{os.getpid()}\n", encoding="utf-8")
        write_text_atomic(self.p.drive_heartbeat, now())

    def serve(self, result):
        """drive 역할: 첫 요청을 기다렸다가 result 로 끝낸다."""
        def run():
            for _ in range(200):
                found = queue.pending(self.p)
                if found:
                    request = json.loads(found[0].read_text(encoding="utf-8"))
                    queue.complete(self.p, found[0], request, result(request))
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def test_submit_refuses_when_drive_is_down(self):
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)
        self.assertIn("/orca-team:up", str(ctx.exception))
        self.assertEqual(queue.pending(self.p), [])

    def test_stale_heartbeat_counts_as_down(self):
        self.make_alive()
        write_text_atomic(self.p.drive_heartbeat, "2000-01-01T00:00:00+00:00")
        with self.assertRaises(OrchError):
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)

    def test_submit_returns_drive_result(self):
        self.make_alive()
        thread = self.serve(lambda req: {"ok": True, "result": {"echo": req["args"], "command": req["command"]}})
        out = queue.submit(self.p, "start", {"id": "T-001"}, wait=5)
        thread.join()
        self.assertEqual(out, {"echo": {"id": "T-001"}, "command": "start"})
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_failed_result_raises_with_code(self):
        self.make_alive()
        thread = self.serve(lambda req: {"ok": False, "error": "거부한다", "code": 2})
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "fix", {"id": "T-001"}, wait=5)
        thread.join()
        self.assertEqual((str(ctx.exception), ctx.exception.code), ("거부한다", 2))

    def test_timeout_leaves_marker_and_result_goes_to_outbox(self):
        self.make_alive()
        out = queue.submit(self.p, "fix", {"id": "T-001"}, wait=0.1)
        self.assertEqual(out["status"], "queued")
        [path] = queue.pending(self.p)
        request = json.loads(path.read_text(encoding="utf-8"))
        queue.complete(self.p, path, request, {"ok": True, "result": {"id": "T-001"}})
        last = comm.read_outbox(self.p)[-1]
        self.assertEqual((last["type"], last["request"], last["result"]["ok"]), ("result", request["id"], True))
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_pending_keeps_submit_order(self):
        self.make_alive()
        queue.submit(self.p, "start", {"id": "T-001"}, wait=0)
        queue.submit(self.p, "start", {"id": "T-002"}, wait=0)
        ids = [json.loads(path.read_text(encoding="utf-8"))["args"]["id"] for path in queue.pending(self.p)]
        self.assertEqual(ids, ["T-001", "T-002"])

    def test_post_decide(self):
        entry = comm.post_decide(self.p, "worker_failed", "T-001", "빌드 실패", files=["docs/tasks/T-001/report.md"],
                                 commands=['orch fix T-001 --note "<지시>"'])
        self.assertEqual((entry["type"], entry["kind"], entry["task"], entry["files"]),
                         ("decide", "worker_failed", "T-001", ["docs/tasks/T-001/report.md"]))
        self.assertIn("빌드 실패", entry["body"])
        with self.assertRaises(OrchError):
            comm.post_decide(self.p, "nope", "T-001", "x")


if __name__ == "__main__":
    unittest.main()
