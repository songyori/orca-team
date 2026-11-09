import json
import os
import threading
import time
import unittest
from unittest import mock

from helpers import FAKES, ProjectCase
from orch import drive, queue, setup, util
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
        no_handle = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "ORCA_TERMINAL_HANDLE")}
        with mock.patch.dict(os.environ, no_handle, clear=True), self.assertRaises(OrchError) as ctx:
            drive.main(self.p, cfg)
        self.assertIn("ORCA_TERMINAL_HANDLE", str(ctx.exception))

    def test_bind_creates_run_from_drive_terminal(self):
        self.set_run(drive_handle="term_drive")
        self.respond("orchestration run-create", {"ok": True, "result": {"runId": "run_new"}})
        out = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml")).bind()
        self.assertEqual((out["status"], self.run_state()["run_id"]), ("created", "run_new"))


STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}


class UpRetryTest(UpCase):
    def receipt(self):
        return json.loads((self.root / ".orchestration/state/drive/launch.json").read_text(encoding="utf-8"))

    def test_launch_failure_keeps_receipt_and_allows_retry(self):
        self.set_run()
        self.respond("terminal create", {"ok": False, "error": {"code": "launch_failed"}}, code=1)
        proc = self.orch("up", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.receipt(), {"ok": False, "error": {"code": "launch_failed"}})
        self.assertNotIn("drive_handle", self.run_state())
        self.assertNotIn("launched_at", self.run_state())
        self.assertIn("관제 탭을 만들지 못했다", proc.stderr)
        self.orch("up", check=False, env=MAIN)
        self.assertEqual(len(self.calls_for("terminal create")), 2)

    def test_unparseable_output_keeps_receipt_and_blocks_duplicate(self):
        self.set_run()
        self.respond_raw("terminal create", "not json at all")
        proc = self.orch("up", check=False, env=MAIN)
        self.assertNotEqual(proc.returncode, 0)
        receipt = self.receipt()
        self.assertFalse(receipt["ok"])
        self.assertIn("파싱 실패", receipt["parse_error"])
        self.assertIn("launched_at", self.run_state())
        again = self.orch("up", check=False, env=MAIN)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(json.loads(again.stdout)["status"], "starting")
        self.assertEqual(len(self.calls_for("terminal create")), 1)

    def test_ok_without_handle_errors_and_keeps_receipt(self):
        self.set_run()
        self.respond("terminal create", {"ok": True, "result": {}})
        proc = self.orch("up", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.receipt(), {"ok": True, "result": {}})
        self.assertNotIn("drive_handle", self.run_state())

    def test_grace_window_blocks_second_launch_then_allows_after(self):
        self.set_run()
        self.respond("terminal create", CREATED)
        self.respond("terminal show", STALE, code=1)
        self.orch("up", env=MAIN)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("starting", "term_d1"))
        self.assertIn("orch status", out["message"])
        self.assertEqual(len(self.calls_for("terminal create")), 1)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual((st["drive"]["alive"], st["drive"]["starting"]), (False, True))
        run = self.run_state()
        run["launched_at"] = "2020-01-01T00:00:00+00:00"
        self.set_run(**run)
        self.assertEqual(json.loads(self.orch("up", env=MAIN).stdout)["status"], "launched")
        self.assertEqual(len(self.calls_for("terminal create")), 2)

    def test_wait_run_times_out_without_run(self):
        self.set_run(drive_handle="term_drive")
        self.drive_alive()
        proc = self.orch("up", "--wait-run", "1", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Run", proc.stderr)
        self.assertNotIn("None", proc.stderr)

    def test_loses_launch_claim_to_concurrent_up(self):
        """시작 유예 확인(잠금 밖)과 선점(잠금 안) 사이에 다른 up 이 launched_at 을 쓰면 띄우지 않고 starting 을 돌려준다."""
        self.set_run(run_id="run_x")
        claimed_at = util.now()
        real_starting, checks = setup.starting, []

        def precheck_then_concurrent_claim(run):
            checks.append(run)
            if len(checks) > 1:
                return real_starting(run)
            self.set_run(run_id="run_x", launched_at=claimed_at)
            return False

        with mock.patch.object(setup, "starting", side_effect=precheck_then_concurrent_claim):
            out = setup.up(self.p, Config.load(self.p.config_path))
        self.assertEqual((out["status"], out["handle"], out["run_id"]), ("starting", None, "run_x"))
        self.assertIn("아직 시작 중", out["message"])
        self.assertEqual(self.calls_for("terminal create"), [])
        self.assertEqual(self.run_state()["launched_at"], claimed_at)

    def test_claim_gives_up_when_other_up_already_launched_drive(self):
        self.set_run(run_id="run_x")
        real_starting = setup.starting

        def other_launched(run):
            self.drive_alive()  # 다른 up 이 관제를 띄워 살아 있게 됐다
            return real_starting(run)

        with mock.patch.object(setup, "drive_alive", side_effect=[False, True, True]), \
                mock.patch.object(setup, "starting", side_effect=other_launched):
            out = setup.up(self.p, Config.load(self.p.config_path))
        self.assertEqual(out["status"], "running")
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_hung_drive_is_not_relaunched(self):
        self.set_run(run_id="run_x", drive_handle="term_drive")
        self.drive_alive()
        (self.root / ".orchestration/state/drive/heartbeat").write_text("2020-01-01T00:00:00+00:00", encoding="utf-8")
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("hung", "term_drive"))
        self.assertIn("응답이 없다", out["message"])
        self.assertEqual(self.calls_for("terminal create"), [])
        self.assertEqual(self.calls_for("terminal send"), [])


class ConcurrentRunIdTest(UpCase):
    def _alive_with_concurrent_bind(self):
        def alive(p):
            run = self.run_state()
            run["run_id"] = "run_bound"
            self.set_run(**run)
            return True
        return mock.patch.object(setup, "drive_alive", side_effect=alive)

    def test_status_alive_keeps_concurrent_run_id(self):
        self.set_run(drive_handle="term_drive", launched_at="2999-01-01T00:00:00+00:00")
        with self._alive_with_concurrent_bind():
            out = setup.status(self.p, Config.load(self.p.config_path))
        self.assertEqual(out["run_id"], "run_bound")
        self.assertEqual(self.run_state()["run_id"], "run_bound")
        self.assertNotIn("launched_at", self.run_state())

    def test_up_alive_keeps_concurrent_run_id(self):
        self.set_run(drive_handle="term_drive", launched_at="2999-01-01T00:00:00+00:00")
        with self._alive_with_concurrent_bind():
            out = setup.up(self.p, None)
        self.assertEqual((out["status"], out["run_id"]), ("running", "run_bound"))
        self.assertEqual(self.run_state()["run_id"], "run_bound")
        self.assertNotIn("launched_at", self.run_state())


class BindTest(UpCase):
    def test_bind_only_from_drive_terminal_then_rebinds(self):
        self.set_run(drive_handle="term_drive")
        self.assertEqual(self.orch("bind", check=False, env=MAIN).returncode, 2)
        self.assertEqual(self.calls_for("orchestration run-create"), [])
        self.respond("orchestration run-create", {"ok": True, "result": {"run": {"id": "run_new"}}})
        self.assertEqual(json.loads(self.orch("bind").stdout), {"status": "created", "run_id": "run_new"})
        objective = self.calls_for("orchestration run-create")[-1]
        self.assertEqual(objective[objective.index("--objective") + 1], "테스트 프로젝트. 할 일 웹앱.")
        self.assertEqual(json.loads(self.orch("bind").stdout)["status"], "rebound")
        call = self.calls_for("orchestration run-use")[-1]
        self.assertEqual(call[call.index("--id") + 1], "run_new")


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

    def test_queued_start_ready_exits_1_when_a_start_fails(self):
        self.drive_alive()
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "boom"}}, code=1)
        tasks_dir = self.root / ".orchestration/state/tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (tasks_dir / "T-001.json").write_text(json.dumps({"id": "T-001", "role": "developer", "status": "pending"}),
                                              encoding="utf-8")
        d = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"))
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                d.run_queue()
                time.sleep(0.05)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            proc = self.orch("start-ready", env=MAIN, check=False)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual([s["status"] for s in json.loads(proc.stdout)["started"]], ["launch_failed"])

    def test_malformed_request_is_completed_with_error(self):
        self.drive_alive()
        self.p.queue_dir.mkdir(parents=True, exist_ok=True)
        path = self.p.queue_dir / "000001.json"
        path.write_text(json.dumps({"command": "start", "args": {}}), encoding="utf-8")
        d = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"))
        self.assertEqual(d.run_queue(), 1)
        self.assertEqual(queue.pending(self.p), [])
        result = json.loads((self.p.queue_dir / "000001.result.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertIn("KeyError", result["error"])

    def test_drive_side_runs_directly(self):
        out = json.loads(self.orch("start", "T-001").stdout)  # 기본 환경이 관제 터미널이다
        self.assertEqual(out["dispatch_id"], "ctx_1")


if __name__ == "__main__":
    unittest.main()
