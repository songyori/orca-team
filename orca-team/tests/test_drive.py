import json
import os
import unittest
from unittest import mock

from helpers import FAKES
from test_tasks import TaskCase
from orch import agents, ask, comm, drive, plan, tasks
from orch.config import Config
from orch.paths import OrchError, Project


class FakeProc:
    def __init__(self):
        self.returncode, self.out, self.err = None, "", ""

    def finish(self, code, out="", err=""):
        self.returncode, self.out, self.err = code, out, err

    def poll(self):
        return self.returncode

    def communicate(self):
        return self.out, self.err


class DriveCase(TaskCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": "term_drive",
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)
        self.spawned = []
        self.drive = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"), spawn=self.fake_spawn)

    def fake_spawn(self, args):
        proc = FakeProc()
        self.spawned.append((args, proc))
        return proc

    def started(self, task_id="T-001", role="developer", extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"[{task_id}] spec")
        self.orch("start", task_id)

    def outbox(self, kind=None):
        return [e for e in comm.read_outbox(self.p) if kind is None or e.get("type") == kind]

    def done_msg(self, outcome="succeeded", dispatch="ctx_1"):
        return {"id": "msg_1", "type": "worker_done", "body": "끝냈다",
                "payload": {"taskId": "task_1", "dispatchId": dispatch, "outcome": outcome}}

    def finish_review(self, verdict, task_id="T-001", status=None, **extra):
        """리뷰 자식 프로세스가 끝난 모양을 만든다(리뷰가 쓰는 상태도 맞춘다)."""
        self.set_state(task_id, status=status or ("approved" if verdict == "APPROVE" else "changes"))
        args, proc = self.spawned[-1]
        self.assertEqual(args, ["review", task_id])
        proc.finish(0, json.dumps({"id": task_id, "round": self.state(task_id)["round"], "verdict": verdict,
                                   "source": "codex", "review": f"docs/reviews/{task_id}/r1.md", **extra}))
        self.drive.poll_jobs()


class MessageTest(DriveCase):
    def test_worker_done_starts_review_then_approve_accepts(self):
        self.started()
        self.drive.handle_message(self.done_msg())
        self.finish_review("APPROVE")
        self.assertEqual(self.state("T-001")["status"], "done")

    def test_failed_worker_goes_to_main(self):
        self.started()
        self.drive.handle_message(self.done_msg(outcome="failed"))
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"], entry["files"]),
                         ("worker_failed", "T-001", ["docs/tasks/T-001/report.md"]))
        self.assertEqual(self.state("T-001")["decide"], entry["id"])
        self.assertEqual(self.spawned, [])

    def test_question_goes_to_main_with_message_id(self):
        self.started()
        self.drive.handle_message({"id": "msg_q", "type": "question", "body": "토큰 이름?",
                                   "payload": {"taskId": "task_1", "dispatchId": "ctx_1"}})
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["message_id"], entry["question"]), ("worker_question", "msg_q", "토큰 이름?"))

    def test_check_passes_ack(self):
        self.respond_seq("orchestration check",
                         {"ok": True, "result": {"deliveryId": "d1", "messages": [self.done_msg()]}},
                         {"ok": True, "result": {"deliveryId": None, "messages": []}})
        self.assertEqual(len(self.drive.check(0)), 1)
        self.drive.check(0)
        call = self.calls_for("orchestration check")[-1]
        self.assertEqual(call[call.index("--ack") + 1], "d1")


class ReviewResultTest(DriveCase):
    def test_request_changes_fixes(self):
        self.started()
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES", untracked=["src/tmp.log", "other/x"])
        self.assertEqual((self.state("T-001")["status"], self.state("T-001")["round"]), ("working", 2))
        self.assertIn("src/tmp.log", self.state("T-001")["history"][-1]["note"])

    def test_max_rounds_goes_to_main_once(self):
        self.started()
        self.set_state("T-001", round=3)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        self.assertEqual([e["kind"] for e in self.outbox("decide")], ["max_rounds"])
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)  # 같은 라운드에 다시 묻지 않는다

    def test_repeated_orch_test_failure_goes_to_main(self):
        self.started()
        failed = {"stage": "review", "round": 1, "verdict": "REQUEST_CHANGES", "source": "orch", "tests": {"quick": "failed"}}
        self.set_state("T-001", history=self.state("T-001")["history"] + [failed, {**failed, "round": 2}], round=2)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        self.assertEqual(self.outbox("decide")[-1]["kind"], "repeat_test_failure")

    def test_review_errors(self):
        self.started()
        self.drive.start_review("T-001")
        self.spawned[-1][1].finish(2, "", "orch: 테스트 잠금(exclusive)을 600초 안에 얻지 못했다")
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide"), [])
        self.drive.reconcile()
        self.assertEqual(self.spawned[-1][0], ["review", "T-001"])
        self.spawned[-1][1].finish(2, "", "orch: `[test]` 검증 실패: quick 이 없다")
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide")[-1]["kind"], "test_config")

    def test_pending_dep_fix_after_approve(self):
        self.started("T-001", role="designer")
        self.set_state("T-001", pending_dep_fix="선행 디자인 T-000 가 바뀐다: 색. 바뀐 선행 디자인에 맞춘다")
        self.drive.start_review("T-001")
        self.finish_review("APPROVE")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 2))
        self.assertIn("선행 디자인 T-000", st["history"][-1]["note"])
        self.assertNotIn("pending_dep_fix", st)


class FixReviewTest(DriveCase):
    def test_max_rounds_decide_lifts_after_answer(self):
        self.started()
        self.set_state("T-001", round=3)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)
        ask.answer(self.p, self.outbox("decide")[0]["id"], "그대로 둔다")
        self.drive.reconcile()
        self.assertEqual([e["kind"] for e in self.outbox("decide")], ["max_rounds", "max_rounds"])

    def test_reviewing_task_with_answered_decide_is_rereviewed(self):
        self.started()
        entry = self.drive.decide("escalation", "T-001", "리뷰 실패")
        self.set_state("T-001", status="reviewing")
        self.drive.reconcile()
        self.assertEqual(self.spawned, [])  # 아직 열려 있다
        ask.answer(self.p, entry["id"], "다시 한다")
        self.drive.reconcile()
        self.assertEqual(self.spawned[-1][0], ["review", "T-001"])

    def test_pending_dep_fix_kept_at_max_round(self):
        self.started("T-001", role="designer")
        note = "선행 디자인 T-000 가 바뀐다: 색"
        self.set_state("T-001", pending_dep_fix=note, round=3)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        [entry] = self.outbox("decide")
        self.assertEqual(entry["kind"], "max_rounds")
        self.assertIn(note, entry["summary"])
        self.assertEqual(self.state("T-001")["pending_dep_fix"], note)

    def test_pending_dep_fix_kept_when_fix_fails_after_approve(self):
        self.started("T-001", role="designer")
        note = "선행 디자인 T-000 가 바뀐다: 색"
        self.set_state("T-001", pending_dep_fix=note)
        self.drive.start_review("T-001")
        with mock.patch.object(tasks, "fix", side_effect=OrchError("고치기 거부", 2)):
            self.finish_review("APPROVE")
        self.assertEqual(self.state("T-001")["pending_dep_fix"], note)
        self.assertIn(note, self.outbox("decide")[-1]["summary"])

    def test_late_worker_done_is_ignored(self):
        self.started()
        self.drive.handle_message(self.done_msg(dispatch="ctx_old"))
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.outbox("decide"), [])

    def test_decide_during_review_is_marked_after_child_exits(self):
        self.started()
        self.drive.start_review("T-001")
        entry = self.drive.decide("escalation", "T-001", "참고")
        self.assertNotIn("decide", self.state("T-001"))
        self.spawned[-1][1].finish(3)
        self.drive.poll_jobs()
        self.assertEqual(self.state("T-001")["decide"], entry["id"])


class IsolationTest(DriveCase):
    def msgs(self, *ids):
        return {"ok": True, "result": {"deliveryId": "d", "messages": [
            {"id": i, "type": "escalation", "body": "x", "payload": {}} for i in ids]}}

    def test_failing_message_does_not_stop_tick_and_posts_once(self):
        handled = []

        def handler(msg):
            if msg["id"] == "m1":
                raise KeyError("boom")
            handled.append(msg["id"])
        self.respond("orchestration check", self.msgs("m1", "m2"))
        with mock.patch.object(self.drive, "handle_message", side_effect=handler):
            self.drive.tick(wait_ms=0)
            self.drive.tick(wait_ms=0)
        self.assertEqual(handled, ["m2", "m2"])
        [entry] = self.outbox("decide")
        self.assertIn("KeyError", entry["summary"])

    def test_resets_error_does_not_stop_tick(self):
        self.respond("orchestration check", {"ok": True, "result": {"messages": []}})
        with mock.patch.object(self.drive, "resets", side_effect=OrchError("출력 파싱 실패", 1)):
            self.drive.tick(wait_ms=0)
        self.assertIsNotNone(self.drive.last_reconcile)

    def test_check_failure_flag(self):
        self.respond("orchestration check", {"ok": False}, code=1)
        self.assertEqual(self.drive.check(0), [])
        self.assertTrue(self.drive.check_failed)

    def test_stuck_resetting_is_cleared(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[agents.test]\ntitle = "테스트"\nroles = ["tester"]\n',
                        encoding="utf-8")
        self.drive = drive.Drive(self.p, Config.load(path), spawn=self.fake_spawn)
        agents.update(self.p, "test", lambda e: e.update(resetting={"dispatch_id": "ctx_h", "at": "2026-01-01T00:00:00+00:00"}))
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"stage": "settled", "state": "failed"}, "projection": {"liveness": {"verdict": "exited"}}}})
        self.drive.reconcile()
        self.assertFalse(agents.entry(self.p, "test").get("resetting"))
        self.assertIn("인수인계가 끝나지 않았다", self.outbox("decide")[-1]["summary"])


class AcceptFlowTest(DriveCase):
    def test_planner_accept_applies_plan(self):
        self.started("T-001", role="planner")
        with mock.patch.object(plan, "apply", return_value={"tasks": [
                {"id": "T-002", "role": "developer", "title": "폼", "depends_on": ["T-001"]}]}) as apply:
            self.drive.start_review("T-001")
            self.finish_review("APPROVE")
        apply.assert_called_once()
        self.assertFalse(apply.call_args.args[2])
        self.assertIn("T-002 [developer] 폼 ← T-001", self.outbox("progress")[-1]["body"])

    def test_plan_apply_error_makes_planner_task(self):
        self.started("T-001", role="planner", extra="plan: append\n")
        with mock.patch.object(plan, "apply", side_effect=OrchError("tasks.toml: key 중복", 2)):
            self.drive.start_review("T-001")
            self.finish_review("APPROVE")
        [new] = [st for st in tasks.all_states(self.p) if st["id"] != "T-001"]  # on_review 뒤 start_ready 가 바로 시작할 수 있다
        text = (self.root / f".orchestration/tasks/{new['id']}.md").read_text(encoding="utf-8")
        self.assertIn("key 중복", text)
        self.assertIn("plan: append", text)
        self.assertEqual(new["role"], "planner")


class ReconcileTest(DriveCase):
    def show(self, stage, state):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w1", "stage": stage, "state": state},
            "projection": {"liveness": {"verdict": "exited"}}}})

    def test_settled_worker_without_mail_is_reviewed(self):
        self.started()
        self.show("settled", "succeeded")
        self.drive.reconcile()
        self.assertEqual(self.spawned[-1][0], ["review", "T-001"])

    def test_dead_worker_is_sent_back(self):
        self.started()
        self.show("settled", "failed")
        self.drive.reconcile()
        st = self.state("T-001")
        self.assertEqual(st["round"], 2)
        self.assertIn("이전 작업자가 중간에 끝났다", st["history"][-1]["note"])

    def test_tick_reads_mail_and_queue(self):
        self.started()
        self.respond_seq("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": [self.done_msg()]}})
        self.respond("orchestration check", {"ok": True, "result": {"messages": []}})
        self.drive.tick(wait_ms=0)
        self.assertEqual(self.spawned[0][0], ["review", "T-001"])
        self.assertTrue((self.root / ".orchestration/state/drive/heartbeat").is_file())


class ResetTest(DriveCase):
    def setUp(self):
        super().setUp()
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[agents.test]\ntitle = "테스트"\nroles = ["tester"]\n',
                        encoding="utf-8")
        self.drive = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"), spawn=self.fake_spawn)

    def test_failed_handoff_escalates_once_and_clears_pending(self):
        cfg = self.drive.cfg
        name = "test"
        self.assertIn(name, cfg.agents)
        agents.update(self.p, name, lambda e: e.update(reset_pending=True))
        with mock.patch.object(agents, "begin_reset", side_effect=[{"name": name, "status": "failed"}, None]) as begin:
            self.drive.resets()
            self.drive.resets()
        self.assertEqual(begin.call_count, 2 * len(cfg.agents))
        self.assertEqual(len(self.outbox("decide")), 1)
        self.assertFalse(agents.entry(self.p, name).get("reset_pending"))


if __name__ == "__main__":
    unittest.main()
