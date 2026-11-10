import json
import os
import unittest
from unittest import mock

from helpers import FAKES
from test_agents import FRESH, LIVE, NEXT, SPLIT, START_FAILED, UNKNOWN, live_output
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

    def test_queued_fix_from_main_carries_and_clears_pending_dep_fix(self):
        self.started("T-001", role="designer")
        pending = "선행 디자인 T-000 가 바뀐다: 색"
        self.set_state("T-001", status="changes", pending_dep_fix=pending)
        from orch import queue
        self.drive_alive()
        queue.submit(self.p, "fix", {"id": "T-001", "note": "추가"}, wait=0)
        self.assertEqual(self.drive.run_queue(), 1)
        st = self.state("T-001")
        self.assertNotIn("pending_dep_fix", st)
        note = st["history"][-1]["note"]
        self.assertIn(pending, note)
        self.assertIn("추가", note)
        self.assertLess(note.index(pending), note.index("추가"))

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

    def test_failed_question_is_actionable(self):
        self.started()
        msg = {"id": "msg_q", "type": "question", "body": "토큰 이름?",
               "payload": {"taskId": "task_1", "dispatchId": "ctx_1"}}
        self.respond("orchestration check", {"ok": True, "result": {"deliveryId": "d", "messages": [msg]}})
        real = self.drive.decide
        calls = []

        def flaky(kind, *a, **kw):
            calls.append(kind)
            if kind == "worker_question":
                raise KeyError("boom")
            return real(kind, *a, **kw)
        with mock.patch.object(self.drive, "decide", side_effect=flaky):
            self.drive.tick(wait_ms=0)
        [entry] = self.outbox("decide")
        self.assertIn("토큰 이름?", entry["summary"])
        self.assertIn("KeyError", entry["summary"])
        self.assertEqual((entry["message_id"], entry["task"]), ("msg_q", "T-001"))

    def test_isolated_reraises_code_12(self):
        with self.assertRaises(OrchError) as cm:
            self.drive.isolated("k", "x", lambda: (_ for _ in ()).throw(OrchError("no orca", 12)))
        self.assertEqual(cm.exception.code, 12)

    def test_failure_key_unset_when_posting_fails(self):
        with mock.patch.object(self.drive, "decide", side_effect=OSError("disk")):
            self.drive.fail("k", "x", KeyError("a"))
        self.assertNotIn("k", self.drive.failures)
        self.drive.fail("k", "x", KeyError("a"))
        self.assertEqual(len(self.outbox("decide")), 1)

    def test_job_failure_is_isolated(self):
        self.started()
        self.drive.start_review("T-001")
        self.spawned[-1][1].finish(0, "{}")
        with mock.patch.object(self.drive, "review_done", side_effect=KeyError("x")):
            self.drive.poll_jobs()
        self.assertEqual(self.drive.jobs, {})
        self.assertIn("job:review:T-001", self.drive.failures)

    def test_check_failure_escalates_once(self):
        self.respond("orchestration check", {"ok": False}, code=1)
        self.drive.check(0)
        self.drive.check(0)
        self.assertEqual(len(self.outbox("decide")), 1)

    def test_stale_escalation_is_ignored(self):
        self.started()
        self.drive.handle_message({"id": "m", "type": "escalation", "body": "멈춤",
                                   "payload": {"taskId": "task_1", "dispatchId": "ctx_old"}})
        self.assertEqual(self.outbox("decide"), [])

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

    def test_one_failing_task_does_not_stop_reconcile(self):
        self.started("T-001")
        tasks.save_state(self.p, {**self.state("T-001"), "id": "T-002"})
        seen = []

        def fake(st):
            seen.append(st["id"])
            if st["id"] == "T-001":
                raise KeyError("boom")
        with mock.patch.object(self.drive, "reconcile_task", side_effect=fake):
            self.drive.reconcile()
        self.assertEqual(seen, ["T-001", "T-002"])

    def test_tick_reads_mail_and_queue(self):
        self.started()
        self.respond_seq("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": [self.done_msg()]}})
        self.respond("orchestration check", {"ok": True, "result": {"messages": []}})
        self.drive.tick(wait_ms=0)
        self.assertEqual(self.spawned[0][0], ["review", "T-001"])
        self.assertTrue((self.root / ".orchestration/state/drive/heartbeat").is_file())


class SettleResetTest(DriveCase):
    def setUp(self):
        super().setUp()
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[agents.test]\ntitle = "테스트"\nroles = ["tester"]\n',
                        encoding="utf-8")
        self.drive = drive.Drive(self.p, Config.load(path), spawn=self.fake_spawn)

    def set_resetting(self, at):
        from datetime import datetime
        at = at or datetime.now().astimezone().isoformat()
        agents.update(self.p, "test", lambda e: e.update(resetting={"dispatch_id": "ctx_h", "at": at}))

    def show(self, stage, state, verdict="alive"):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"stage": stage, "state": state}, "projection": {"liveness": {"verdict": verdict}}}})

    def test_settled_failed_with_recent_at_escalates(self):
        self.set_resetting(None)
        self.show("settled", "failed")
        self.drive.reconcile()
        self.assertFalse(agents.entry(self.p, "test").get("resetting"))
        self.assertIn("인수인계가 끝나지 않았다", self.outbox("decide")[-1]["summary"])

    def test_recent_live_handoff_is_left_alone(self):
        self.set_resetting(None)
        self.show("running", None)
        self.drive.reconcile()
        self.assertTrue(agents.entry(self.p, "test").get("resetting"))
        self.assertEqual(self.outbox("decide"), [])


class AgentDriveCase(DriveCase):
    """생산 고정 에이전트(planner·designer·developer)가 있는 관제."""

    def setUp(self):
        super().setUp()
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8")
                        + '\n[agents.production]\ntitle = "생산"\nroles = ["planner", "designer", "developer"]\n',
                        encoding="utf-8")
        self.respond("orchestration worker-start", FRESH)
        self.respond("terminal split", SPLIT)
        self.respond("terminal show", LIVE)
        self.respond("orchestration task-create", {"ok": True, "result": {"taskId": "task_2"}})
        self.drive = drive.Drive(self.p, Config.load(path), spawn=self.fake_spawn)


class StartWaitTest(AgentDriveCase):
    def test_start_request_during_reset_waits_then_starts(self):
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec")
        agents.update(self.p, "production", lambda e: e.update(resetting={"dispatch_id": "ctx_h", "at": "x"}))
        out = self.drive.execute({"command": "start", "args": {"id": "T-001"}})
        self.assertEqual((out["status"], out["reason"]), ("waiting", "agent"))
        self.drive.finish_step()
        self.assertEqual(self.outbox("done"), [])
        agents.update(self.p, "production", lambda e: e.update(resetting=None))
        self.drive.start_ready()
        self.assertEqual(self.state("T-001")["status"], "working")


class QueuedFixReconcileTest(AgentDriveCase):
    def test_working_task_with_queued_fix_keeps_main_note(self):
        """worker_failed 뒤 메인의 fix 가 바쁜 에이전트 대기열에 들어갔다. 재확인이 그 지시를 DIED_NOTE 로 덮지 않는다."""
        self.started("T-001")
        self.drive.handle_message(self.done_msg(outcome="failed"))
        self.respond("orchestration worker-start", NEXT)
        self.started("T-002", role="planner")
        self.assertEqual(agents.entry(self.p, "production")["task"], "T-002")

        def check(st):
            if st["id"] == "T-001":
                return False, "stage=settled", {"stage": "settled", "state": "failed"}
            return True, "stage=running", {"stage": "running"}
        with mock.patch.object(tasks, "worker_check", side_effect=check):
            out = self.drive.execute({"command": "fix", "args": {"id": "T-001", "note": "원인: X. Y 를 고친다"}})
            self.assertEqual(out["status"], "queued")
            ask.answer(self.p, self.outbox("decide")[-1]["id"], "fix 로 다시 보냈다")
            self.drive.reconcile()
        self.assertEqual(self.state("T-001")["queued_fix"]["note"], "원인: X. Y 를 고친다")
        self.assertEqual(agents.entry(self.p, "production")["queue"], ["T-001"])


class DroppedDependencyTest(DriveCase):
    def test_pending_with_dropped_dependency_escalates_once(self):
        self.write_spec("T-001")
        self.write_spec("T-002", role="tester", extra="depends_on: T-001\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "specs")
        base = {"stage": "work", "round": 1, "base": None, "task_id": None, "dispatch_id": None, "history": [],
                "verdicts": {}}
        tasks.save_state(self.p, {**base, "id": "T-001", "role": "developer", "title": "a", "status": "dropped",
                                  "owns": ["src/**"], "depends_on": []})
        tasks.save_state(self.p, {**base, "id": "T-002", "role": "tester", "title": "b", "status": "pending",
                                  "owns": ["tests/**"], "depends_on": ["T-001"]})
        self.drive.reconcile()
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"]), ("escalation", "T-002"))
        self.assertIn("T-001", entry["summary"])
        self.assertIn('orch drop T-002 --reason "<이유>"', entry["commands"])
        self.assertIn("depends_on 을 고쳐 커밋한 뒤 orch start T-002", entry["commands"])
        self.drive.reconcile()
        ask.answer(self.p, entry["id"], "확인")
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)


class ReplyTest(DriveCase):
    def test_reply_failure_names_fix_and_scope(self):
        self.respond("orchestration reply", {"ok": False, "error": {"code": "message_not_found"}}, code=1)
        with self.assertRaises(OrchError) as ctx:
            self.drive.execute({"command": "reply", "args": {"message_id": "msg_9", "body": "답", "id": "T-001"}})
        self.assertIn('orch fix T-001 --note "<답>"', str(ctx.exception))
        self.assertIn('orch scope T-001 "<답>"', str(ctx.exception))


class FinishedTaskMailTest(DriveCase):
    def test_worker_done_for_dropped_task_is_ignored(self):
        self.started()
        self.set_state("T-001", status="dropped")
        for outcome in ("succeeded", "failed"):
            self.drive.handle_message(self.done_msg(outcome=outcome))
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.outbox("decide"), [])


class LaunchFailedReconcileTest(DriveCase):
    WORKERS = {"ok": True, "result": {"workers": [
        {"dispatchId": "ctx_9", "state": "running", "task": {"title": "T-001 work r1 [developer] 로그인"}},
        {"dispatchId": "ctx_8", "state": "running", "task": {"title": "T-002 work r1 [developer] 가입"}}]}}

    def test_crashed_launch_is_escalated_once_with_leftover_workers(self):
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec")
        tasks.save_state(self.p, {"id": "T-001", "role": "developer", "title": "로그인", "status": "launch_failed",
                                  "stage": "work", "round": 1, "base": "x", "owns": ["src/**"], "depends_on": [],
                                  "task_id": None, "dispatch_id": None, "verdicts": {}, "agent": "temp",
                                  "history": [{"stage": "work", "round": 1, "at": "2026-01-01T00:00:00+00:00",
                                               "ok": None, "pending": True, "pid": 0}]})
        self.respond("orchestration worker-list", self.WORKERS)
        self.drive.reconcile()
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"]), ("escalation", "T-001"))
        self.assertIn("ctx_9", entry["summary"])
        self.assertNotIn("ctx_8", entry["summary"])
        self.assertEqual(entry["commands"], ["orch start T-001 --retry", 'orch drop T-001 --reason "<이유>"'])
        self.drive.reconcile()
        ask.answer(self.p, entry["id"], "확인")
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)

    def test_start_ready_launch_failure_is_escalated_once(self):
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec")
        tasks.save_state(self.p, {"id": "T-001", "role": "developer", "title": "로그인", "status": "pending",
                                  "stage": "work", "round": 1, "base": None, "owns": ["src/**"], "depends_on": [],
                                  "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}})
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "x"}}, code=1)
        self.respond("orchestration worker-list", {"ok": True, "result": {"workers": []}})
        self.drive.start_ready()
        [entry] = self.outbox("decide")
        self.assertIn("worker-start 실패", entry["summary"])
        self.assertIn("남은 워커 없음", entry["summary"])
        ask.answer(self.p, entry["id"], "확인")
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)


class GuardLaunchFailedTest(DriveCase):
    def test_internal_fix_launch_failure_posts_one_decide_with_retry_commands(self):
        self.started()

        def failing_fix(p, cfg, task_id, *args, **kw):
            st = tasks.load_state(p, task_id)
            st.update(status="launch_failed", stage="fix", round=2)
            tasks.save_state(p, st)
            raise OrchError("worker-start 실패", 1)

        self.respond("orchestration worker-list", {"ok": True, "result": {"workers": []}})
        with mock.patch.object(tasks, "fix", side_effect=failing_fix):
            self.drive.run_fix("T-001", "고친다")
        self.drive.reconcile()
        [entry] = self.outbox("decide")
        self.assertEqual(entry["commands"], ["orch fix T-001 --retry", 'orch drop T-001 --reason "<이유>"'])
        self.assertIn("worker-start 실패", entry["summary"])


class PlanRecoveryTest(DriveCase):
    def test_planner_accept_recovers_after_crash_before_apply(self):
        self.started("T-001", role="planner")
        self.set_state("T-001", status="approved")
        with mock.patch.object(drive.Drive, "apply_plan", side_effect=RuntimeError("관제가 죽었다")):
            with self.assertRaises(RuntimeError):
                self.drive.accepted("T-001", tasks.accept(self.p, "T-001"))
        self.assertIs(self.state("T-001")["plan_applied"], False)
        tasks.save_state(self.p, {**self.state("T-001"), "id": "T-002"})  # 예전에 끝난 기획 태스크(표시 없음)
        self.set_state("T-002", plan_applied=None)
        with mock.patch.object(plan, "apply", return_value={"tasks": []}) as apply:
            self.drive.reconcile()
            self.drive.reconcile()
        apply.assert_called_once()
        self.assertIs(self.state("T-001")["plan_applied"], True)


    def test_planner_without_append_after_applied_plan_does_not_loop(self):
        self.p.plan_json.write_text(json.dumps({"tasks": {"a": "T-009"}, "digests": {}}), encoding="utf-8")
        self.started("T-001", role="planner")
        self.set_state("T-001", status="approved")
        calls = []

        def fake_apply(p, cfg, append):
            calls.append(append)
            raise OrchError("새 key 가 없다" if append else "계획을 이미 적용했다(state/plan.json)", 2)

        before = len(list(self.p.tasks_dir.glob("T-*.md")))
        with mock.patch.object(plan, "apply", side_effect=fake_apply):
            self.drive.accepted("T-001", tasks.accept(self.p, "T-001"))
        self.assertEqual(calls, [True])
        self.assertEqual(len(list(self.p.tasks_dir.glob("T-*.md"))), before)
        self.assertIs(self.state("T-001")["plan_applied"], True)


class LivenessTest(AgentDriveCase):
    UNKNOWN = {"ok": False, "error": {"code": "x"}}
    STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}

    def test_unknown_liveness_with_dead_agent_terminal_is_treated_as_finished(self):
        self.started("T-001")
        self.respond("orchestration worker-show", self.UNKNOWN, code=1)
        self.respond("terminal show", self.STALE, code=1)
        self.drive.reconcile()
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 2))
        self.assertIn("이전 작업자가 중간에 끝났다", st["history"][-1]["note"])

    def test_unknown_liveness_escalates_once_after_cap_and_never_kills(self):
        self.started("T-001")
        self.respond("orchestration worker-show", self.UNKNOWN, code=1)
        self.drive.reconcile()
        self.assertEqual(self.outbox("decide"), [])
        self.assertTrue(self.state("T-001")["liveness_unknown_since"])
        self.set_state("T-001", liveness_unknown_since="2026-01-01T00:00:00+00:00")
        self.drive.reconcile()
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"]), ("escalation", "T-001"))
        self.assertIn("확인하지 못했다", entry["summary"])
        ask.answer(self.p, entry["id"], "기다린다")
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 1))


class UnknownStartDriveTest(AgentDriveCase):
    def test_working_session_is_not_relaunched_and_uses_liveness_cap(self):
        """start_unknown 인데 작업 터미널이 일하는 중이면 다시 띄우지 않는다. 오래 이어지면 생존 모름 규칙으로 한 번 올린다."""
        self.started("T-001")
        self.respond("orchestration worker-show", UNKNOWN)
        self.respond("terminal show", live_output(1))
        history = self.state("T-001")["history"]
        history[-1]["at"] = "2026-01-01T00:00:00+00:00"  # UNKNOWN_GRACE_SECONDS 가 지났다
        self.set_state("T-001", history=history)
        starts = len(self.calls_for("orchestration worker-start"))
        self.drive.reconcile()
        self.drive.reconcile()
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])
        self.assertEqual(self.outbox("decide"), [])
        self.assertTrue(self.state("T-001")["liveness_unknown_since"])
        self.set_state("T-001", liveness_unknown_since="2026-01-01T00:00:00+00:00")
        self.drive.reconcile()
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"]), ("escalation", "T-001"))
        self.assertIn("확인하지 못했다", entry["summary"])
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)
        self.assertEqual(len([h for h in self.state("T-001")["history"] if h.get("verdict") == "busy"]), 1)


class TempLivenessTest(DriveCase):
    def test_unknown_verdict_with_dead_worker_terminal_is_treated_as_finished(self):
        self.started()
        self.respond("orchestration worker-show", {"ok": True, "result": {"worker": {
            "agentTerminalHandle": "term_w1", "stage": "running"}}})
        self.respond("terminal show", {"ok": False, "error": {"code": "terminal_handle_stale"}}, code=1)
        self.drive.reconcile()
        self.assertEqual(self.state("T-001")["round"], 2)


class MaxRoundCommandTest(DriveCase):
    def test_failed_worker_at_max_round_suggests_extra_round(self):
        self.started()
        self.set_state("T-001", round=3)
        self.drive.handle_message(self.done_msg(outcome="failed"))
        [entry] = self.outbox("decide")
        self.assertIn('orch fix T-001 --extra-round --note "<원인과 지시>"', entry["commands"])

    def test_repeat_test_failure_at_max_round_suggests_extra_round(self):
        self.started()
        failed = {"stage": "review", "round": 2, "verdict": "REQUEST_CHANGES", "source": "orch", "tests": {"quick": "failed"}}
        self.set_state("T-001", history=self.state("T-001")["history"] + [failed, {**failed, "round": 3}], round=3)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        entry = self.outbox("decide")[-1]
        self.assertEqual(entry["kind"], "repeat_test_failure")
        self.assertIn('orch fix T-001 --extra-round --note "<지시>"', entry["commands"])


class HeartbeatTest(DriveCase):
    def test_start_ready_and_design_decide_get_heartbeat(self):
        with mock.patch.object(plan, "start_ready", return_value={"started": [], "waiting": [], "fixes": []}) as ready:
            self.drive.start_ready()
            self.drive.execute({"command": "start-ready", "args": {}})
        for call in ready.call_args_list:
            self.assertEqual(call.kwargs.get("beat"), self.drive.beat)
        with mock.patch.object(plan, "design_decide", return_value={}) as decide:
            self.drive.execute({"command": "design-decide", "args": {"id": "out-0001", "approve": [], "fixes": {}}})
        self.assertEqual(decide.call_args.kwargs.get("beat"), self.drive.beat)

    def test_finish_reset_gets_heartbeat(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[agents.test]\ntitle = "테스트"\nroles = ["tester"]\n',
                        encoding="utf-8")
        self.drive = drive.Drive(self.p, Config.load(path), spawn=self.fake_spawn)
        agents.update(self.p, "test", lambda e: e.update(resetting={"dispatch_id": "ctx_h", "at": "x"}))
        with mock.patch.object(agents, "finish_reset", return_value={"status": "reset"}) as finish:
            self.drive.handle_message({"id": "m", "type": "worker_done", "body": "",
                                       "payload": {"dispatchId": "ctx_h", "outcome": "succeeded"}})
        self.assertEqual(finish.call_args.kwargs.get("beat"), self.drive.beat)


class BindTest(DriveCase):
    def test_bind_retries_transient_failure_with_heartbeat(self):
        beats = []
        with mock.patch.object(self.drive, "bind", side_effect=[OrchError("run-use 실패", 1), {"status": "attached"}]), \
                mock.patch.object(self.drive, "beat", side_effect=lambda: beats.append(1)):
            self.assertEqual(self.drive.bind_retry(sleep=lambda s: None), {"status": "attached"})
        self.assertTrue(beats)
        self.assertIn("Run 연결 실패(1/", self.p.drive_log.read_text(encoding="utf-8"))

    def test_fatal_error_goes_to_drive_log(self):
        cfg = self.drive.cfg
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": "term_drive"}), \
                mock.patch.object(drive.Drive, "bind", side_effect=OrchError("run-use 실패", 1)), \
                mock.patch.object(drive.time, "sleep"):
            os.environ.pop("CLAUDECODE", None)
            with self.assertRaises(OrchError):
                drive.main(self.p, cfg)
        log = self.p.drive_log.read_text(encoding="utf-8")
        self.assertIn("관제 종료", log)
        self.assertIn("run-use 실패", log)
        self.assertFalse(self.p.drive_pid.exists())


class ResetTest(DriveCase):
    def setUp(self):
        super().setUp()
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[agents.test]\ntitle = "테스트"\nroles = ["tester"]\n',
                        encoding="utf-8")
        self.drive = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"), spawn=self.fake_spawn)

    def test_failed_handoff_retries_then_escalates_once_with_cause(self):
        """인수인계를 넣지 못하면 리셋 표시를 두고 다음 틱에 다시 한다. 3번째 실패에서만 원인과 함께 한 번 올린다."""
        name = "test"
        self.assertIn(name, self.drive.cfg.agents)
        agents.update(self.p, name, lambda e: e.update(reset_pending=True, handle="term_t1", since_reset=1))
        self.respond("terminal show", live_output(300))  # 쉬는 세션
        self.respond("orchestration task-create", {"ok": True, "result": {"taskId": "task_h"}})
        self.respond("orchestration worker-start", START_FAILED, code=1)
        for count in (1, 2):
            self.drive.resets()
            self.assertEqual(self.outbox("decide"), [])
            e = agents.entry(self.p, name)
            self.assertEqual((e["reset_pending"], e["reset_failures"]), (True, count))
        self.drive.resets()
        [entry] = self.outbox("decide")
        self.assertEqual(entry["kind"], "escalation")
        self.assertIn("terminal_busy: terminal is not accepting input", entry["summary"])
        self.assertIn("자동 압축 중이거나 다른 일을 하면 실패한다. 화면을 본 뒤 다시 요청한다", entry["summary"])
        self.assertEqual(entry["commands"], [f"orch agent reset {name}"])
        e = agents.entry(self.p, name)
        self.assertEqual((e["reset_pending"], "reset_failures" in e), (False, False))
        self.assertEqual([self.arg(c, "--status") for c in self.calls_for("orchestration task-update")], ["failed"] * 3)
        log = self.p.drive_log.read_text(encoding="utf-8")
        self.assertIn(f"{name} 리셋 시작: failed(3/3): terminal_busy: terminal is not accepting input", log)
        self.drive.resets()
        self.assertEqual(len(self.outbox("decide")), 1)

    def test_unknown_terminal_cap_escalates_once_and_frees_roles(self):
        name = "test"
        agents.update(self.p, name, lambda e: e.update(reset_pending=True, handle="term_x",
                                                       reset_unknown_since="2020-01-01T00:00:00+00:00"))
        with mock.patch.object(agents.orca, "terminal_state", return_value="unknown"):
            self.drive.resets()
            self.drive.resets()
        [entry] = self.outbox("decide")
        self.assertEqual(entry["kind"], "escalation")
        self.assertIn(name, entry["summary"])
        self.assertFalse(agents.entry(self.p, name).get("reset_pending"))


if __name__ == "__main__":
    unittest.main()
