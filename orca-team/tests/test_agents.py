import json
import os
import unittest
from unittest import mock

from helpers import FAKES, ProjectCase
from test_tasks import TaskCase
from orch import agents, tasks
from orch.config import Config
from orch.paths import Project

LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
FRESH = {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1", "terminal": "term_a1"}}
SPLIT = {"ok": True, "result": {"split": {"handle": "term_r1", "tabId": "tab_1"}}}
NEXT = {"ok": True, "result": {"taskId": "task_2", "dispatchId": "ctx_2", "terminal": "term_a1"}}


class AgentCase(TaskCase):
    def setUp(self):
        ProjectCase.setUp(self)
        self.scaffold(agents=True)
        self.respond("orchestration worker-start", FRESH)
        self.respond("terminal split", SPLIT)
        self.respond("terminal show", LIVE)
        self.respond("orchestration task-create", {"ok": True, "result": {"taskId": "task_2"}})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": self.env["ORCA_TERMINAL_HANDLE"],
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cfg(self):
        return Config.load(self.root / ".orchestration/config.toml")

    def agents_state(self):
        return json.loads((self.root / ".orchestration/state/agents.json").read_text(encoding="utf-8"))

    def commit_spec(self, task_id, role="developer", extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"[{task_id}] spec")

    def no_overflow(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"), encoding="utf-8")

    def pending(self, task_id, role, owns):
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.write_text(json.dumps({"id": task_id, "role": role, "title": "x", "status": "pending", "stage": "work",
                                    "round": 1, "base": None, "owns": owns, "depends_on": [], "task_id": None,
                                    "dispatch_id": None, "history": [], "verdicts": {}}), encoding="utf-8")


class PlaceTest(AgentCase):
    def test_place_rules(self):
        cfg = self.cfg()
        self.assertEqual(agents.place(self.p, cfg, "developer"), "production")
        agents.update(self.p, "production", lambda e: e.update(task="T-001"))
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.TEMP)
        cfg.data["overflow"] = {"enabled": False}
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.WAIT)
        agents.update(self.p, "production", lambda e: e.update(task=None, reset_pending=True))
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.WAIT)
        cfg.data["agents"].pop("production")
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.TEMP)


class AgentStartTest(AgentCase):
    def test_first_start_launches_kept_open_agent_with_review_pane(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        call = self.last_start()
        self.assertIn("--keep-open", call)
        self.assertNotIn("--terminal", call)
        self.assertEqual((self.arg(call, "--model"), self.arg(call, "--effort")), ("opus", "xhigh"))
        split = self.calls_for("terminal split")[-1]
        self.assertEqual(self.arg(split, "--direction"), "vertical")
        self.assertTrue(self.arg(split, "--command").endswith("review-view production"))
        self.assertEqual(self.arg(self.calls_for("terminal rename")[-1], "--title"), "orca-team 생산")
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["review_handle"], state["task"]), ("term_a1", "term_r1", "T-001"))
        self.assertEqual(self.state("T-001")["agent"], "production")
        self.assertEqual(self.state("T-001")["history"][-1]["agent"], "production")

    def test_next_task_goes_to_same_terminal(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond("orchestration worker-start", NEXT)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        call = self.last_start()
        self.assertEqual((self.arg(call, "--task"), self.arg(call, "--terminal")), ("task_2", "term_a1"))
        self.assertNotIn("--model", call)
        self.assertEqual(len(self.calls_for("terminal split")), 1)
        self.assertEqual(self.calls_for("orchestration task-create")[-1][self.calls_for("orchestration task-create")[-1].index("--task-title") + 1],
                         "T-002 work r1 [planner] 로그인")
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)

    def test_dead_terminal_relaunches(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond("terminal show", STALE, code=1)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        self.assertIn("--keep-open", self.last_start())
        self.assertEqual(len(self.calls_for("terminal split")), 2)

    def test_busy_agent_overflows_to_temp_worker(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_9", "dispatchId": "ctx_9"}})
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        call = self.last_start()
        self.assertNotIn("--keep-open", call)
        self.assertEqual(self.arg(call, "--model"), "opus")  # planner 직무 모델
        self.assertEqual(self.state("T-002")["agent"], "temp")
        self.assertEqual(self.agents_state()["production"]["task"], "T-001")

    def test_busy_agent_without_overflow_refuses_and_start_ready_waits(self):
        self.no_overflow()
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.commit_spec("T-002", role="planner")
        self.assertEqual(self.orch("start", "T-002", check=False).returncode, 3)
        self.pending("T-002", "planner", ["docs/planning/**"])
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["waiting"], [{"id": "T-002", "reason": "agent", "detail": "생산"}])

    def test_front_matter_model_is_ignored_for_agent(self):
        self.commit_spec("T-001", extra="claude_model: haiku\n")
        self.orch("start", "T-001")
        self.assertEqual(self.arg(self.last_start(), "--model"), "opus")
        self.assertTrue(self.state("T-001")["history"][-1]["model_ignored"])


class AgentFixTest(AgentCase):
    def started(self, task_id="T-001", role="developer"):
        self.commit_spec(task_id, role=role)
        self.orch("start", task_id)

    def test_fix_goes_to_same_agent_without_release(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="changes")
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--note", "고친다")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        call = self.last_start()
        self.assertEqual(self.arg(call, "--terminal"), "term_a1")
        self.assertEqual((self.state("T-001")["round"], self.state("T-001")["history"][-1]["note"]), (2, "고친다"))

    def test_fix_queues_while_agent_busy_then_drains(self):
        self.started()                      # 생산 에이전트가 T-001 을 하는 중
        self.commit_spec("T-002", role="planner")
        head = self.git("rev-parse", "HEAD").strip()
        (self.root / ".orchestration/state/tasks/T-002.json").write_text(json.dumps({
            "id": "T-002", "role": "planner", "title": "로그인", "status": "changes", "stage": "work", "round": 1,
            "base": head, "owns": ["docs/planning/**"], "depends_on": [], "agent": "production", "task_id": "task_x",
            "dispatch_id": "ctx_x", "history": [], "verdicts": {"1": "REQUEST_CHANGES"}}), encoding="utf-8")
        out = json.loads(self.orch("fix", "T-002", "--note", "보강").stdout)
        self.assertEqual((out["status"], out["agent"]), ("queued", "production"))
        self.assertEqual(self.agents_state()["production"]["queue"], ["T-002"])
        self.assertEqual(self.state("T-002")["queued_fix"]["note"], "보강")
        agents.release_task(self.p, "production", "T-001")
        self.respond("orchestration worker-start", NEXT)
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["fixes"][0]["id"], "T-002")
        self.assertEqual(self.arg(self.last_start(), "--terminal"), "term_a1")
        self.assertNotIn("queued_fix", self.state("T-002"))
        self.assertEqual(self.agents_state()["production"]["queue"], [])

    def test_extra_round(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="changes", round=3)
        proc = self.orch("fix", "T-001", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--extra-round", proc.stderr)
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--extra-round")
        last = self.state("T-001")["history"][-1]
        self.assertEqual((self.state("T-001")["round"], last["extra_round"]), (4, True))

    def test_accept_and_drop_do_not_release_agent(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="approved")
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["released"]), ("done", None))
        self.assertNotIn("notified", out)
        self.commit_spec("T-002", role="planner")
        self.respond("orchestration worker-start", NEXT)
        self.orch("start", "T-002")
        agents.release_task(self.p, "production", "T-002")
        self.set_state("T-002", status="changes")
        agents.enqueue(self.p, "production", "T-002")
        self.orch("drop", "T-002", "--reason", "필요 없어졌다")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        self.assertEqual(self.agents_state()["production"]["queue"], [])


class ResetTest(AgentCase):
    def started(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")

    def test_spec_reads_handoff_file_when_present(self):
        (self.root / "docs/handoff").mkdir(parents=True)
        (self.root / "docs/handoff/production.md").write_text("# 인수인계\n", encoding="utf-8")
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.assertIn("먼저 `docs/handoff/production.md` 를 읽는다", self.arg(self.last_start(), "--spec"))

    def test_spec_has_no_handoff_line_without_file(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        spec = self.arg(self.last_start(), "--spec")
        self.assertNotIn("docs/handoff", spec)
        self.assertNotIn("\n\n\n", spec)

    def test_mark_reset_pending_only_used_agents(self):
        self.started()
        self.assertEqual(agents.mark_reset_pending(self.p, self.cfg()), ["production"])
        self.assertTrue(self.agents_state()["production"]["reset_pending"])
        self.assertNotIn("research", self.agents_state())

    def test_begin_and_finish_reset(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual((out["status"], out["dispatch_id"]), ("handoff", "ctx_h"))
        created = self.calls_for("orchestration task-create")[-1]
        self.assertIn("docs/handoff/production.md", self.arg(created, "--spec"))
        self.assertEqual(self.arg(created, "--task-title"), "[handoff] 생산 인수인계")
        self.assertEqual(self.arg(self.last_start(), "--terminal"), "term_a1")
        self.assertEqual(agents.reset_owner(self.p, "ctx_h"), "production")
        self.assertEqual(agents.place(self.p, self.cfg(), "developer"), agents.WAIT)
        out = agents.finish_reset(self.p, "production", True, sleep=lambda _: None)
        self.assertEqual(out["status"], "reset")
        sent = self.calls_for("terminal send")[-1]
        self.assertEqual((self.arg(sent, "--terminal"), self.arg(sent, "--text")), ("term_a1", "/clear"))
        self.assertEqual(self.arg(self.calls_for("terminal wait")[-1], "--for"), "tui-idle")
        state = self.agents_state()["production"]
        self.assertEqual((state["since_reset"], state["reset_pending"], state["resetting"]), (0, False, None))
        self.assertEqual(agents.place(self.p, self.cfg(), "developer"), "production")

    def test_failed_handoff_keeps_session(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        agents.begin_reset(self.p, self.cfg(), "production")
        out = agents.finish_reset(self.p, "production", False, sleep=lambda _: None)
        self.assertEqual(out["status"], "handoff_failed")
        self.assertEqual(self.calls_for("terminal send"), [])
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)

    def test_begin_reset_skips_dead_terminal_and_busy_agent(self):
        self.started()
        agents.update(self.p, "production", lambda e: e.update(reset_pending=True, task="T-009"))
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        agents.update(self.p, "production", lambda e: e.update(task=None))
        self.respond("terminal show", STALE, code=1)
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "skipped")
        self.assertEqual((self.agents_state()["production"]["reset_pending"], self.agents_state()["production"]["handle"]),
                         (False, None))

    def test_orch_agent_reset(self):
        self.orch("agent", "reset", "production")
        self.assertTrue(self.agents_state()["production"]["reset_pending"])
        self.assertEqual(self.orch("agent", "reset", "nobody", check=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
