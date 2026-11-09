import json
import os
import unittest
from unittest import mock

from helpers import FAKES
from test_tasks import TaskCase
from orch import ask, comm, plan, tasks
from orch.config import Config
from orch.paths import OrchError, Project


class DecideCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": self.env["ORCA_TERMINAL_HANDLE"],
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cfg(self):
        return Config.load(self.root / ".orchestration/config.toml")


class AnswerDecideTest(DecideCase):
    def test_decide_is_closed_once(self):
        entry = comm.post_decide(self.p, "worker_failed", "T-001", "실패")
        self.assertIn(entry["id"], [q["id"] for q in ask.open_questions(self.p)])
        ask.answer(self.p, entry["id"], "fix 로 다시 보냈다")
        self.assertNotIn(entry["id"], [q["id"] for q in ask.open_questions(self.p)])
        with self.assertRaises(OrchError) as ctx:
            ask.answer(self.p, entry["id"], "또")
        self.assertEqual(ctx.exception.code, 3)

    def test_worker_question_reply_goes_through_queue(self):
        entry = comm.post_decide(self.p, "worker_question", "T-001", "토큰 이름?", extra={"message_id": "msg_9",
                                                                                          "question": "토큰 이름?"})
        with mock.patch("orch.queue.submit", return_value={"replied": "msg_9"}) as submit:
            out = ask.answer(self.p, entry["id"], "color.primary 를 쓴다")
        submit.assert_called_once_with(self.p, "reply", {"message_id": "msg_9", "body": "color.primary 를 쓴다"})
        self.assertEqual(out["sent"], {"replied": "msg_9"})

    def test_approval_answer_points_to_design_decide(self):
        entry = comm.post(self.p, "approval", "디자인 확인", {"tasks": ["T-001"]})
        with self.assertRaises(OrchError) as ctx:
            ask.answer(self.p, entry["id"], "전부 승인")
        self.assertIn("design-decide", str(ctx.exception))


class DesignDecideTest(DecideCase):
    def awaiting(self, task_id, depends=()):
        extra = f"owns: docs/design/{task_id}/**\n" + (f"depends_on: {', '.join(depends)}\n" if depends else "")
        self.write_spec(task_id, role="designer", extra=extra)  # 화면마다 소유 경로가 달라야 fix 의 잠금 검사를 지난다
        (self.root / f".orchestration/state/tasks/{task_id}.json").write_text(json.dumps({
            "id": task_id, "role": "designer", "title": "화면", "status": "awaiting_user", "stage": "work", "round": 1,
            "base": self.git("rev-parse", "HEAD").strip(), "owns": [f"docs/design/{task_id}/**"],
            "depends_on": list(depends), "agent": "temp", "task_id": "t", "dispatch_id": "d", "released": True,
            "history": [], "verdicts": {"1": "APPROVE"}}), encoding="utf-8")

    def test_fix_spreads_to_dependent_screen(self):
        self.awaiting("T-001")
        self.awaiting("T-002", depends=["T-001"])
        self.awaiting("T-003")
        entry = comm.post(self.p, "approval", "디자인 3개", {"tasks": ["T-001", "T-002", "T-003"]})
        out = plan.design_decide(self.p, self.cfg(), entry["id"], ["T-002", "T-003"], {"T-001": "색을 밝게"})
        self.assertEqual((out["approved"], out["fixed"], out["dependent_fixes"]), (["T-003"], ["T-001"], ["T-002"]))
        notes = [json.loads((self.root / f".orchestration/state/tasks/{t}.json").read_text(encoding="utf-8"))
                 for t in ("T-001", "T-002", "T-003")]
        self.assertEqual([n["status"] for n in notes], ["working", "working", "done"])
        self.assertIn("선행 디자인 T-001 가 바뀐다: 색을 밝게", notes[1]["history"][-1]["note"])
        self.assertIn(entry["id"], comm.load_answered(self.p)["ids"])

    def test_every_screen_needs_a_decision(self):
        self.awaiting("T-001")
        self.awaiting("T-002")
        entry = comm.post(self.p, "approval", "디자인 2개", {"tasks": ["T-001", "T-002"]})
        with self.assertRaises(OrchError) as ctx:
            plan.design_decide(self.p, self.cfg(), entry["id"], ["T-001"], {})
        self.assertIn("T-002", str(ctx.exception))
        with self.assertRaises(OrchError):
            plan.design_decide(self.p, self.cfg(), entry["id"], ["T-001", "T-002"], {"T-001": "x"})

    def test_working_dependent_is_deferred(self):
        self.awaiting("T-001")
        self.awaiting("T-002", depends=["T-001"])
        self.set_state("T-002", status="working")
        entry = comm.post(self.p, "approval", "디자인 1개", {"tasks": ["T-001"]})
        out = plan.design_decide(self.p, self.cfg(), entry["id"], [], {"T-001": "여백"})
        self.assertEqual(out["deferred"], ["T-002"])
        self.assertIn("T-001", self.state("T-002")["pending_dep_fix"])

    def test_cli_parses_fix_pairs(self):
        self.awaiting("T-001")
        entry = comm.post(self.p, "approval", "디자인 1개", {"tasks": ["T-001"]})
        out = json.loads(self.orch("design-decide", entry["id"], "--fix", "T-001=제목을 키운다 = 크게").stdout)
        self.assertEqual(out["fixed"], ["T-001"])
        self.assertEqual(self.state("T-001")["history"][-1]["note"], "제목을 키운다 = 크게")


class ScopeTest(DecideCase):
    def test_scope_sends_to_dispatch(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        out = tasks.scope(self.p, "T-001", "-다크 모드도 맞춘다")
        self.assertTrue(out["sent"])
        call = self.calls_for("orchestration send")[-1]
        self.assertEqual(self.arg(call, "--to"), "dispatch:ctx_1")
        self.assertIn("--body=-다크 모드도 맞춘다", call)
        self.set_state("T-001", status="changes")
        with self.assertRaises(OrchError):
            tasks.scope(self.p, "T-001", "x")


if __name__ == "__main__":
    unittest.main()
