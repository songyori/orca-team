import json
import unittest
from unittest import mock
from datetime import datetime

from helpers import ProjectCase
from orch import orca, setup, util
from orch.config import Config
from orch.paths import Project

STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
LIVE = {"ok": True, "result": {"terminal": {"connected": True, "orphaned": False, "agentIdentity": "claude"}}}
MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}


class CoordinatorTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.run_json = self.root / ".orchestration/state/run.json"

    def set_run(self, **run):
        self.run_json.write_text(json.dumps(run), encoding="utf-8")

    def run_state(self):
        return json.loads(self.run_json.read_text(encoding="utf-8"))

    def test_up_launches_coordinator_with_fixed_flags(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("launched", "term_new"))
        call = self.calls_for("agent launch")[-1]
        for flag, value in (("--agent", "claude"), ("--surface", "terminal"), ("--model", "opus"),
                            ("--effort", "xhigh"), ("--worktree", f"path:{self.root}")):
            self.assertEqual(call[call.index(flag) + 1], value, flag)
        self.assertTrue(call[call.index("--prompt") + 1].startswith("[orca-team:coordinator]"))
        self.assertEqual(self.run_state()["coordinator_handle"], "term_new")

    def test_up_keeps_live_coordinator(self):
        self.set_run(run_id="run_x", coordinator_handle="term_coord")
        self.respond("terminal show", LIVE)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["run_id"]), ("running", "run_x"))
        self.assertEqual(self.calls_for("agent launch"), [])

    def receipt(self):
        return json.loads((self.root / ".orchestration/state/coordinator-launch.json").read_text(encoding="utf-8"))

    def test_up_failure_keeps_receipt(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": False, "error": {"code": "launch_failed"}}, code=1)
        proc = self.orch("up", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.receipt(), {"ok": False, "error": {"code": "launch_failed"}})
        self.assertNotIn("coordinator_handle", self.run_state())
        self.assertNotIn("launched_at", self.run_state())
        self.assertIn("코디네이터 실행 실패", proc.stderr)

    def test_up_retries_immediately_after_definite_failure(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": False, "error": {"code": "launch_failed"}}, code=1)
        self.orch("up", check=False, env=MAIN)
        self.orch("up", check=False, env=MAIN)
        self.assertEqual(len(self.calls_for("agent launch")), 2)

    def test_alive_clears_launched_at_in_up_and_status(self):
        recent = {"coordinator_handle": "term_coord", "launched_at": datetime.now().astimezone().isoformat()}
        self.set_run(**recent)
        self.respond("terminal show", LIVE)
        self.orch("up", env=MAIN)
        self.assertNotIn("launched_at", self.run_state())
        self.set_run(**recent)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual((st["coordinator"]["alive"], st["coordinator"]["starting"]), (True, False))
        self.assertNotIn("launched_at", self.run_state())
        self.respond("terminal show", STALE, code=1)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertFalse(st["coordinator"]["starting"])

    def test_starting_future_launched_at_is_not_starting(self):
        self.set_run(launched_at="2999-01-01T00:00:00+00:00")
        self.respond("terminal show", STALE, code=1)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertFalse(st["coordinator"]["starting"])

    def test_wait_run_timeout_message_without_handle(self):
        self.set_run(launched_at=datetime.now().astimezone().isoformat())
        self.respond("terminal show", STALE, code=1)
        proc = self.orch("up", "--wait-run", "1", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("None", proc.stderr)

    def test_up_unparseable_output_keeps_receipt_and_blocks_duplicate(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond_raw("agent launch", "not json at all")
        proc = self.orch("up", check=False, env=MAIN)
        self.assertNotEqual(proc.returncode, 0)
        receipt = self.receipt()
        self.assertFalse(receipt["ok"])
        self.assertIn("파싱 실패", receipt["parse_error"])
        self.assertIn("launched_at", self.run_state())
        again = self.orch("up", check=False, env=MAIN)
        self.assertEqual(again.returncode, 0)
        self.assertEqual(json.loads(again.stdout)["status"], "starting")
        self.assertEqual(len(self.calls_for("agent launch")), 1)

    def test_up_ok_without_handle_errors_with_receipt(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {}})
        proc = self.orch("up", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.receipt(), {"ok": True, "result": {}})
        self.assertNotIn("coordinator_handle", self.run_state())

    def test_up_rejects_own_terminal_handle_as_coordinator(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"caller": {"handle": "term_main"}}})
        proc = self.orch("up", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("term_main", proc.stderr)
        self.assertIn("메인", proc.stderr)
        self.assertEqual(self.receipt(), {"ok": True, "result": {"caller": {"handle": "term_main"}}})
        self.assertNotIn("coordinator_handle", self.run_state())
        self.assertIn("launched_at", self.run_state())
        again = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual(again["status"], "starting")
        self.assertEqual(len(self.calls_for("agent launch")), 1)

    def test_up_prefers_explicit_terminal_handle_over_nested_handle(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"worktree": {"handle": "wt_other"},
                                                              "terminal": {"handle": "term_right"}}})
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual(out["handle"], "term_right")
        self.assertEqual(self.run_state()["coordinator_handle"], "term_right")

    def test_up_grace_window_blocks_second_launch_then_allows_after(self):
        self.set_run()
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        self.orch("up", env=MAIN)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("starting", "term_new"))
        self.assertIn("orch status", out["message"])
        self.assertEqual(len(self.calls_for("agent launch")), 1)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertTrue(st["coordinator"]["starting"])
        run = self.run_state()
        run["launched_at"] = "2020-01-01T00:00:00+00:00"
        self.set_run(**run)
        self.assertEqual(json.loads(self.orch("up", env=MAIN).stdout)["status"], "launched")
        self.assertEqual(len(self.calls_for("agent launch")), 2)
        st = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertTrue(st["coordinator"]["starting"])

    def test_up_wait_run_times_out_without_run(self):
        self.set_run(coordinator_handle="term_coord")
        self.respond("terminal show", LIVE)
        proc = self.orch("up", "--wait-run", "1", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Run", proc.stderr)

    def test_update_run_rereads_applies_and_returns(self):
        p = Project(self.root)
        self.set_run(a=1)
        got = util.update_run(p, lambda run: run.update(b=2))
        self.assertEqual(got, {"a": 1, "b": 2})
        self.assertEqual(self.run_state(), {"a": 1, "b": 2})
        self.assertTrue((self.root / ".orchestration/state/run.json.lock").exists())

    def _alive_with_concurrent_bind(self):
        def fake_alive(handle):
            run = self.run_state()
            run["run_id"] = "run_bound"
            self.set_run(**run)
            return True
        return mock.patch.object(orca, "terminal_alive", side_effect=fake_alive)

    def test_status_alive_keeps_concurrent_run_id(self):
        self.set_run(coordinator_handle="term_coord", launched_at="2999-01-01T00:00:00+00:00")
        with self._alive_with_concurrent_bind():
            out = setup.status(Project(self.root), Config.load(Project(self.root).config_path))
        self.assertEqual(out["run_id"], "run_bound")
        self.assertEqual(self.run_state()["run_id"], "run_bound")
        self.assertNotIn("launched_at", self.run_state())

    def test_up_alive_keeps_concurrent_run_id(self):
        self.set_run(coordinator_handle="term_coord", launched_at="2999-01-01T00:00:00+00:00")
        with self._alive_with_concurrent_bind():
            out = setup.up(Project(self.root), None)
        self.assertEqual((out["status"], out["run_id"]), ("running", "run_bound"))
        self.assertEqual(self.run_state()["run_id"], "run_bound")
        self.assertNotIn("launched_at", self.run_state())

    def test_bind_only_from_coordinator_then_rebinds(self):
        self.set_run(coordinator_handle="term_coord")
        self.assertEqual(self.orch("bind", check=False, env=MAIN).returncode, 2)
        self.assertEqual(self.calls_for("orchestration run-create"), [])
        self.respond("orchestration run-create", {"ok": True, "result": {"run": {"id": "run_new"}}})
        self.assertEqual(json.loads(self.orch("bind").stdout), {"status": "created", "run_id": "run_new"})
        objective = self.calls_for("orchestration run-create")[-1]
        self.assertEqual(objective[objective.index("--objective") + 1], "테스트 프로젝트. 할 일 웹앱.")
        self.assertEqual(json.loads(self.orch("bind").stdout)["status"], "rebound")
        call = self.calls_for("orchestration run-use")[-1]
        self.assertEqual(call[call.index("--id") + 1], "run_new")

    def test_status(self):
        self.respond("terminal show", STALE, code=1)
        self.orch("notify", "--type", "progress", "x")
        out = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual(out["run_id"], "run_x")
        self.assertFalse(out["coordinator"]["alive"])
        self.assertEqual(out["unread_outbox"], 1)
        self.assertFalse(out["watch_alive"])
        self.assertTrue(out["setup_mode"])
        self.assertEqual(out["tasks"], [])


if __name__ == "__main__":
    unittest.main()
