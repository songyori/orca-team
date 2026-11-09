import json
import os
import subprocess
import sys
import time
import unittest

from helpers import ORCH, ProjectCase

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}


class CommTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()

    def outbox(self):
        path = self.root / ".orchestration/state/outbox.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_notify_appends_numbered_lines(self):
        a = json.loads(self.orch("notify", "--type", "progress", "하나").stdout)
        b = json.loads(self.orch("notify", "--type", "question", "둘").stdout)
        self.assertEqual((a["id"], b["id"]), ("out-0001", "out-0002"))
        self.assertEqual([m["type"] for m in self.outbox()], ["progress", "question"])

    def test_notify_rejects_bad_type_and_empty_body(self):
        self.assertEqual(self.orch("notify", "--type", "nope", "x", check=False).returncode, 2)
        self.assertEqual(self.orch("notify", "--type", "done", "  ", check=False).returncode, 2)

    def test_watch_returns_only_new_lines(self):
        self.orch("notify", "--type", "progress", "먼저")
        first = json.loads(self.orch("watch", "--timeout", "5", env=MAIN).stdout)
        self.assertEqual([m["body"] for m in first], ["먼저"])
        self.assertEqual(json.loads(self.orch("watch", "--timeout", "0.5", env=MAIN).stdout), [])
        self.assertFalse((self.root / ".orchestration/state/watch.pid").exists())

    def test_watch_wakes_on_new_line(self):
        proc = subprocess.Popen([sys.executable, str(ORCH), "watch", "--timeout", "20"], cwd=self.root,
                                env={**self.env, **MAIN}, stdout=subprocess.PIPE, text=True)
        time.sleep(1.0)
        self.orch("notify", "--type", "done", "끝")
        out, _ = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual([m["type"] for m in json.loads(out)], ["done"])

    def test_watch_refuses_second_instance(self):
        (self.root / ".orchestration/state/watch.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.assertEqual(self.orch("watch", "--timeout", "0.5", check=False, env=MAIN).returncode, 3)

    def test_tell_sends_to_run_mailbox(self):
        self.orch("tell", "로그인 화면 만들어 줘", env=MAIN)
        call = self.calls_for("orchestration send")[-1]
        self.assertEqual(call[call.index("--to") + 1], "run:run_x")
        self.assertEqual(call[call.index("--type") + 1], "status")
        self.assertEqual(call[call.index("--subject") + 1], "[main] 로그인 화면 만들어 줘")
        self.orch("tell", "--reply", "out-0003", "A안", env=MAIN)
        call = self.calls_for("orchestration send")[-1]
        self.assertEqual(call[call.index("--subject") + 1], "[main] reply out-0003")
        self.assertIn("--body=A안", call)

    def test_tell_body_with_leading_dashes_is_not_a_flag(self):
        self.orch("tell", "--", "--help 를 본문으로 보낸다", env=MAIN)
        call = self.calls_for("orchestration send")[-1]
        self.assertIn("--body=--help 를 본문으로 보낸다", call)
        self.assertNotIn("--body", call)
        self.assertNotIn("--help", call)

    def test_tell_without_run_fails(self):
        (self.root / ".orchestration/state/run.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.orch("tell", "x", check=False, env=MAIN).returncode, 1)
        self.assertEqual(self.calls_for("orchestration send"), [])

    def test_wait_requires_coordinator_terminal(self):
        self.assertEqual(self.orch("wait", check=False, env=MAIN).returncode, 2)
        self.assertEqual(self.calls_for("orchestration check"), [])

    def test_wait_uses_fixed_arguments(self):
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": []}})
        out = json.loads(self.orch("wait", "--ack", "d0").stdout)
        self.assertEqual(out["result"]["deliveryId"], "d1")
        call = self.calls_for("orchestration check")[-1]
        self.assertEqual(call[call.index("--ack") + 1], "d0")
        self.assertEqual(call[call.index("--types") + 1], "worker_done,escalation,question,status")
        self.assertEqual(call[call.index("--timeout-ms") + 1], "3600000")
        self.assertIn("--wait", call)
        self.assertFalse((self.root / ".orchestration/state/wait.pid").exists())

    def state(self, name):
        return self.root / ".orchestration/state" / name

    def test_wait_records_main_message_ids(self):
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": [
            {"id": "msg_a", "subject": "[main] 로그인 화면", "type": "status"},
            {"id": "msg_w", "subject": "질문", "type": "question"},
            {"id": "msg_b", "subject": "[main] reply out-0001", "type": "status"},
            {"subject": "[main] id 없음", "type": "status"}]}})
        self.orch("wait")
        ids = self.state("main-msg-ids.json")
        self.assertEqual(json.loads(ids.read_text(encoding="utf-8")), ["msg_a", "msg_b"])
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d2", "messages": [
            {"id": "msg_b", "subject": "[main] reply out-0001"}, {"id": "msg_c", "subject": "[main] 다음"}]}})
        self.orch("wait", "--ack", "d1")
        self.assertEqual(json.loads(ids.read_text(encoding="utf-8")), ["msg_a", "msg_b", "msg_c"])

    def test_wait_without_main_messages_writes_nothing(self):
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": []}})
        self.orch("wait")
        self.assertFalse(self.state("main-msg-ids.json").exists())

    def test_concurrent_notify_ids_unique(self):
        procs = [subprocess.Popen([sys.executable, str(ORCH), "notify", "--type", "progress", f"m{i}"],
                                  cwd=self.root, env=self.env, stdout=subprocess.DEVNULL) for i in range(10)]
        self.assertEqual([pr.wait(timeout=30) for pr in procs], [0] * 10)
        ids = [m["id"] for m in self.outbox()]
        self.assertEqual(len(ids), 10)
        self.assertEqual(sorted(ids), [f"out-{n:04d}" for n in range(1, 11)])

    def test_corrupt_and_unterminated_lines_are_skipped(self):
        self.orch("notify", "--type", "progress", "하나")
        with self.state("outbox.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("{깨진 줄\n")
            fh.write('{"id": "out-0003", "type": "done", "body": "반쪽"')
        out = json.loads(self.orch("watch", "--timeout", "5", env=MAIN).stdout)
        self.assertEqual([m["body"] for m in out], ["하나"])
        new = json.loads(self.orch("notify", "--type", "done", "셋").stdout)
        self.assertEqual(new["id"], "out-0002")
        out = json.loads(self.orch("watch", "--timeout", "5", env=MAIN).stdout)
        self.assertEqual([m["body"] for m in out], ["셋"])

    def test_cursor_unchanged_when_delivery_fails(self):
        from helpers import PLUGIN  # noqa: F401
        from orch import comm
        from orch.paths import Project
        self.orch("notify", "--type", "progress", "하나")
        proj = Project(self.root)
        def boom(lines):
            raise BrokenPipeError
        with self.assertRaises(BrokenPipeError):
            comm.watch(proj, timeout=1, deliver=boom)
        self.assertFalse(self.state("outbox.cursor").exists() and self.state("outbox.cursor").read_text().strip() != "0")
        self.assertFalse(self.state("watch.pid").exists())
        self.assertEqual(len(json.loads(self.orch("watch", "--timeout", "5", env=MAIN).stdout)), 1)

    def test_watch_takes_over_stale_pid(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.state("watch.pid").write_text(f"{dead.pid}\n", encoding="utf-8")
        self.assertEqual(self.orch("watch", "--timeout", "0.5", env=MAIN).returncode, 0)
        self.assertFalse(self.state("watch.pid").exists())

    def test_wait_refuses_second_instance(self):
        self.state("wait.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.assertEqual(self.orch("wait", check=False).returncode, 3)
        self.assertEqual(self.calls_for("orchestration check"), [])

    def test_watch_exits_quietly_when_parent_dies(self):
        from unittest import mock
        from orch import comm
        from orch.paths import Project
        ppids = iter([4242, 4242])
        with mock.patch.object(comm.os, "getppid", lambda: next(ppids, 1)):
            started = time.monotonic()
            out = comm.watch(Project(self.root), poll=0.01, timeout=2)
        self.assertEqual(out, [])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.state("watch.pid").exists())

    def test_watch_sigterm_cleans_pid(self):
        proc = subprocess.Popen([sys.executable, str(ORCH), "watch", "--timeout", "30"], cwd=self.root,
                                env={**self.env, **MAIN}, stdout=subprocess.PIPE, text=True)
        pid = self.state("watch.pid")
        for _ in range(100):
            if pid.exists():
                break
            time.sleep(0.05)
        proc.terminate()
        proc.communicate(timeout=10)
        self.assertFalse(pid.exists())

    def test_post_carries_extra_fields_but_notify_rejects_ask_types(self):
        from orch import comm
        from orch.paths import OrchError, Project
        for kind in ("ask", "approval"):
            self.assertEqual(self.orch("notify", "--type", kind, "x", check=False).returncode, 2)
        seen = []
        entry = comm.post(Project(self.root), "ask", " 질문 ", {"task": "T-001", "render": {"header": "기획"}},
                          check=lambda entries: seen.append(len(entries)))
        self.assertEqual((entry["id"], entry["type"], entry["body"], entry["task"]), ("out-0001", "ask", "질문", "T-001"))
        self.assertEqual(seen, [0])
        self.assertEqual(self.outbox()[0]["render"], {"header": "기획"})
        with self.assertRaises(OrchError):
            comm.post(Project(self.root), "nope", "x")



class BrokenConfigTest(ProjectCase):
    """config.toml 이 깨져도 소통·조회·정리 명령은 돈다. 설정이 필요한 명령만 exit 2."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        (self.root / ".orchestration/config.toml").write_text("roles = [깨진\n", encoding="utf-8")
        state = {"id": "T-001", "role": "developer", "title": "로그인", "status": "approved", "round": 1,
                 "owns": ["src/**"], "dispatch_id": "ctx_1", "task_id": "task_1", "history": []}
        (self.root / ".orchestration/state/tasks").mkdir(parents=True, exist_ok=True)
        (self.root / ".orchestration/state/tasks/T-001.json").write_text(json.dumps(state), encoding="utf-8")

    def test_comm_which_accept_and_drop_work(self):
        self.orch("notify", "--type", "progress", "x")
        self.assertEqual(len(json.loads(self.orch("watch", "--timeout", "0.5", env=MAIN).stdout)), 1)
        self.orch("tell", "요청", env=MAIN)
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": []}})
        self.orch("wait")
        self.assertEqual(json.loads(self.orch("which", "ctx_1").stdout)["id"], "T-001")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")
        self.assertNotIn("[developer]", self.outbox_text())  # accept 는 outbox 에 쓰지 않는다

    def outbox_text(self):
        return (self.root / ".orchestration/state/outbox.jsonl").read_text(encoding="utf-8")

    def test_drop_works(self):
        self.assertEqual(json.loads(self.orch("drop", "T-001", "--reason", "중단").stdout)["status"], "dropped")

    def test_config_commands_fail_with_exit_2(self):
        for args in (("up",), ("task", "new", "developer", "x"), ("start", "T-002"),
                     ("fix", "T-001"), ("review", "T-001")):
            with self.subTest(args=args):
                proc = self.orch(*args, check=False, env=MAIN if args[0] == "up" else None)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("config.toml", proc.stderr)


if __name__ == "__main__":
    unittest.main()
