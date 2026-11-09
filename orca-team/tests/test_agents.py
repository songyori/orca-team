import json
import os
import unittest
from unittest import mock

from helpers import FAKES, ProjectCase
from test_tasks import TaskCase
from orch import agents
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


if __name__ == "__main__":
    unittest.main()
