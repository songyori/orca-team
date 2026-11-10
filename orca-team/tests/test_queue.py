import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from helpers import PLUGIN, ProjectCase
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
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)
        # 프로세스는 살아 있고 heartbeat 만 멈췄다: /orca-team:up 은 hung 을 돌려줄 뿐이라 꺼진 것과 다르게 알린다
        self.assertIn("멈춘 것 같다", str(ctx.exception))
        self.assertNotIn("/orca-team:up", str(ctx.exception))

    def test_wait_is_shorter_than_bash_default_timeout(self):
        self.assertLessEqual(queue.WAIT_SECONDS, 90)  # Bash 도구 기본 제한(120초)에 먼저 죽으면 그만둔 표시를 못 남긴다

    def test_sigterm_while_waiting_leaves_abandoned_marker(self):
        self.make_alive()
        code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from orch import queue; "
                "from orch.paths import Project; queue.submit(Project(Path(sys.argv[2])), 'fix', {'id': 'T-001'}, wait=30)")
        proc = subprocess.Popen([sys.executable, "-c", code, str(PLUGIN / "lib"), str(self.root)], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            if queue.pending(self.p):
                break
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        [path] = queue.pending(self.p)
        self.assertTrue((self.p.queue_dir / f"{path.name[:6]}.abandoned").exists())

    def test_interrupt_while_waiting_leaves_abandoned_marker(self):
        self.make_alive()
        with mock.patch.object(queue.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                queue.submit(self.p, "fix", {"id": "T-001"}, wait=30)
        self.assertEqual(sorted(f.name for f in self.p.queue_dir.iterdir()), ["000001.abandoned", "000001.json"])

    def test_interrupt_after_result_posts_it_to_outbox(self):
        """그만두는 순간 결과가 이미 왔으면 버리지 않고 outbox result 로 남긴다."""
        self.make_alive()

        def arrive(_):
            write_text_atomic(self.p.queue_dir / "000001.result.json", json.dumps({"ok": False, "error": "거부", "code": 2}))
            raise KeyboardInterrupt
        with mock.patch.object(queue.time, "sleep", side_effect=arrive):
            with self.assertRaises(KeyboardInterrupt):
                queue.submit(self.p, "fix", {"id": "T-001"}, wait=30)
        last = comm.read_outbox(self.p)[-1]
        self.assertEqual((last["type"], last["request"], last["result"]["error"]), ("result", "q-000001", "거부"))
        self.assertEqual(sorted(f.name for f in self.p.queue_dir.iterdir()), ["000001.json"])

    def test_submit_returns_drive_result(self):
        self.make_alive()
        thread = self.serve(lambda req: {"ok": True, "result": {"echo": req["args"], "command": req["command"]}})
        out = queue.submit(self.p, "start", {"id": "T-001"}, wait=5)
        thread.join()
        self.assertEqual(out, {"echo": {"id": "T-001"}, "command": "start"})
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_request_numbers_keep_growing_after_files_are_removed(self):
        """처리된 요청 파일이 지워져도 번호가 다시 1 부터 매겨지지 않는다(drive.log 에서 요청을 가른다)."""
        self.make_alive()
        ids = []
        for _ in range(2):
            thread = self.serve(lambda req: (ids.append(req["id"]), {"ok": True, "result": {}})[1])
            queue.submit(self.p, "start", {"id": "T-001"}, wait=5)
            thread.join()
            self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])
        self.assertEqual(ids, ["q-000001", "q-000002"])

    def test_sequence_starts_after_leftover_files(self):
        self.make_alive()
        self.p.queue_dir.mkdir(parents=True, exist_ok=True)
        (self.p.queue_dir / "000007.abandoned").touch()
        out = queue.submit(self.p, "fix", {"id": "T-001"}, wait=0.1)
        self.assertEqual(out["request"], "q-000008")

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

    def test_complete_after_abandon_marker_posts_one_outbox_result(self):
        self.make_alive()
        queue_dir = self.p.queue_dir
        queue_dir.mkdir(parents=True, exist_ok=True)
        request = {"id": "q-000001", "command": "fix", "args": {"id": "T-001"}, "at": now()}
        write_text_atomic(queue_dir / "000001.json", json.dumps(request))
        (queue_dir / "000001.abandoned").touch()
        path = queue_dir / "000001.json"
        queue.complete(self.p, path, request, {"ok": True, "result": {"id": "T-001"}})
        results = [e for e in comm.read_outbox(self.p) if e["type"] == "result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["request"], "q-000001")
        self.assertEqual(sorted(queue_dir.iterdir()), [])

    def test_finish_wait_takes_result_when_present(self):
        self.make_alive()
        self.p.queue_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.p.queue_dir / "000001.result.json",
                          json.dumps({"ok": True, "result": {"id": "T-001"}}))
        out = queue._finish_wait(self.p, "000001")
        self.assertEqual(out, {"id": "T-001"})
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_finish_wait_marks_abandoned_when_result_absent(self):
        self.make_alive()
        self.p.queue_dir.mkdir(parents=True, exist_ok=True)
        out = queue._finish_wait(self.p, "000001")
        self.assertEqual((out["status"], out["request"]), ("queued", "q-000001"))
        self.assertEqual([f.name for f in self.p.queue_dir.iterdir()], ["000001.abandoned"])

    def test_naive_heartbeat_counts_as_down(self):
        self.make_alive()
        write_text_atomic(self.p.drive_heartbeat, "2026-01-01T00:00:00")
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)
        self.assertIn("멈춘 것 같다", str(ctx.exception))  # pid 는 살아 있다(up 은 hung)
        self.assertEqual(queue.pending(self.p), [])

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
