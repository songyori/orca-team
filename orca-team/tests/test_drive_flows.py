import json
import unittest
from unittest import mock

from helpers import fake_test_commands, write_test_config
from test_drive import DriveCase
from orch import comm, followups, plan, tasks


def item(task, text, item_id=None, rnd=1):
    return {"task": task, "round": rnd, "id": item_id, "text": text}


class BatchFollowupsTest(DriveCase):
    def setUp(self):
        super().setUp()
        self.started("T-001", extra="owns: src/login/**\n")
        self.set_state("T-001", status="done")

    def test_regressions_cleanup_and_gaps(self):
        found = {"tasks": ["T-001", "T-002"],
                 "regressions": [item("T-002", "로그인 실패 테스트가 깨진다. 원인 T-001"), item("T-002", "원인 모름")],
                 "followups": [item("T-001", "변수 이름", "r1-F2")],
                 "planning_gaps": [item("T-001", "비밀번호 찾기 화면이 spec 에 없다")]}
        with mock.patch.object(followups, "followups", return_value=found):
            self.drive.batch_done("T-002")
            self.drive.batch_done("T-002")  # 다시 불러도 만들지 않는다
        created_tasks = [st for st in tasks.all_states(self.p) if st["id"] != "T-001"]  # start_ready 가 하나는 바로 띄운다
        self.assertEqual(len(created_tasks), 2)
        texts = {st["title"]: (self.root / f".orchestration/tasks/{st['id']}.md").read_text(encoding="utf-8") for st in created_tasks}
        regression = next(t for title, t in texts.items() if title.startswith("회귀 수정"))
        self.assertIn("## Change\n출처: T-002 followups\n로그인 실패", regression)
        self.assertIn("owns: src/login/**", regression)
        cleanup = texts["묶음 정리: T-002"]
        self.assertIn("- r1-F2: 변수 이름", cleanup)
        self.assertEqual(sorted(e["kind"] for e in self.outbox("decide")), ["planning_gaps", "unknown_regression"])
        self.assertIn("묶음 완료: T-002", self.outbox("progress")[-1]["body"])


class FinishTest(DriveCase):
    def all_done(self):
        self.started("T-001")
        self.set_state("T-001", status="done")

    def test_done_is_reported_once_without_test_config(self):
        self.all_done()
        with mock.patch.object(followups, "followups", return_value={"tasks": [], "regressions": [], "followups": [],
                                                                     "planning_gaps": []}):
            self.drive.finish_step()
            self.drive.finish_step()
        [done] = self.outbox("done")
        self.assertIn("T-001", done["body"])

    def test_final_test_runs_then_reports(self):
        self.all_done()
        quick, full = fake_test_commands(self)
        write_test_config(self.root, quick, full)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test config")
        with mock.patch.object(followups, "followups", return_value={"tasks": [], "regressions": [], "followups": [],
                                                                     "planning_gaps": []}):
            self.drive.finish_step()
        args, proc = self.spawned[-1]
        self.assertEqual(args, ["test", "--full"])
        proc.finish(1, json.dumps({"status": "failed", "tail": "FAIL a", "log": "/tmp/x.log"}))
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide")[-1]["kind"], "unknown_regression")
        self.drive.finish_step()
        self.assertEqual(len(self.spawned), 1)  # 같은 태스크 묶음으로는 다시 돌리지 않는다


class DesignFlowTest(DriveCase):
    def awaiting_screen(self, task_id, proposal="없음"):
        self.write_spec(task_id, role="designer", extra=f"owns: docs/design/{task_id}/**\n")
        report = self.root / f"docs/tasks/{task_id}/report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"## r1\n바꾼 파일\n\n### 시스템 추가 제안\n{proposal}\n", encoding="utf-8")
        (self.root / f".orchestration/state/tasks/{task_id}.json").write_text(json.dumps({
            "id": task_id, "role": "designer", "title": "화면", "status": "awaiting_user", "stage": "work", "round": 1,
            "base": self.git("rev-parse", "HEAD").strip(), "owns": [f"docs/design/{task_id}/**"], "depends_on": [],
            "agent": "temp", "task_id": "t", "dispatch_id": "d", "released": True, "history": [], "verdicts": {}}),
            encoding="utf-8")

    def test_no_proposals_asks_for_approval(self):
        self.awaiting_screen("T-001")
        with mock.patch.object(plan, "design_review", return_value={"id": "out-0001", "tasks": ["T-001"]}) as review:
            self.drive.design_step()
        review.assert_called_once()

    def test_proposals_make_supplement_then_fix_screens(self):
        self.awaiting_screen("T-001", proposal="강조 버튼 토큰이 필요하다(결제 화면)")
        with mock.patch.object(plan, "design_review") as review:
            self.drive.design_step()
        review.assert_not_called()
        [sup] = [st for st in tasks.all_states(self.p) if st.get("role") == "director"]
        spec = (self.root / f".orchestration/tasks/{sup['id']}.md").read_text(encoding="utf-8")
        self.assertIn("- T-001 r1: 강조 버튼 토큰이 필요하다", spec)
        self.assertIn("owns: docs/design/system/**, docs/design/direction.md", spec)
        self.set_state(sup["id"], status="done")
        with mock.patch.object(plan, "design_review") as review:
            self.drive.design_step()
        st = self.state("T-001")
        self.assertEqual((st["status"], st["history"][-1]["supplement"]), ("working", sup["id"]))
        self.assertEqual(self.outbox("decide")[-1]["kind"], "token_update")

    def test_recorded_proposal_is_not_collected_again(self):
        self.awaiting_screen("T-001", proposal="강조 버튼 토큰")
        (self.root / "docs/design").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/design/direction.md").write_text("- 강조 버튼(T-001 r1): 받아들임\n", encoding="utf-8")
        with mock.patch.object(plan, "design_review", return_value={"id": "out-0001", "tasks": ["T-001"]}) as review:
            self.drive.design_step()
        review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
