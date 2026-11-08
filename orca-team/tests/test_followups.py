import json
import unittest

from test_tasks import TaskCase
from orch import followups

REVIEW = """# T-001 r2 리뷰
## 판정 근거
근거
## 이전 지적 확인
- r1-F1: 해결
## 지적 사항
- 없음
## 후속
- r2-F2 [P2] src/a.ts:3 — 이름이 모호하다. 바꾼다.
  둘째 줄 설명
- r2-F3 [P3] src/a.ts:9 — 주석 오타.
## 기획 누락
- r2-F4 빈 목록일 때 문구가 spec 에 없다. 빈 상태가 필요하다.
## 회귀
- 해당 없음
## 직접 확인한 것
- 읽음

VERDICT: APPROVE
"""
OLD_R1 = "# T-001 r1 리뷰\n## 지적 사항\n- 없음\n## 후속\n- r1-F9 [P2] 옛 지적\n\nVERDICT: REQUEST_CHANGES\n"
TESTER_REVIEW = """# T-003 r1 리뷰
## 지적 사항
- 없음
## 후속
- 없음
## 기획 누락
- 없음
## 회귀
- tests/grp/a.test.ts > 합계 — T-002. 합계가 틀린다.

VERDICT: APPROVE
"""
DERIVED_SPEC = """---
id: {id}
role: developer
title: 회귀 수정
owns: src/**
---
## Target
src/

## Change
출처: T-003 followups
- 합계 고치기

## Constraints
없음

## Acceptance
- orch test 통과
"""


class SectionItemsTest(unittest.TestCase):
    def test_none_with_reason_is_empty(self):
        text = ("## 회귀\n- 해당 없음(묶음 리뷰 아님)\n- 해당 없음 — 묶음 리뷰가 아니다\n- (없음)\n- 없음.\n- 없음\n"
                "- 해당 없음\n- 없음표 정리 — T-002. 근거.\n- r1-F4 tests/a.test.ts — T-002. 근거.\n\nVERDICT: APPROVE\n")
        self.assertEqual(followups.section_items(text, "회귀"),
                         ["없음표 정리 — T-002. 근거.", "r1-F4 tests/a.test.ts — T-002. 근거."])


class FollowupsTest(TaskCase):
    def put_state(self, task_id, role, status="done", depends_on=(), rounds=(1,)):
        st = {"id": task_id, "role": role, "title": task_id, "status": status, "round": max(rounds),
              "owns": [], "depends_on": list(depends_on), "history": [],
              "verdicts": {str(r): "APPROVE" for r in rounds}}
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st), encoding="utf-8")

    def put_review(self, task_id, rnd, text):
        path = self.root / f"docs/reviews/{task_id}/r{rnd}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def followups(self, *args, check=True):
        proc = self.orch("followups", *args, check=check)
        return json.loads(proc.stdout) if check else proc

    def group(self):
        self.put_state("T-001", "developer", rounds=(1, 2))
        self.put_review("T-001", 1, OLD_R1)
        self.put_review("T-001", 2, REVIEW)
        self.put_state("T-002", "developer")
        self.put_review("T-002", 1, REVIEW.replace("r2-", "r1-"))
        self.put_state("T-003", "tester", depends_on=("T-001", "T-002"))
        self.put_review("T-003", 1, TESTER_REVIEW)

    def test_group_collects_latest_round_of_members(self):
        self.group()
        out = self.followups("T-003")
        self.assertEqual(out["tasks"], ["T-001", "T-002", "T-003"])
        self.assertEqual([(f["task"], f["round"], f["id"]) for f in out["followups"]],
                         [("T-001", 2, "r2-F2"), ("T-001", 2, "r2-F3"), ("T-002", 1, "r1-F2"), ("T-002", 1, "r1-F3")])
        self.assertIn("이름이 모호하다. 바꾼다. 둘째 줄 설명", out["followups"][0]["text"])
        self.assertEqual([(g["task"], g["id"]) for g in out["planning_gaps"]], [("T-001", "r2-F4"), ("T-002", "r1-F4")])
        self.assertEqual(out["regressions"], [{"task": "T-003", "round": 1, "id": None,
                                               "text": "tests/grp/a.test.ts > 합계 — T-002. 합계가 틀린다."}])
        self.assertNotIn("r1-F9", json.dumps(out, ensure_ascii=False))

    def test_non_tester_task_is_alone(self):
        self.group()
        self.assertEqual(self.followups("T-001")["tasks"], ["T-001"])

    def test_rest_only_done_ungrouped_developers(self):
        self.group()
        self.put_state("T-004", "developer")
        self.put_state("T-005", "developer", status="working")
        self.put_state("T-006", "designer")
        self.assertEqual(self.followups("--rest")["tasks"], ["T-004"])

    def test_followup_derived_task_skips_followups(self):
        self.put_state("T-004", "developer")
        self.put_review("T-004", 1, REVIEW.replace("r2-", "r1-"))
        (self.root / ".orchestration/tasks/T-004.md").write_text(DERIVED_SPEC.format(id="T-004"), encoding="utf-8")
        out = self.followups("--rest")
        self.assertEqual(out["tasks"], ["T-004"])
        self.assertEqual(out["followups"], [])
        self.assertEqual(len(out["planning_gaps"]), 1)

    def test_unreadable_spec_is_not_derived(self):
        self.put_state("T-004", "developer")
        self.put_review("T-004", 1, REVIEW.replace("r2-", "r1-"))
        (self.root / ".orchestration/tasks/T-004.md").write_text("front matter 없음\n", encoding="utf-8")
        out = self.followups("--rest")
        self.assertEqual([f["id"] for f in out["followups"]], ["r1-F2", "r1-F3"])

    def test_old_format_review_gives_nothing(self):
        self.put_state("T-001", "developer")
        self.put_review("T-001", 1, "# 리뷰\n\n없음\n\nVERDICT: APPROVE\n")
        out = self.followups("T-001")
        self.assertEqual((out["followups"], out["planning_gaps"], out["regressions"]), ([], [], []))

    def test_argument_rules(self):
        self.assertEqual(self.followups(check=False).returncode, 2)
        self.assertEqual(self.followups("T-001", "--rest", check=False).returncode, 2)
        self.assertEqual(self.followups("T-099", check=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
