import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from helpers import FAKES, ORCH, ProjectCase
from orch import comm, drive, queue, setup
from orch.config import Config
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
SHELL = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}
CREATED = {"ok": True, "result": {"terminal": {"handle": "term_d1", "title": "orca-team 관제"}}}
SPEC = "---\nid: T-001\nrole: developer\ntitle: 로그인\n---\n## Target\nsrc\n\n## Change\n폼\n\n## Constraints\n없음\n\n## Acceptance\n통과\n"


class UpCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.run_json = self.root / ".orchestration/state/run.json"
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": "term_drive",
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_run(self, **run):
        self.run_json.write_text(json.dumps(run), encoding="utf-8")

    def run_state(self):
        return json.loads(self.run_json.read_text(encoding="utf-8"))


class UpTest(UpCase):
    def test_up_creates_drive_tab(self):
        self.set_run(run_id="run_x")
        self.respond("terminal create", CREATED)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("launched", "term_d1"))
        call = self.calls_for("terminal create")[-1]
        self.assertEqual(call[call.index("--title") + 1], "orca-team 관제")
        self.assertTrue(call[call.index("--command") + 1].endswith("/.orchestration/bin/orch drive"))
        self.assertEqual(self.run_state()["drive_handle"], "term_d1")

    def test_up_restarts_drive_in_old_shell(self):
        self.set_run(run_id="run_x", drive_handle="term_d0")
        self.respond("terminal show", SHELL)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("relaunched", "term_d0"))
        sent = self.calls_for("terminal send")[-1]
        self.assertTrue(sent[sent.index("--text") + 1].endswith(" drive"))
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_up_leaves_live_drive(self):
        self.set_run(run_id="run_x", drive_handle="term_drive")
        self.drive_alive()
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual(out["status"], "running")
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_up_refuses_own_terminal_as_drive(self):
        self.set_run(run_id="run_x")
        self.respond("terminal create", {"ok": True, "result": {"terminal": {"handle": "term_main"}}})
        proc = self.orch("up", check=False, env=MAIN)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("메인 자신의 터미널", proc.stderr)

    def test_status_shows_drive_and_agents(self):
        self.drive_alive()
        out = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual((out["drive"]["handle"], out["drive"]["alive"]), ("term_drive", True))
        self.assertEqual(out["agents"], [])  # scaffold() 는 [agents] 를 지운다


class DriveMainTest(UpCase):
    def test_refuses_inside_claude_and_without_terminal(self):
        cfg = Config.load(self.root / ".orchestration/config.toml")
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), self.assertRaises(OrchError):
            drive.main(self.p, cfg)
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": ""}), self.assertRaises(OrchError):
            drive.main(self.p, cfg)

    def test_bind_creates_run_from_drive_terminal(self):
        self.set_run(drive_handle="term_drive")
        self.respond("orchestration run-create", {"ok": True, "result": {"runId": "run_new"}})
        out = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml")).bind()
        self.assertEqual((out["status"], self.run_state()["run_id"]), ("created", "run_new"))


class QueueRoutingTest(UpCase):
    def setUp(self):
        super().setUp()
        (self.root / ".orchestration/tasks/T-001.md").write_text(SPEC, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "setup")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1"}})

    def test_main_start_goes_through_drive(self):
        self.drive_alive()
        d = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"))
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                d.run_queue()
                time.sleep(0.05)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            out = json.loads(self.orch("start", "T-001", env=MAIN).stdout)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(out["dispatch_id"], "ctx_1")
        self.assertEqual(json.loads((self.root / ".orchestration/state/tasks/T-001.json").read_text())["status"],
                         "working")

    def test_main_start_refuses_without_drive(self):
        proc = self.orch("start", "T-001", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("/orca-team:up", proc.stderr)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])

    def test_main_spec_error_is_reported_before_queue(self):
        self.drive_alive()
        proc = self.orch("start", "T-009", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(queue.pending(self.p), [])

    def test_drive_side_runs_directly(self):
        out = json.loads(self.orch("start", "T-001").stdout)  # 기본 환경이 관제 터미널이다
        self.assertEqual(out["dispatch_id"], "ctx_1")


if __name__ == "__main__":
    unittest.main()
