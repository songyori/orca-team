import json
import os
import time
import unittest
from unittest import mock

from helpers import FAKES, ProjectCase
from test_tasks import TaskCase, show_worker
from orch import agents, orca, tasks
from orch.config import Config
from orch.paths import Project

LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
FRESH = {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1", "terminal": "term_a1"}}
SPLIT = {"ok": True, "result": {"split": {"handle": "term_r1", "tabId": "tab_1"}}}
NEXT = {"ok": True, "result": {"taskId": "task_2", "dispatchId": "ctx_2", "terminal": "term_a1"}}
START_FAILED = {"ok": False, "error": {"code": "terminal_busy", "message": "terminal is not accepting input"}}


def live_output(seconds_ago):
    """실제 Orca 1.4.250 `terminal show` 모양: lastOutputAt 은 ms epoch 정수다(일하는 Claude 는 스피너로 1초 안팎마다 바뀐다)."""
    return {"ok": True, "result": {"terminal": {"connected": True, "writable": True, "agentIdentity": "claude",
                                                "agentWait": None, "lastOutputAt": int((time.time() - seconds_ago) * 1000),
                                                "title": "생산", "paneTitle": "생산 (Claude)", "tabId": "tab_1"}}}


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

    def test_busy_agent_without_overflow_waits_as_pending_then_start_ready_starts(self):
        self.no_overflow()
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.commit_spec("T-002", role="planner")
        out = json.loads(self.orch("start", "T-002").stdout)
        self.assertEqual((out["status"], out["reason"]), ("waiting", "agent"))
        self.assertIn("enabled = false", out["message"])
        st = self.state("T-002")
        self.assertEqual((st["status"], st["role"], st["depends_on"], st["dispatch_id"]), ("pending", "planner", [], None))
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["waiting"], [{"id": "T-002", "reason": "agent", "detail": "생산"}])
        agents.release_task(self.p, "production", "T-001")
        self.respond("orchestration worker-start", NEXT)
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-002"])
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_start_during_agent_reset_waits_with_reset_reason(self):
        self.commit_spec("T-001")
        agents.update(self.p, "production", lambda e: e.update(resetting={"dispatch_id": "ctx_h", "at": "x"}))
        out = json.loads(self.orch("start", "T-001").stdout)
        self.assertEqual((out["status"], out["reason"]), ("waiting", "agent"))
        self.assertIn("리셋", out["message"])
        self.assertNotIn("enabled = false", out["message"])
        self.assertEqual(self.state("T-001")["status"], "pending")
        self.assertEqual(self.calls_for("orchestration worker-start"), [])

    def test_task_and_handle_are_recorded_before_layout_calls(self):
        """화면 배치(split·이름) 도중 관제가 죽어도 에이전트가 맡은 태스크와 작업 터미널을 잃지 않는다."""
        self.commit_spec("T-001")
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": "term_drive"}), \
                mock.patch.object(agents, "make_layout", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                tasks.start(self.p, self.cfg(), "T-001")
        state = self.agents_state()["production"]
        self.assertEqual((state["task"], state["handle"]), ("T-001", "term_a1"))

    def test_dead_review_pane_is_split_again(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond_seq("terminal show", LIVE, (STALE, 1))  # 작업 터미널은 살아 있고 리뷰 pane 은 죽었다
        self.respond("terminal split", {"ok": True, "result": {"split": {"handle": "term_r2", "tabId": "tab_1"}}})
        self.respond("orchestration worker-start", NEXT)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        self.assertEqual(len(self.calls_for("terminal split")), 2)
        self.assertEqual(self.agents_state()["production"]["review_handle"], "term_r2")

    def test_failed_layout_is_retried_once(self):
        self.respond("terminal split", {"ok": False, "error": {"code": "x"}}, code=1)
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.assertIn("layout_error", self.agents_state()["production"])
        for task_id in ("T-002", "T-003"):
            previous = self.agents_state()["production"]["task"]
            agents.release_task(self.p, "production", previous)
            self.set_state(previous, status="done")  # 같은 소유 경로의 다음 기획 태스크가 시작할 수 있게
            self.respond("orchestration worker-start", NEXT)
            self.commit_spec(task_id, role="planner")
            self.orch("start", task_id)
        self.assertEqual(len(self.calls_for("terminal split")), 2)  # 처음 + 한 번 다시
        self.respond("terminal split", SPLIT)
        agents.release_task(self.p, "production", "T-003")
        self.set_state("T-003", status="done")
        self.commit_spec("T-004", role="planner")
        self.orch("start", "T-004")
        self.assertEqual(len(self.calls_for("terminal split")), 2)

    def test_unknown_terminal_state_does_not_launch_fresh_session(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond("terminal show", {"ok": True, "result": {}})  # 터미널 모양을 알 수 없다(unknown)
        self.commit_spec("T-002", role="planner")
        starts = len(self.calls_for("orchestration worker-start"))
        proc = self.orch("start", "T-002", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("상태를 알 수 없다", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.agents_state()["production"]["handle"], "term_a1")

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

    def busy_with_t001_and_t002_changes(self, status="changes", role="planner"):
        """생산 에이전트가 T-001 을 하는 중이고, 같은 에이전트가 했던 T-002 가 수정을 기다린다."""
        self.started()
        self.commit_spec("T-002", role=role)
        head = self.git("rev-parse", "HEAD").strip()
        (self.root / ".orchestration/state/tasks/T-002.json").write_text(json.dumps({
            "id": "T-002", "role": role, "title": "로그인", "status": status, "stage": "work", "round": 1,
            "base": head, "owns": ["docs/planning/**"], "depends_on": [], "agent": "production", "task_id": "task_x",
            "dispatch_id": "ctx_x", "history": [], "verdicts": {"1": "REQUEST_CHANGES"}}), encoding="utf-8")

    def test_fix_on_own_finished_task_runs_now_with_main_note(self):
        """에스컬레이션 뒤 워커는 끝났지만 에이전트는 아직 그 태스크를 맡고 있다. 그 태스크 뒤에 줄 세우지 않고 바로 띄운다."""
        self.started()
        show_worker(self, "exited", stage="settled", state="failed")
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--note", "메인 지시")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["history"][-1]["note"]), ("working", 2, "메인 지시"))
        self.assertNotIn("queued_fix", st)
        state = self.agents_state()["production"]
        self.assertEqual((state["task"], state.get("queue") or []), ("T-001", []))
        self.assertEqual(self.arg(self.last_start(), "--terminal"), "term_a1")

    def test_since_reset_counts_tasks_not_fix_rounds(self):
        """since_reset 은 처리한 태스크 수다. 같은 태스크의 수정 라운드는 다시 세지 않는다. 리셋하면 처음부터 센다."""
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="changes")
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--note", "고친다")
        agents.release_task(self.p, "production", "T-001")
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        agents.release_task(self.p, "production", "T-002")
        self.assertEqual(self.agents_state()["production"]["since_reset"], 2)
        agents.finish_reset(self.p, "production", True, sleep=lambda _: None)
        self.assertEqual(self.agents_state()["production"]["since_reset"], 0)
        agents.update(self.p, "production", lambda e: e.update(task="T-001"))
        agents.release_task(self.p, "production", "T-001")  # 리셋 뒤 같은 태스크의 수정 라운드는 새 세션의 일이다
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)

    def test_force_fix_on_live_own_task_abandons_and_relaunches_fresh(self):
        self.started()
        show_worker(self, "live", stage="running")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.respond("terminal split", {"ok": True, "result": {"split": {"handle": "term_r2", "tabId": "tab_2"}}})
        self.orch("fix", "T-001", "--force", "--note", "멈추고 다시")
        for verb in ("worker-abandon", "worker-release"):
            self.assertEqual(self.arg(self.calls_for(f"orchestration {verb}")[-1], "--dispatch"), "ctx_1")
        # worker-release 는 --keep-open 터미널을 닫지 않는다. 옛 세션이 이어 일하지 않게 새로 띄우기 전에 탭을 닫는다.
        calls = [c[:2] for c in self.calls()]
        [close] = self.calls_for("terminal close")
        self.assertEqual((self.arg(close, "--terminal"), "--tab" in close), ("term_a1", True))
        self.assertLess(calls.index(["terminal", "close"]), len(calls) - 1 - calls[::-1].index(["orchestration", "worker-start"]))
        self.assertIn("--keep-open", self.last_start())
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["dispatch_id"], st["history"][-1]["note"]),
                         ("working", 2, "ctx_5", "멈추고 다시"))
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"], state.get("queue") or []), ("term_a2", "T-001", []))

    def test_force_fix_abandon_failure_still_closes_tab_and_relaunches(self):
        """abandon 이 실패해도 탭을 닫으면 세션은 멈춘 것이다. 실패는 이력에 남기고 새 세션으로 띄운다."""
        self.started()
        show_worker(self, "live", stage="running")
        self.respond("orchestration worker-abandon", {"ok": False, "error": {"code": "x"}}, code=1)
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.orch("fix", "T-001", "--force", "--note", "x")
        self.assertEqual(self.arg(self.calls_for("terminal close")[-1], "--terminal"), "term_a1")
        self.assertIn("--keep-open", self.last_start())
        last = self.state("T-001")["history"][-1]
        self.assertEqual((last["stage"], last["closed_tab"]), ("fix", True))
        self.assertIn("worker-abandon", last["dispatch_error"])
        self.assertNotIn("previous_abandoned", last)
        self.assertEqual(self.agents_state()["production"]["handle"], "term_a2")

    def test_force_fix_abandon_and_tab_close_failure_escalates_without_relaunch(self):
        self.started()
        show_worker(self, "live", stage="running")
        self.respond("orchestration worker-abandon", {"ok": False, "error": {"code": "x"}}, code=1)
        self.respond("terminal close", {"ok": False, "error": {"code": "x", "message": "close failed"}}, code=1)
        starts = len(self.calls_for("orchestration worker-start"))
        proc = self.orch("fix", "T-001", "--force", "--note", "x", check=False)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("worker-abandon", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 1))
        error = st["history"][-1]["release_error"]
        self.assertEqual(sorted(error), ["terminal-close", "worker-abandon"])
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), ("term_a1", "T-001"))

    def test_second_force_fix_skips_abandon_of_abandoned_dispatch(self):
        """탭 닫기만 실패한 뒤 다시 fix --force 하면 이미 버린 디스패치에 abandon 을 다시 부르지 않고 탭 닫기로 간다."""
        self.started()
        show_worker(self, "live", stage="running")
        self.respond_seq("terminal close", ({"ok": False, "error": {"code": "x"}}, 1))
        self.assertEqual(self.orch("fix", "T-001", "--force", "--note", "x", check=False).returncode, 1)
        self.assertEqual(self.state("T-001")["history"][-1]["previous_abandoned"], "ctx_1")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.orch("fix", "T-001", "--force", "--note", "x")
        self.assertEqual(len(self.calls_for("orchestration worker-abandon")), 1)
        self.assertEqual(len(self.calls_for("terminal close")), 2)
        self.assertEqual(self.state("T-001")["dispatch_id"], "ctx_5")

    def test_force_fix_skips_abandon_when_worker_is_already_abandoned(self):
        self.started()
        show_worker(self, "live", stage="running", state="abandoned")
        self.orch("fix", "T-001", "--force", "--note", "x")
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])
        self.assertEqual(self.arg(self.calls_for("terminal close")[-1], "--terminal"), "term_a1")
        self.assertIn("--keep-open", self.last_start())

    def test_force_fix_tab_close_failure_escalates_and_keeps_agent(self):
        self.started()
        show_worker(self, "live", stage="running")
        self.respond("terminal close", {"ok": False, "error": {"code": "x", "message": "close failed"}}, code=1)
        starts = len(self.calls_for("orchestration worker-start"))
        proc = self.orch("fix", "T-001", "--force", "--note", "x", check=False)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("terminal-close", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertIn("terminal-close", self.state("T-001")["history"][-1]["release_error"])
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["review_handle"], state["task"]), ("term_a1", "term_r1", "T-001"))

    def test_force_fix_that_ends_up_queued_records_previous_abandoned(self):
        self.started()
        show_worker(self, "live", stage="running")
        agents.enqueue(self.p, "production", "T-009")  # 다른 태스크가 먼저 줄을 서 있다
        out = json.loads(self.orch("fix", "T-001", "--force", "--note", "멈추고 다시").stdout)
        self.assertEqual(out["status"], "queued")
        entry = self.state("T-001")["history"][-1]
        self.assertEqual((entry["stage"], entry["previous_abandoned"]), ("force_fix", "ctx_1"))

    def test_refusal_for_live_agent_task_does_not_claim_release(self):
        self.started()
        show_worker(self, "live", stage="running")
        proc = self.orch("fix", "T-001", "--note", "x", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--force", proc.stderr)
        self.assertNotIn("해제", proc.stderr)

    def test_second_queued_fix_merges_notes(self):
        self.busy_with_t001_and_t002_changes()
        self.orch("fix", "T-002", "--note", "보강")
        self.orch("fix", "T-002", "--note", "추가")
        note = self.state("T-002")["queued_fix"]["note"]
        self.assertIn("보강", note)
        self.assertIn("추가", note)
        self.assertLess(note.index("보강"), note.index("추가"))
        self.assertEqual(self.agents_state()["production"]["queue"], ["T-002"])

    def test_accept_and_drop_clear_queued_fix(self):
        self.busy_with_t001_and_t002_changes(status="awaiting_user", role="designer")
        self.assertEqual(json.loads(self.orch("fix", "T-002", "--note", "색").stdout)["status"], "queued")
        self.assertEqual(json.loads(self.orch("accept", "T-002").stdout)["status"], "done")
        self.assertNotIn("queued_fix", self.state("T-002"))
        self.assertEqual(self.agents_state()["production"]["queue"], [])
        self.set_state("T-002", status="changes")
        self.orch("fix", "T-002", "--note", "또")
        self.orch("drop", "T-002", "--reason", "필요 없어졌다")
        self.assertNotIn("queued_fix", self.state("T-002"))
        self.assertEqual(self.agents_state()["production"]["queue"], [])

    def test_drain_error_clears_queued_fix_and_reports_note(self):
        self.busy_with_t001_and_t002_changes()
        self.orch("fix", "T-002", "--note", "보강")
        self.set_state("T-002", round=3)  # 그 사이 최대 라운드가 됐다: 대기열의 fix 가 거부된다
        agents.release_task(self.p, "production", "T-001")
        out = json.loads(self.orch("start-ready").stdout)
        [row] = out["fixes"]
        self.assertEqual((row["id"], row["status"], row["note"]), ("T-002", "error", "보강"))
        self.assertNotIn("queued_fix", self.state("T-002"))
        self.assertEqual(self.agents_state()["production"]["queue"], [])

    def test_force_drop_of_live_own_task_frees_terminal(self):
        self.started()
        show_worker(self, "live", stage="running")
        out = json.loads(self.orch("drop", "T-001", "--reason", "그만", "--force").stdout)
        self.assertEqual(out["status"], "dropped")
        for verb in ("worker-abandon", "worker-release"):
            self.assertEqual(self.arg(self.calls_for(f"orchestration {verb}")[-1], "--dispatch"), "ctx_1")
        self.assertEqual(self.arg(self.calls_for("terminal close")[-1], "--terminal"), "term_a1")
        self.assertNotIn("release_error", out)
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), (None, None))

    def test_force_drop_tab_close_failure_drops_and_asks_user_to_close(self):
        """drop 은 닫지 못해도 중단한다(지금 규칙). 사용자에게 탭을 닫아 달라고 안내한다."""
        self.started()
        show_worker(self, "live", stage="running")
        self.respond("terminal close", {"ok": False, "error": {"code": "x", "message": "close failed"}}, code=1)
        out = json.loads(self.orch("drop", "T-001", "--reason", "그만", "--force").stdout)
        self.assertEqual((out["status"], out["released"]), ("dropped", False))
        self.assertIn("terminal-close", out["release_error"])
        self.assertIn("탭", out["hint"])

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

    def test_finish_reset_beats_between_steps(self):
        self.started()
        beats = []
        agents.finish_reset(self.p, "production", True, sleep=lambda _: None, beat=lambda: beats.append(1))
        self.assertGreaterEqual(len(beats), 2)

    def test_begin_reset_waits_on_unknown_terminal_state(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        state = self.agents_state()["production"]
        self.assertEqual((state["reset_pending"], state["handle"]), (True, "term_a1"))
        self.assertEqual(self.calls_for("orchestration task-create"), [])

    def test_begin_reset_gives_up_after_unknown_cap(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        self.assertTrue(self.agents_state()["production"]["reset_unknown_since"])
        old = "2020-01-01T00:00:00+00:00"
        agents.update(self.p, "production", lambda e: e.update(reset_unknown_since=old))
        out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual(out["status"], "unknown_stuck")
        state = self.agents_state()["production"]
        self.assertEqual((state["reset_pending"], "reset_unknown_since" in state), (False, False))

    def test_unknown_since_is_cleared_while_agent_is_busy(self):
        """모름 시각은 연속 구간에만 쓴다. 에이전트가 그새 일을 맡으면 지워, 다음 모름이 바로 상한에 걸리지 않게 한다."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        agents.update(self.p, "production", lambda e: e.update(reset_unknown_since="2020-01-01T00:00:00+00:00",
                                                               task="T-009"))
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        self.assertNotIn("reset_unknown_since", self.agents_state()["production"])
        agents.update(self.p, "production", lambda e: e.pop("task", None))
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        self.assertTrue(self.agents_state()["production"]["reset_pending"])

    def test_begin_reset_waits_while_session_outputs(self):
        """쉬는 세션이 자동 압축 중이면(최근 출력) 인수인계를 넣지 않고 다음 틱으로 미룬다. 출력이 멈추면 넣는다."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("terminal show", live_output(2))
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "deferred")  # 처음 미룰 때만 알린다
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        self.assertEqual(self.calls_for("orchestration task-create"), [])
        state = self.agents_state()["production"]
        self.assertTrue(state["reset_pending"])
        self.assertTrue(state["reset_busy_since"])
        self.respond("terminal show", live_output(120))
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "handoff")
        self.assertNotIn("reset_busy_since", self.agents_state()["production"])

    def test_busy_deferral_is_capped(self):
        """작업 세션 출력이 RESET_UNKNOWN_SECONDS 넘게 이어지면 리셋 표시를 지우고 한 번 알린다(직무가 멈추지 않게)."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("terminal show", live_output(2))
        agents.update(self.p, "production", lambda e: e.update(reset_busy_since="2020-01-01T00:00:00+00:00"))
        out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual(out["status"], "busy_stuck")
        self.assertGreaterEqual(out["minutes"], 30)
        state = self.agents_state()["production"]
        self.assertEqual((state["reset_pending"], "reset_busy_since" in state), (False, False))
        self.assertEqual(self.calls_for("orchestration task-create"), [])
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))

    def test_busy_since_is_cleared_when_session_goes_quiet_or_agent_is_taken(self):
        """출력 대기 시각은 이어진 구간에만 쓴다. 에이전트가 일을 맡거나 출력이 멈추면 지운다."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("terminal show", live_output(2))
        agents.begin_reset(self.p, self.cfg(), "production")
        agents.update(self.p, "production", lambda e: e.update(task="T-009"))
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        self.assertNotIn("reset_busy_since", self.agents_state()["production"])
        agents.update(self.p, "production", lambda e: e.update(task=None))
        agents.begin_reset(self.p, self.cfg(), "production")
        self.assertTrue(self.agents_state()["production"]["reset_busy_since"])
        self.respond("terminal show", STALE, code=1)  # 터미널이 죽었다: 리셋할 세션이 없다
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "skipped")
        self.assertNotIn("reset_busy_since", self.agents_state()["production"])

    def test_failed_handoff_start_closes_created_orca_task(self):
        """task-create 는 됐는데 worker-start 가 실패하면 만든 Orca 태스크를 failed 로 닫는다(고아 태스크를 남기지 않는다)."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("orchestration worker-start", START_FAILED, code=1)
        out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual(out["status"], "failed")
        self.assertIn("terminal_busy", out["error"])
        [update] = self.calls_for("orchestration task-update")
        self.assertEqual((self.arg(update, "--id"), self.arg(update, "--status")), ("task_2", "failed"))
        self.assertIn("terminal_busy", self.arg(update, "--result"))
        state = self.agents_state()["production"]
        self.assertEqual((state["reset_pending"], state["reset_failures"]), (True, 1))
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "handoff")
        self.assertNotIn("reset_failures", self.agents_state()["production"])

    def test_handoff_never_launches_fresh_session(self):
        """인수인계 직전에 터미널이 죽어도 새 세션에 인수인계를 쓰게 하지 않는다(새 세션은 맥락이 없다)."""
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        starts = len(self.calls_for("orchestration worker-start"))
        with mock.patch.object(orca, "terminal_status", return_value=("alive", None)), \
                mock.patch.object(orca, "terminal_state", return_value="dead"):
            out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)

    def test_orch_agent_reset(self):
        self.orch("agent", "reset", "production")
        self.assertTrue(self.agents_state()["production"]["reset_pending"])
        self.assertEqual(self.orch("agent", "reset", "nobody", check=False).returncode, 2)


UNKNOWN = {"ok": True, "result": {"dispatchId": "ctx_1", "state": "start_unknown", "stage": "turn_start_unobserved",
                                  "liveness": "live", "terminal": "term_a1"}}


class RecoveryTest(AgentCase):
    def test_forget_dead_clears_only_dead_handles(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.assertEqual(agents.forget_dead(self.p, self.cfg()), [])
        self.respond("terminal show", STALE, code=1)
        self.assertEqual(agents.forget_dead(self.p, self.cfg()), ["production"])
        self.assertIsNone(self.agents_state()["production"]["handle"])
        self.assertEqual(self.agents_state()["production"]["task"], "T-001")  # 태스크 처리는 drive 가 한다

    SHELL_ONLY = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}  # claude 가 끝나 셸만 남았다

    def unknown_started(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-show", UNKNOWN)
        return len(self.calls_for("orchestration worker-start"))

    def test_unknown_start_on_dead_terminal_closes_old_tab_and_relaunches(self):
        """(c)(d) 작업 터미널이 죽었으면 옛 탭을 닫고(terminal close --tab) 새 세션으로 띄운다."""
        self.unknown_started()
        self.respond("terminal show", self.SHELL_ONLY)
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.respond("terminal split", {"ok": True, "result": {"split": {"handle": "term_r2", "tabId": "tab_2"}}})
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual((out["status"], out["count"]), ("relaunched", 1))
        for verb in ("worker-abandon", "worker-release"):
            self.assertEqual(self.arg(self.calls_for(f"orchestration {verb}")[-1], "--dispatch"), "ctx_1")
        [close] = self.calls_for("terminal close")
        self.assertEqual(self.arg(close, "--terminal"), "term_a1")
        self.assertIn("--tab", close)
        self.assertIn("--keep-open", self.last_start())
        recover = [h for h in self.state("T-001")["history"] if h.get("stage") == "recover"][-1]
        self.assertIs(recover["closed_tab"], True)
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), ("term_a2", "T-001"))
        self.assertEqual(self.state("T-001")["dispatch_id"], "ctx_5")
        self.assertEqual(self.state("T-001")["unknown_starts"], 1)
        self.assertTrue(any(h.get("stage") == "recover" and h.get("reason") == "start_unknown"
                            and h.get("dispatch_id") == "ctx_1" for h in self.state("T-001")["history"]))

    def test_unknown_start_with_working_session_waits(self):
        """(a) 작업 터미널에 최근 출력이 있으면 세션이 일하는 중이다. 다시 넣지 않고 기다린다(이력은 디스패치마다 한 번)."""
        starts = self.unknown_started()
        self.respond("terminal show", live_output(1))
        for _ in range(2):
            self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "busy")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])
        self.assertEqual(self.calls_for("terminal close"), [])
        st = self.state("T-001")
        busy = [h for h in st["history"] if h.get("stage") == "recover"]
        self.assertEqual(busy, [{**busy[0], "reason": "start_unknown", "verdict": "busy", "dispatch_id": "ctx_1"}])
        self.assertEqual((st["dispatch_id"], st.get("unknown_starts")), ("ctx_1", None))
        self.assertEqual(self.agents_state()["production"]["handle"], "term_a1")

    def test_unknown_start_with_idle_session_resends_to_same_terminal(self):
        """(b) 살아 있고 출력이 멈췄으면 입력이 사라졌다고 본다. 디스패치를 버리고 같은 터미널에 다시 넣는다."""
        starts = self.unknown_started()
        self.respond("terminal show", live_output(120))
        self.respond("orchestration worker-start", NEXT)
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual((out["status"], out["count"]), ("relaunched", 1))
        self.assertEqual(self.arg(self.calls_for("orchestration worker-abandon")[-1], "--dispatch"), "ctx_1")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])  # 같은 터미널을 쓴다
        self.assertEqual(self.calls_for("terminal close"), [])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts + 1)
        call = self.last_start()
        self.assertEqual((self.arg(call, "--task"), self.arg(call, "--terminal")), ("task_2", "term_a1"))
        self.assertNotIn("--keep-open", call)
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), ("term_a1", "T-001"))
        st = self.state("T-001")
        self.assertEqual((st["dispatch_id"], st["unknown_starts"], st["history"][-1]["fresh"]), ("ctx_2", 1, False))

    def test_unknown_start_with_unknown_terminal_waits(self):
        """작업 터미널 상태를 모르면 다시 띄우지 않고 다음 확인으로 미룬다."""
        starts = self.unknown_started()
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "unknown")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])

    def test_unknown_start_close_failure_escalates_without_relaunch(self):
        """(d) 옛 탭을 닫지 못하면 다시 띄우지 않고 메인에 올린다."""
        starts = self.unknown_started()
        self.respond("terminal show", self.SHELL_ONLY)
        self.respond("terminal close", {"ok": False, "error": {"code": "x", "message": "close failed"}}, code=1)
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual(out["status"], "escalate")
        self.assertIn("terminal-close", out["release_error"])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertIn("terminal-close", self.state("T-001")["history"][-1]["release_error"])
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), ("term_a1", "T-001"))  # 닫지 못했으면 바꾸지 않는다

    def test_third_unknown_start_escalates(self):
        starts = self.unknown_started()
        self.set_state("T-001", unknown_starts=2)
        self.respond("terminal show", self.SHELL_ONLY)
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "escalate")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.state("T-001")["unknown_starts"], 3)
        self.assertIsNone(self.agents_state()["production"]["handle"])

    def test_busy_check_does_not_count_toward_limit(self):
        """UNKNOWN_START_LIMIT 은 다시 넣거나 새로 띄운 횟수만 센다. 기다린 확인은 세지 않는다."""
        starts = self.unknown_started()
        self.set_state("T-001", unknown_starts=2)
        self.respond("terminal show", live_output(1))
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "busy")
        self.respond("terminal show", live_output(120))
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "escalate")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.state("T-001")["unknown_starts"], 3)
        self.assertEqual(self.agents_state()["production"]["handle"], "term_a1")  # 같은 터미널 경로는 잊지 않는다

    def test_failed_abandon_on_same_terminal_escalates_without_resend(self):
        starts = self.unknown_started()
        self.respond("terminal show", live_output(120))
        self.respond("orchestration worker-abandon", {"ok": False, "error": {"code": "x"}}, code=1)
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual(out["status"], "escalate")
        self.assertIn("worker-abandon", out["release_error"])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)

    def test_failed_release_with_closed_tab_relaunches(self):
        """release 가 실패해도 옛 탭을 닫았으면 세션은 멈춘 것이다. 실패는 이력에 남기고 새로 띄운다."""
        self.unknown_started()
        self.respond("terminal show", self.SHELL_ONLY)
        self.respond("orchestration worker-release", {"ok": False, "error": {"code": "x"}}, code=1)
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual(out["status"], "relaunched")
        recover = [h for h in self.state("T-001")["history"] if h.get("stage") == "recover"][-1]
        self.assertIs(recover["closed_tab"], True)
        self.assertIn("worker-release", recover["dispatch_error"])
        self.assertNotIn("release_error", recover)

    def test_failed_release_and_close_escalates_without_relaunch(self):
        starts = self.unknown_started()
        self.respond("terminal show", self.SHELL_ONLY)
        self.respond("orchestration worker-release", {"ok": False, "error": {"code": "x"}}, code=1)
        self.respond("terminal close", {"ok": False, "error": {"code": "x"}}, code=1)
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual(out["status"], "escalate")
        self.assertEqual(sorted(out["release_error"]), ["terminal-close", "worker-release"])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        last = self.state("T-001")["history"][-1]
        self.assertEqual(last["stage"], "recover")
        self.assertIn("worker-release", last["release_error"])
        self.assertNotIn("closed_tab", last)  # 닫기를 불렀지만 실패했다

    def test_closed_tab_is_not_recorded_without_a_terminal(self):
        """닫을 터미널이 없으면 닫기를 부르지 않고 closed_tab 도 적지 않는다."""
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "dispatchId": "ctx_1", "state": "start_unknown", "stage": "turn_start_unobserved", "liveness": "live"}})
        agents.update(self.p, "production", lambda e: e.update(handle=None))
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "relaunched")
        self.assertEqual(self.calls_for("terminal close"), [])
        recover = [h for h in self.state("T-001")["history"] if h.get("stage") == "recover"][-1]
        self.assertNotIn("closed_tab", recover)

    def test_not_unknown_does_nothing(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-show", {"ok": True, "result": {"state": "running", "stage": "working"}})
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "not_unknown")
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])


if __name__ == "__main__":
    unittest.main()
