import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import ORCH, ProjectCase
from orch import ask, comm
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
WORKER = {"ORCA_TERMINAL_HANDLE": "term_w1", "ORCA_TEAM_ASK_WAIT": "0.2"}
GOOD = {"question": "로그인 방식은?", "example": "이메일이면 가입 화면이 필요하다",
        "options": [{"label": "이메일", "description": "가입 화면 필요"},
                    {"label": "소셜", "description": "외부 연동 필요"}],
        "recommended": 1, "reason": "가입 화면이 줄어든다."}


class AskSchemaTest(unittest.TestCase):
    def problems(self, data):
        with self.assertRaises(OrchError) as cm:
            ask.validate_question(data)
        self.assertEqual(cm.exception.code, 2)
        return str(cm.exception)

    def test_minimal_question_gets_default_header(self):
        q = ask.validate_question({"question": " 언제 쓰나요? ", "example": "출근길 5분"})
        self.assertEqual(q, {"question": "언제 쓰나요?", "example": "출근길 5분", "header": "기획"})

    def test_default_header_follows_asking_role(self):
        minimal = {"question": "어느 톤인가요?", "example": "차분한 모노톤"}
        self.assertEqual(ask.DEFAULT_HEADERS, {"planner": "기획", "director": "디자인"})
        for role, header in (("director", "디자인"), ("planner", "기획"), (None, "기획"), ("developer", "기획")):
            with self.subTest(role=role):
                self.assertEqual(ask.build_render(ask.validate_question(minimal, role))["header"], header)
        self.assertEqual(ask.validate_question({**minimal, "header": "톤 시안"}, "director")["header"], "톤 시안")

    def test_example_prefix_is_stripped_because_render_adds_it(self):
        for example in ("예: 브라우저 저장소", "예:브라우저 저장소", "예) 브라우저 저장소", "예시:  브라우저 저장소",
                        "e.g. 브라우저 저장소", "E.G. 브라우저 저장소", " 예: 브라우저 저장소 "):
            with self.subTest(example=example):
                q = ask.validate_question({"question": "어디에 저장하나요?", "example": example})
                self.assertEqual(q["example"], "브라우저 저장소")
                self.assertTrue(ask.build_render(q)["question"].endswith("\n예: 브라우저 저장소"))
        self.assertEqual(ask.validate_question({"question": "q", "example": "예산은 0원"})["example"], "예산은 0원")
        for example in ("예:", " 예시: ", "e.g."):
            with self.subTest(example=example):
                self.assertIn("example: 필수", self.problems({"question": "q", "example": example}))

    def test_repeated_example_prefixes_are_all_stripped(self):
        for example in ("예: 예: 브라우저 저장소", "e.g. 예시: 브라우저 저장소", "예)예: 예시:  브라우저 저장소"):
            with self.subTest(example=example):
                q = ask.validate_question({"question": "어디에 저장하나요?", "example": example})
                self.assertEqual(ask.build_render(q)["question"], "어디에 저장하나요?\n예: 브라우저 저장소")
        self.assertIn("example: 필수", self.problems({"question": "q", "example": "예: 예:"}))

    def test_missing_and_multiline_fields_are_listed(self):
        msg = self.problems({"question": "첫 줄\n둘째 줄"})
        self.assertIn("question: 한 문장", msg)
        self.assertIn("example: 필수", msg)
        self.assertIn("질문은 JSON 객체", self.problems(["x"]))

    def test_option_count_must_be_2_to_4(self):
        for options in ([GOOD["options"][0]], GOOD["options"] * 3, "a"):
            with self.subTest(options=options):
                self.assertIn("options: 없거나 2~4개", self.problems({**GOOD, "options": options}))

    def test_recommended_and_reason_required_with_options(self):
        msg = self.problems({**GOOD, "recommended": 2, "reason": " "})
        self.assertIn("recommended", msg)
        self.assertIn("reason: 필수", msg)
        self.assertIn("recommended", self.problems({**GOOD, "recommended": True}))
        self.assertIn("recommended", self.problems({k: v for k, v in GOOD.items() if k != "recommended"}))

    def test_length_limits(self):
        msg = self.problems({**GOOD, "question": "가" * 301, "header": "열세글자를넘는꼬리표입니다",
                             "options": [{"label": "가" * 31, "description": "d"}, GOOD["options"][1]]})
        for text in ("question: 300자 이하", "header: 12자 이하", "options[0].label: 30자 이하"):
            self.assertIn(text, msg)

    def test_context_is_optional_markdown_up_to_4000(self):
        q = ask.validate_question({**GOOD, "context": "\n## 요약\n" + "가" * 3990 + "\n"})
        self.assertEqual(q["context"], "## 요약\n" + "가" * 3990)
        self.assertNotIn("context", ask.validate_question({**GOOD, "context": "  "}))
        self.assertIn("context: 4000자 이하", self.problems({**GOOD, "context": "가" * 4001}))
        self.assertIn("context: 마크다운 문자열", self.problems({**GOOD, "context": ["x"]}))


class RenderTest(unittest.TestCase):
    def test_recommended_option_moves_first_with_tag_and_reason(self):
        render = ask.build_render(ask.validate_question({**GOOD, "header": "로그인"}))
        self.assertEqual(render, {
            "header": "로그인", "question": "로그인 방식은?\n예: 이메일이면 가입 화면이 필요하다",
            "options": [{"label": "소셜 (추천)", "description": "추천 사유: 가입 화면이 줄어든다. 외부 연동 필요"},
                        {"label": "이메일", "description": "가입 화면 필요"}],
            "multiSelect": False})

    def test_without_options_render_has_no_options(self):
        render = ask.build_render(ask.validate_question({"question": "목표 사용자는?", "example": "1인 가구 직장인"}))
        self.assertEqual(render, {"header": "기획", "question": "목표 사용자는?\n예: 1인 가구 직장인"})

    def test_context_is_carried_as_render_context(self):
        context = "## 기획 핵심\n- 할 일 웹앱\n\n| key | 직무 |\n|---|---|\n| todo-ui | developer |"
        render = ask.build_render(ask.validate_question({**GOOD, "context": context}))
        self.assertEqual(render["context"], context)
        self.assertEqual(render["question"], "로그인 방식은?\n예: 이메일이면 가입 화면이 필요하다")


class AskCase(ProjectCase):
    """scaffold 한 프로젝트에 working 상태 planner 태스크 T-001 을 둔다."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.set_task("T-001")

    def set_task(self, task_id, role="planner", status="working"):
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": task_id, "role": role, "title": "기획", "status": status, "round": 1,
                                    "owns": ["docs/planning/**"], "history": []}), encoding="utf-8")

    def question_file(self, data=None):
        path = self.tmp / "q.json"
        path.write_text(json.dumps(GOOD if data is None else data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def ask(self, *args, task="T-001", check=False):
        return self.orch("ask-user", "--task", task, *args, check=check, env=WORKER)

    def outbox(self):
        path = self.root / ".orchestration/state/outbox.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class WaitClockTest(AskCase):
    """대기 시계는 모의 객체로 돌린다. 기본 540초, 2초 간격."""

    def test_times_out_after_540_seconds_polling_every_2(self):
        clock = {"t": 1000.0}
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock["t"] += seconds

        with mock.patch.dict(os.environ):
            os.environ.pop("ORCA_TEAM_ASK_WAIT", None)
            out = ask.wait_answer(Project(self.root), "out-0001", clock=lambda: clock["t"], sleep=sleep)
        self.assertEqual(out, {"id": "out-0001", "status": "waiting"})
        self.assertEqual((sum(slept), max(slept), len(slept)), (540.0, 2.0, 270))
        self.assertFalse((self.root / ".orchestration/state/answers/out-0001.read").exists())

    def test_returns_answer_that_arrives_mid_wait_and_marks_read(self):
        p = Project(self.root)
        clock = {"t": 0.0}

        def sleep(seconds):
            clock["t"] += seconds
            if clock["t"] == 6.0:
                ask.write_answer(p, "out-0001", "B안")

        out = ask.wait_answer(p, "out-0001", timeout=540, clock=lambda: clock["t"], sleep=sleep)
        self.assertEqual(out, {"id": "out-0001", "answer": "B안"})
        self.assertEqual(clock["t"], 6.0)
        self.assertTrue((self.root / ".orchestration/state/answers/out-0001.read").exists())


class AskUserTest(AskCase):
    def test_only_working_ask_roles_may_ask(self):
        self.set_task("T-002", role="developer")
        self.set_task("T-003", status="done")
        self.set_task("T-004", role="designer")
        for task_id in ("T-002", "T-003", "T-004", "T-009"):
            proc = self.ask("--json", self.question_file(), task=task_id)
            self.assertEqual(proc.returncode, 2, task_id)
            self.assertIn("ask-user 는 working 상태의 planner·director 태스크만 쓴다", proc.stderr)
        self.assertEqual(self.outbox(), [])
        self.assertEqual(ask.ASK_ROLES, ("planner", "director"))

    def test_director_asks_and_waits_like_planner(self):
        self.set_task("T-002", role="director")
        proc = self.ask("--json", self.question_file(), task="T-002")
        self.assertEqual(proc.returncode, 4, proc.stderr)
        out_id = json.loads(proc.stdout)["id"]
        self.assertEqual((self.outbox()[-1]["type"], self.outbox()[-1]["task"]), ("ask", "T-002"))
        self.orch("answer", out_id, "소셜", env=MAIN)
        out = json.loads(self.ask("--wait", out_id, task="T-002", check=True).stdout)
        self.assertEqual(out, {"id": out_id, "answer": "소셜"})

    def test_question_without_header_uses_asking_role_default(self):
        self.set_task("T-002", role="director")
        self.assertEqual(self.ask("--json", self.question_file(), task="T-002").returncode, 4)
        self.orch("answer", self.outbox()[-1]["id"], "소셜", env=MAIN)
        self.assertEqual(self.ask("--json", self.question_file()).returncode, 4)  # T-001 은 planner
        self.assertEqual([(e["task"], e["render"]["header"]) for e in self.outbox()], [("T-002", "디자인"), ("T-001", "기획")])

    def test_schema_error_lists_fields_and_posts_nothing(self):
        proc = self.ask("--json", self.question_file({"question": "x", "options": [{"label": "a"}]}))
        self.assertEqual(proc.returncode, 2)
        for name in ("example", "options"):
            self.assertIn(name, proc.stderr)
        self.assertEqual(self.outbox(), [])

    def test_times_out_with_exit_4_then_wait_returns_answer(self):
        proc = self.ask("--json", self.question_file())
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"id": "out-0001", "status": "waiting"})
        entry = self.outbox()[0]
        self.assertEqual((entry["type"], entry["task"], entry["body"]), ("ask", "T-001", "로그인 방식은?"))
        self.assertEqual(entry["render"]["options"][0]["label"], "소셜 (추천)")
        self.orch("answer", "out-0001", "소셜", env=MAIN)
        out = json.loads(self.ask("--wait", "out-0001", check=True).stdout)
        self.assertEqual(out, {"id": "out-0001", "answer": "소셜"})
        self.assertTrue((self.root / ".orchestration/state/answers/out-0001.read").exists())

    def test_second_question_while_waiting_is_exit_3(self):
        self.assertEqual(self.ask("--json", self.question_file()).returncode, 4)
        proc = self.ask("--json", self.question_file())
        self.assertEqual(proc.returncode, 3)
        self.assertIn("--wait out-0001", proc.stderr)
        self.assertEqual(len(self.outbox()), 1)
        self.orch("answer", "out-0001", "이메일", env=MAIN)
        self.assertEqual(self.ask("--json", self.question_file()).returncode, 4)  # 답이 오면 다음 질문을 올린다
        self.assertEqual([e["id"] for e in self.outbox()], ["out-0001", "out-0002"])

    def test_wait_rejects_foreign_or_non_ask_id(self):
        self.orch("notify", "--type", "progress", "진행")  # out-0001
        self.set_task("T-002")
        self.ask("--json", self.question_file(), task="T-002")  # out-0002 는 T-002 의 질문
        for out_id in ("out-0001", "out-0002", "out-0099"):
            self.assertEqual(self.ask("--wait", out_id).returncode, 2, out_id)

    def test_pending_returns_unread_answers_once(self):
        self.ask("--json", self.question_file())  # out-0001
        self.orch("answer", "out-0001", "소셜", env=MAIN)
        self.ask("--json", self.question_file())  # out-0002, 답 없음
        out = json.loads(self.ask("--pending", check=True).stdout)
        self.assertEqual(out["pending"], [{"id": "out-0001", "question": "로그인 방식은?", "answer": "소셜"}])
        self.assertEqual(out["waiting"], "out-0002")
        again = json.loads(self.ask("--pending", check=True).stdout)
        self.assertEqual((again["pending"], again["waiting"]), ([], "out-0002"))

    def test_question_from_stdin(self):
        proc = subprocess.run([sys.executable, str(ORCH), "ask-user", "--task", "T-001", "--json", "-"],
                              cwd=self.root, env={**self.env, **WORKER}, input=json.dumps(GOOD, ensure_ascii=False),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(self.outbox()[0]["render"]["header"], "기획")


class AnswerTest(AskCase):
    def test_answer_to_ask_writes_file_once(self):
        self.ask("--json", self.question_file())
        out = json.loads(self.orch("answer", "out-0001", "소셜", env=MAIN).stdout)
        self.assertEqual((out["type"], out["task"]), ("ask", "T-001"))
        saved = json.loads((self.root / ".orchestration/state/answers/out-0001.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["id"], saved["answer"]), ("out-0001", "소셜"))
        self.assertEqual(self.orch("answer", "out-0001", "이메일", check=False, env=MAIN).returncode, 3)
        self.assertEqual(self.calls_for("orchestration send"), [])

    def test_answer_rejects_unknown_progress_and_empty(self):
        self.orch("notify", "--type", "progress", "진행")  # out-0001
        for args in (("out-0099", "x"), ("out-0001", "x"), ("out-0001", "  ")):
            self.assertEqual(self.orch("answer", *args, check=False, env=MAIN).returncode, 2, args)


class OpenQuestionsTest(AskCase):
    """메인이 답하지 않은 질문. 메인이 재시작해도(outbox 커서가 이미 넘어가도) status 와 reask 로 다시 찾는다."""

    def status(self):
        return json.loads(self.orch("status").stdout)["open_questions"]

    def answered(self):
        return json.loads((self.root / ".orchestration/state/answered.json").read_text(encoding="utf-8"))

    def deliver(self):
        """메인 watch 가 지금까지의 outbox 줄을 전했다고 둔다."""
        lines = len(self.outbox())
        (self.root / ".orchestration/state/outbox.cursor").write_text(str(lines), encoding="utf-8")

    def test_ask_is_open_while_planner_works_and_unanswered(self):
        self.ask("--json", self.question_file())  # out-0001, 답 없이 exit 4
        self.assertEqual(self.status(), [{"id": "out-0001", "type": "ask", "task": "T-001"}])
        self.set_task("T-001", status="changes")
        self.assertEqual(self.status(), [])
        self.set_task("T-001")
        self.orch("answer", "out-0001", "소셜", env=MAIN)
        self.assertEqual(self.status(), [])

    def test_question_and_approval_are_open_until_answered_once(self):
        self.orch("notify", "--type", "progress", "진행")  # out-0001
        comm.post_decide(Project(self.root), "worker_failed", "T-001", "실패")  # out-0002
        comm.post(Project(self.root), "approval", "디자인 1개를 확인해 주세요", {"tasks": ["T-002"]})  # out-0003
        self.assertEqual(self.status(), [{"id": "out-0002", "type": "decide"}, {"id": "out-0003", "type": "approval"}])
        self.orch("answer", "out-0002", "A안", env=MAIN)
        self.assertEqual(self.status(), [{"id": "out-0003", "type": "approval"}])
        self.assertEqual(self.answered()["ids"], ["out-0002"])
        proc = self.orch("answer", "out-0002", "B안", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("out-0002", proc.stderr)
        self.assertEqual(self.calls_for("orchestration send"), [])

    def test_failed_send_is_not_recorded(self):
        comm.post_decide(Project(self.root), "worker_question", "T-001", "q", extra={"message_id": "msg_1"})  # out-0001
        with mock.patch("orch.queue.submit", side_effect=OrchError("관제가 꺼져 있다", 1)):
            with self.assertRaises(OrchError):
                ask.answer(Project(self.root), "out-0001", "A안")
        self.assertEqual(self.status(), [{"id": "out-0001", "type": "decide"}])
        self.assertEqual(self.answered()["ids"], [])

    def test_questions_from_before_tracking_are_not_open(self):
        """0.2.0 에서 올라온(답 기록이 없던) question 은 다시 묻지 않는다. 기록은 다음 question·approval 부터 시작한다."""
        outbox = self.root / ".orchestration/state/outbox.jsonl"
        outbox.write_text(json.dumps({"id": "out-0001", "type": "question", "body": "예전 질문", "at": "x"},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(self.status(), [])
        comm.post_decide(Project(self.root), "worker_failed", "T-001", "새 질문")  # out-0002
        self.assertEqual(self.status(), [{"id": "out-0002", "type": "decide"}])
        self.assertEqual(self.answered(), {"since": "out-0002", "ids": []})

    def test_reask_reemits_delivered_open_questions_in_watch_format(self):
        self.ask("--json", self.question_file())  # out-0001 ask
        self.orch("notify", "--type", "progress", "진행")  # out-0002
        comm.post_decide(Project(self.root), "worker_failed", "T-001", "A안과 B안 중 무엇으로 할까요?")  # out-0003
        comm.post_decide(Project(self.root), "worker_failed", "T-001", "답한 질문")  # out-0004
        self.orch("answer", "out-0004", "네", env=MAIN)
        self.deliver()
        comm.post_decide(Project(self.root), "worker_failed", "T-001", "아직 전하지 않은 질문")  # out-0005, watch 가 전한다
        cursor = (self.root / ".orchestration/state/outbox.cursor").read_text(encoding="utf-8")
        out = json.loads(self.orch("reask", env=MAIN).stdout)
        entries = {e["id"]: e for e in self.outbox()}
        self.assertEqual(out, [entries["out-0001"], entries["out-0003"]])
        self.assertEqual((self.root / ".orchestration/state/outbox.cursor").read_text(encoding="utf-8"), cursor)
        self.assertEqual([q["id"] for q in self.status()], ["out-0001", "out-0003", "out-0005"])

    def test_director_ask_is_open_pending_and_reasked(self):
        self.set_task("T-002", role="director")
        self.ask("--json", self.question_file(), task="T-002")  # out-0001, 답 없이 exit 4
        self.assertEqual(self.status(), [{"id": "out-0001", "type": "ask", "task": "T-002"}])
        self.deliver()
        self.assertEqual([e["id"] for e in json.loads(self.orch("reask", env=MAIN).stdout)], ["out-0001"])
        self.orch("answer", "out-0001", "이메일", env=MAIN)
        self.assertEqual(self.status(), [])
        pending = json.loads(self.ask("--pending", task="T-002", check=True).stdout)
        self.assertEqual(pending["pending"], [{"id": "out-0001", "question": "로그인 방식은?", "answer": "이메일"}])


if __name__ == "__main__":
    unittest.main()
