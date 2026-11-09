import json
import os
import unittest
from unittest import mock

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from helpers import fake_test_commands, read_test_args, set_test_modes, write_test_config
from orch import review, testgate
from orch.config import Config
from orch.paths import Project
from test_tasks import TaskCase


class ReviewCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec: 로그인")
        self.orch("start", "T-001")

    def commit_work(self, path="src/login.ts", message="[T-001] feat: 로그인"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// {path}\n", encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", message)

    def modes(self, *modes):
        (self.fake_dir / "codex_run_modes.txt").write_text("".join(m + "\n" for m in modes), encoding="utf-8")

    def runner_calls(self):
        log = self.fake_dir / "codex-run.log"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def review(self, check=True):
        return self.orch("review", "T-001", check=check)


class ReviewTest(ReviewCase):
    def test_approve_commits_review_and_sets_state(self):
        self.commit_work()
        self.modes("approve")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))
        self.assertEqual(self.state("T-001")["status"], "approved")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[T-001] review r1: APPROVE")
        self.assertIn("VERDICT: APPROVE", (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8"))
        call = self.runner_calls()[-1]
        self.assertIn("--bypass-hook-trust", call)
        self.assertEqual(call[call.index("--model") + 1], "gpt-6.1-sol")
        self.assertEqual(call[call.index("--effort") + 1], "xhigh")
        self.assertEqual(call[call.index("--cwd") + 1], str(self.root))
        self.assertEqual(call[call.index("--profile") + 1], "run")
        self.assertEqual(self.calls_for("orchestration worker-retain"), [])

    def test_request_changes(self):
        self.commit_work()
        self.modes("changes")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "REQUEST_CHANGES")
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_no_commits_is_auto_request_changes(self):
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        self.assertEqual(self.runner_calls(), [])

    def test_ownership_violation_is_auto_request_changes(self):
        self.commit_work("NOTES.md", "[T-001] docs: readme")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        self.assertIn("NOTES.md", (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8"))
        self.assertEqual(self.runner_calls(), [])

    def test_report_path_is_always_allowed(self):
        self.commit_work()
        self.commit_work("docs/tasks/T-001/report.md", "[T-001] docs: 리포트")
        self.modes("approve")
        self.assertEqual(json.loads(self.review().stdout)["source"], "codex")

    def test_missing_verdict_retries_once(self):
        self.commit_work()
        self.modes("noverdict", "approve")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")
        self.assertEqual(len(self.runner_calls()), 2)

    def test_two_failures_escalate_and_restore_working(self):
        self.commit_work()
        self.modes("fail", "noverdict")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("2회", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")
        self.assertEqual(len(self.runner_calls()), 2)
        self.assertNotIn("[T-001] review", self.git("log", "--format=%s"))

    def test_codex_file_change_invalidates_verdict(self):
        self.commit_work()
        self.modes("edit_tracked")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/login.ts", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")
        self.assertNotIn("[T-001] review", self.git("log", "--format=%s"))

    def test_other_active_task_changes_are_ignored(self):
        self.write_spec("T-002", role="tester")
        self.orch("start", "T-002")
        self.commit_work()
        self.modes("touch_other")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")

    def test_round_two_ignores_review_and_spec_commits(self):
        self.commit_work()
        self.modes("changes", "approve")
        self.review()
        self.orch("fix", "T-001")
        self.commit_work("src/b.ts", "[T-001] fix: 리뷰 반영")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["round"], out["verdict"], out["source"]), (2, "APPROVE", "codex"))
        prompt_files = sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))
        self.assertIn("docs/reviews/T-001/r1.md", prompt_files[-1].read_text(encoding="utf-8"))

    def test_runner_oserror_restores_working(self):
        self.commit_work()
        bad = self.tmp / "no-such-runner.sh"
        proc = self.orch("review", "T-001", check=False, env={"ORCA_TEAM_CODEX_RUN": str(bad)})
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")
        self.modes("approve")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")

    def test_missing_runner_is_broken_install_exit_12(self):
        self.commit_work()
        proc = self.orch("review", "T-001", check=False,
                         env={"ORCA_TEAM_CODEX_RUN": str(self.tmp / "no-such-runner.sh")})
        self.assertEqual(proc.returncode, 12, proc.stderr)
        self.assertIn("플러그인 설치가 깨졌다", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_non_executable_runner_is_exit_12(self):
        self.commit_work()
        runner = self.tmp / "runner.sh"
        runner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        runner.chmod(0o644)
        proc = self.orch("review", "T-001", check=False, env={"ORCA_TEAM_CODEX_RUN": str(runner)})
        self.assertEqual(proc.returncode, 12, proc.stderr)
        self.assertIn("Codex 래퍼", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_korean_path_not_escaped(self):
        self.commit_work()
        self.commit_work("src/기획/안내.md", "[T-001] docs: 안내")
        self.modes("approve")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["source"], "codex")
        prompt = sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")
        self.assertIn("src/기획/안내.md", prompt)
        self.assertNotIn("\\354", prompt)

    def test_lookalike_subject_is_not_skipped(self):
        self.commit_work("NOTES.md", "[T-001] specialize parser")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        self.assertIn("NOTES.md", (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8"))

    def test_codex_commit_invalidates_verdict(self):
        self.commit_work()
        self.modes("commit")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("HEAD", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_concurrent_other_task_commit_is_accepted(self):
        self.write_spec("T-002", role="tester")
        self.orch("start", "T-002")
        self.commit_work()
        self.modes("other_commit")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")

    def test_non_fast_forward_head_change_invalidates(self):
        self.commit_work()
        self.modes("rewrite")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("HEAD", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_merge_commit_with_owned_file_invalidates(self):
        self.commit_work()
        self.modes("evil_merge")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/evil.txt", proc.stderr)
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_spec_subject_with_product_file_is_checked(self):
        self.commit_work("NOTES.md", "[T-001] spec: x")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        self.assertIn("NOTES.md", (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8"))

    def test_review_requires_working_state(self):
        self.set_state("T-001", status="approved")
        self.assertEqual(self.review(check=False).returncode, 2)

    def test_without_test_config_prompt_keeps_run_it_yourself(self):
        self.commit_work()
        self.modes("approve")
        self.review()
        prompt = sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")
        self.assertIn("리포트의 검증 출력을 그대로 믿지 말고 가능하면 직접 실행한다.", prompt)
        self.assertNotIn("## orch 테스트 결과", prompt)
        self.assertNotIn("{verify_rule}", prompt)


class ReviewTamperTest(ReviewCase):
    """Codex 는 테스트·빌드를 돌린다. 추적 안 된 산출물은 경고만 하고, 추적 파일 내용 변경과 HEAD 이동만 판정을 무효로 한다."""

    def test_new_untracked_artifact_is_warning_not_invalidation(self):
        self.commit_work()
        self.modes("pycache")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))
        self.assertEqual(out["untracked"], ["__pycache__/x.pyc"])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[T-001] review r1: APPROVE")
        self.assertIn("__pycache__/x.pyc", self.git("log", "-1", "--format=%b"))
        self.assertEqual(self.state("T-001")["history"][-1]["untracked"], ["__pycache__/x.pyc"])

    def test_new_untracked_file_in_owns_is_warning(self):
        self.commit_work()
        self.modes("modify")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "APPROVE")
        self.assertEqual(out["untracked"], ["src/codex-touched.txt"])

    def test_clean_review_has_no_untracked_warning(self):
        self.commit_work()
        self.modes("approve")
        out = json.loads(self.review().stdout)
        self.assertNotIn("untracked", out)
        self.assertEqual(self.git("log", "-1", "--format=%b").strip(), "")

    def test_task_activated_mid_review_is_excluded(self):
        self.commit_work("tests/a.txt", "chore: 기존 테스트")
        self.commit_work()
        self.modes("start_other")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")

    def test_staged_rename_out_of_owns_invalidates(self):
        self.write_spec("T-002", role="tester")
        self.orch("start", "T-002")
        self.commit_work()
        self.modes("mv_staged")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/login.ts", proc.stderr)

    def test_committed_rename_out_of_owns_invalidates(self):
        self.write_spec("T-002", role="tester")
        self.orch("start", "T-002")
        self.commit_work()
        self.modes("rename_commit")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/login.ts", proc.stderr)

    def test_uncommitted_owned_change_blocks_review_before_codex(self):
        self.commit_work()
        (self.root / "src/login.ts").write_text("// 커밋 안 한 수정\n", encoding="utf-8")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("src/login.ts", proc.stderr)
        self.assertIn("커밋", proc.stderr)
        self.assertEqual(self.runner_calls(), [])
        self.assertEqual(self.state("T-001")["status"], "working")

    def test_uncommitted_change_outside_owns_does_not_block(self):
        self.commit_work("NOTES.md", "chore: readme")
        self.commit_work()
        (self.root / "NOTES.md").write_text("사용자가 고치는 중\n", encoding="utf-8")
        self.modes("approve")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")

    def test_snapshot_survives_unreadable_and_skips_excluded_files(self):
        from orch import review
        self.commit_work("tests/locked.txt", "chore: x")
        locked = self.root / "tests/locked.txt"
        locked.write_text("다른 워커가 쓰는 중\n", encoding="utf-8")
        locked.chmod(0)
        try:
            hashes, _ = review.snapshot(self.root, [])
            self.assertEqual(hashes["tests/locked.txt"], "?")
            hashes, _ = review.snapshot(self.root, ["tests/**"])
            self.assertNotIn("tests/locked.txt", hashes)
        finally:
            locked.chmod(0o644)


class ReviewReentryTest(ReviewCase):
    """코디네이터가 리뷰 도중 끊겨 reviewing 으로 남은 태스크를 JSON 을 고치지 않고 다시 리뷰한다."""

    def pid_file(self):
        return self.root / ".orchestration/state/tasks/T-001/review.pid"

    def test_stale_reviewing_can_be_reviewed_again(self):
        self.commit_work()
        self.set_state("T-001", status="reviewing")
        self.modes("approve")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")
        self.assertEqual(self.state("T-001")["status"], "approved")

    def test_live_review_process_refuses_second_review(self):
        self.commit_work()
        self.set_state("T-001", status="reviewing")
        self.pid_file().write_text(f"{os.getpid()}\n", encoding="utf-8")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(self.runner_calls(), [])
        self.assertEqual(self.state("T-001")["status"], "reviewing")

    def test_review_pid_exists_only_while_reviewing(self):
        self.commit_work()
        self.modes("approve")
        self.review()
        seen = json.loads((self.fake_dir / "review_pids_seen.json").read_text(encoding="utf-8"))
        self.assertEqual(seen, ["T-001/review.pid"])
        self.assertFalse(self.pid_file().exists())


class ReviewDesignSectionTest(ReviewCase):
    """디자인·UI 직무(director, designer, developer) 리뷰 프롬프트에만 디자인 기준과 orch 자동 검사 결과가 들어간다."""

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def commit_text(self, path, text, task_id="T-001"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def section(self, files, shas):
        p = Project(self.root)
        return review.design_section(p, Config.load(p.config_path), "developer", files, shas)

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def test_diff_lint_comes_before_content_lint(self):
        self.commit_text("src/close.tsx", '<button aria-label="닫기">x</button>\n')
        self.commit_text("src/close.tsx", "<span>🚀</span>\n<button>x</button>\n")
        section = self.section(["src/close.tsx"], [self.head()])
        emoji = section.index("- src/close.tsx:1: 이모지(🚀)")
        lost = section.index("- src/close.tsx: 지운 줄에서 aria-label 1개가 줄었다(대체가 있는지 확인)")
        self.assertLess(lost, emoji)

    def test_review_prompt_has_zoom_block(self):
        self.commit_text("src/index.html", '<meta name="viewport" content="width=device-width, user-scalable=no">\n')
        self.review()
        self.assertIn('- src/index.html:1: 확대 막기(user-scalable=no">)\n', self.prompt())

    def test_combined_findings_are_capped(self):
        with mock.patch("orch.review.design_findings", return_value=[f"a.css:{n}: x" for n in range(40)]), \
                mock.patch("orch.review.design_diff_lint", return_value=[f"b.css: y{n}" for n in range(20)]):
            section = self.section(["a.css"], [])
        self.assertIn("- b.css: y19\n", section)
        self.assertIn("- a.css:29: x\n", section)
        self.assertNotIn("a.css:30: x", section)
        self.assertIn("- … 외 10건\n", section)

    def test_failing_check_keeps_the_other(self):
        self.commit_text("src/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n")
        with mock.patch("orch.review.design_diff_lint", side_effect=RuntimeError("boom")):
            section = self.section(["src/card.css"], [self.head()])
        self.assertIn("- src/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)\n", section)
        self.assertIn("- 자동 검사 실패: RuntimeError: boom\n", section)

    def test_developer_prompt_has_design_rules_and_lint_warnings(self):
        self.commit_text("src/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n")
        self.review()
        prompt = self.prompt()
        self.assertIn("## 디자인·UI 기준", prompt)
        self.assertIn(".orchestration/design-rules.md", prompt)
        self.assertIn("- src/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)", prompt)
        self.assertNotIn("{design_section}", prompt)
        self.assertNotIn("{design_lint}", prompt)
        self.assertNotIn("\n\n\n", prompt)
        self.assertIn("- src/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)\n\n## REVIEW_FILE 형식\n", prompt)

    def test_clean_change_reports_none(self):
        self.commit_text("src/card.css", ".card { border-left: 1px solid #e5e7eb; }\n")
        self.review()
        prompt = self.prompt()
        self.assertIn("orch 자동 검사", prompt)
        self.assertIn("\n- 없음\n", prompt)

    def test_project_without_director_role_has_no_design_section(self):
        """0.3.0 프로젝트([roles.director] 없음)는 디자인 기준 없이 지금 흐름 그대로 리뷰한다."""
        self.remove_role("director")
        self.commit_text("src/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n")
        self.review()
        prompt = self.prompt()
        self.assertNotIn("디자인·UI 기준", prompt)
        self.assertNotIn("design-rules.md", prompt)
        self.assertNotIn("orch 자동 검사", prompt)
        self.assertNotIn("{design_section}", prompt)
        self.assertNotIn("\n\n\n", prompt)  # 디자인 절이 빠져도 절 사이 빈 줄은 하나다
        self.assertRegex(prompt, r"[^\n]\n\n## REVIEW_FILE 형식\n")

    def test_tester_prompt_has_no_design_section(self):
        self.write_spec("T-002", role="tester", title="로그인 테스트")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 로그인 테스트")
        self.orch("start", "T-002")
        self.commit_text("tests/login.test.ts", "// 🚀\n", task_id="T-002")
        self.orch("review", "T-002")
        prompt = self.prompt()
        self.assertIn("[tester]", prompt)
        self.assertNotIn("design-rules.md", prompt)
        self.assertNotIn("orch 자동 검사", prompt)
        self.assertNotIn("{design_section}", prompt)
        self.assertNotIn("\n\n\n", prompt)
        self.assertRegex(prompt, r"[^\n]\n\n## REVIEW_FILE 형식\n")


class ReviewTextSectionTest(ReviewCase):
    """서비스 문구·문서(한글이 든 텍스트 대상 파일)를 바꾼 태스크 리뷰에만 문구 기준과 orch 문구 자동 검사가 들어간다."""

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def commit_text(self, path, text, task_id="T-001"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def test_korean_ui_text_adds_text_section_with_lint(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인이 완료됬어요";\n')
        self.review()
        prompt = self.prompt()
        self.assertIn("## 문구 기준", prompt)
        self.assertIn(".orchestration/text-rules.md", prompt)
        self.assertIn('- src/login.tsx:1: 오탈자 "됬"(export const done = "로그인이 완료됬어요";)\n\n'
                      "## REVIEW_FILE 형식\n", prompt)
        for raw in ("{text_section}", "{text_lint}", "{design_section}"):
            self.assertNotIn(raw, prompt)
        self.assertNotIn("\n\n\n", prompt)
        self.assertLess(prompt.index("## 디자인·UI 기준"), prompt.index("## 문구 기준"))

    def test_clean_korean_text_reports_none(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인했어요";\n')
        self.review()
        section = self.prompt().split("## 문구 기준", 1)[1]
        self.assertIn("orch 문구 자동 검사", section)
        self.assertIn("\n- 없음\n", section)

    def test_no_korean_text_has_no_text_section(self):
        self.commit_text("src/login.ts", "export const done = 1;\n")
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 문구 기준", prompt)
        self.assertNotIn("{text_section}", prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_excluded_paths_have_no_text_section(self):
        self.write_spec("T-002", role="tester", title="로그인 테스트")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 로그인 테스트")
        self.orch("start", "T-002")
        self.commit_text("tests/login.test.ts", 'it("로그인이 완료됬어요", () => {});\n', task_id="T-002")
        self.orch("review", "T-002")
        self.assertNotIn("## 문구 기준", self.prompt())

    def test_project_without_text_rules_has_no_text_section(self):
        """0.4.x 프로젝트(.orchestration/text-rules.md 없음)는 문구 기준 없이 지금 흐름 그대로 리뷰한다."""
        self.git("rm", "-q", "--", ".orchestration/text-rules.md")
        self.git("commit", "-q", "-m", "chore: 0.4 프로젝트")
        self.commit_text("src/login.tsx", 'export const done = "로그인이 완료됬어요";\n')
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 문구 기준", prompt)
        self.assertNotIn("text-rules.md", prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_text_section_without_design_section(self):
        self.remove_role("director")
        self.commit_text("src/login.tsx", 'export const done = "로그인이 완료됬어요";\n')
        self.review()
        prompt = self.prompt()
        self.assertIn("## 문구 기준", prompt)
        self.assertNotIn("## 디자인·UI 기준", prompt)
        self.assertNotIn("\n\n\n", prompt)
        self.assertRegex(prompt, r"[^\n]\n\n## REVIEW_FILE 형식\n")

    def test_lint_failure_does_not_block_review(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인했어요";\n')
        with mock.patch("orch.review.text_lint", side_effect=RuntimeError("boom")):
            section = review.text_section(Project(self.root), ["src/login.tsx"], ["0" * 40])
        self.assertIn("## 문구 기준", section)
        self.assertIn("- 자동 검사 실패: RuntimeError: boom\n", section)

    def test_lint_failure_message_is_one_line(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인했어요";\n')
        with mock.patch("orch.review.text_lint", side_effect=RuntimeError("git failed:\nfatal: bad\n")):
            section = review.text_section(Project(self.root), ["src/login.tsx"], ["0" * 40])
        self.assertIn("- 자동 검사 실패: RuntimeError: git failed: fatal: bad", section)
        self.assertNotIn("\n\n\n", section)



class GateReviewCase(ReviewCase):
    """[test] 를 가짜 테스트 명령으로 둔 리뷰 프로젝트. 테스트 메서드가 없는 공통 부모다(상속한 테스트가 중복 실행되지 않게)."""

    def setUp(self):
        super().setUp()
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)
        self.git("add", "--", "docs/planning/tasks.toml")
        self.git("commit", "-q", "-m", "chore: test 설정")

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")


class ReviewGateTest(GateReviewCase):
    """[test] 가 있으면 orch 가 Codex 전에 quick 을 확인한다."""

    def test_quick_failure_skips_codex(self):
        self.commit_work()
        set_test_modes(self, "fail", "fail")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]), ("REQUEST_CHANGES", "orch", {"quick": "failed"}))
        self.assertEqual(self.runner_calls(), [])
        review = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        for text in ("quick: 실패", "FAIL tests/a.test.ts", "`.orchestration/bin/orch test T-001`",
                     "실패가 이 태스크 탓이 아니면(소유 경로 밖 코드, 위의 커밋 안 된 다른 파일) 코드를 고치지 말고",
                     "프리앰블의 ask 명령으로 묻는다"):
            self.assertIn(text, review)
        self.assertTrue(review.rstrip().endswith("VERDICT: REQUEST_CHANGES"))
        self.assertEqual(read_test_args(self), [["src/login.ts"], ["src/login.ts"]])
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_quick_pass_goes_to_codex_with_results(self):
        self.commit_work()
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]), ("APPROVE", "codex", {"quick": "passed"}))
        prompt = self.prompt()
        for text in ("## orch 테스트 결과", "- quick: 통과", "전체 테스트를 돌리지 않고"):
            self.assertIn(text, prompt)
        for text in ("가능하면 직접 실행", "{test_section}", "{group_section}", "## 묶음 리뷰"):
            self.assertNotIn(text, prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_flaky_is_noted(self):
        self.commit_work()
        set_test_modes(self, "fail", "pass")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["source"], "codex")
        self.assertIn("불안정 의심", self.prompt())
        self.assertIn("quick 첫 실행 로그:", self.prompt())

    def test_passing_record_for_same_code_is_reused(self):
        self.commit_work()
        self.orch("test", "T-001")
        self.commit_work("docs/tasks/T-001/report.md", "[T-001] docs: 리포트")
        self.review()
        self.assertEqual(len(read_test_args(self)), 1)

    def test_changed_code_reruns_quick(self):
        self.commit_work()
        self.orch("test", "T-001")
        self.commit_work("src/b.ts", "[T-001] feat: 더하기")
        self.review()
        self.assertEqual(read_test_args(self), [["src/login.ts"], ["src/b.ts", "src/login.ts"]])

    def test_unrunnable_stops_review_without_round(self):
        self.commit_work()
        set_test_modes(self, "missing")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test] 명령을 실행할 수 없다", proc.stderr)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 1))
        self.assertNotIn("[T-001] review", self.git("log", "--format=%s"))
        self.assertEqual(self.runner_calls(), [])

    def test_untracked_owned_file_stops_review_without_round(self):
        self.commit_work()
        (self.root / "src/extra.ts").write_text("// 커밋 안 함\n", encoding="utf-8")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("소유 경로에 커밋하지 않은 변경이 있다: src/extra.ts", proc.stderr)
        self.assertIn("orch fix T-001", proc.stderr)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 1))
        self.assertEqual((read_test_args(self), self.runner_calls()), ([], []))

    def test_non_gated_role_has_no_gate(self):
        self.assertIsNone(testgate.gate_results(Project(self.root), "T-001", "designer", 1, None))


class ReviewGroupTest(GateReviewCase):
    """T-001(developer, done)과 그것에 의존하는 묶음 테스트 T-002(tester)."""

    def setUp(self):
        super().setUp()
        self.commit_work()
        self.set_state("T-001", status="done")
        self.write_spec("T-002", role="tester", title="묶음 테스트",
                        extra="owns: tests/grp/**, docs/qa/grp.md\ndepends_on: T-001\n")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 묶음 테스트")
        self.orch("start", "T-002")
        self.commit_work("tests/grp/a.test.ts", "[T-002] test: 묶음 통합 테스트")

    def review_group(self, check=True):
        return self.orch("review", "T-002", check=check)

    def test_tester_review_runs_full_only_and_adds_group_section(self):
        set_test_modes(self, "fail")
        out = json.loads(self.review_group().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))
        self.assertEqual(out["tests"], {"full": "failed"})
        self.assertEqual(read_test_args(self), [["--all"]])
        prompt = self.prompt()
        for text in ("## 묶음 리뷰", "T-001 [developer] 로그인 — 작업 커밋 1개, 바꾼 파일: src/login.ts",
                     "- full: 실패", "출력 끝:", "FAIL tests/a.test.ts", "제품 회귀", "전체 테스트를 돌리지 않고",
                     "다른 지적처럼 번호를 이어 붙이고"):
            self.assertIn(text, prompt)
        for text in ("## orch 테스트 결과", "- quick:"):
            self.assertNotIn(text, prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_full_record_of_same_round_is_reused(self):
        self.orch("test", "T-002", "--full")
        out = json.loads(self.review_group().stdout)
        self.assertEqual(out["tests"], {"full": "passed"})
        self.assertEqual(read_test_args(self), [["--all"]])
        self.assertIn("- full: 통과", self.prompt())
        self.assertNotIn("출력 끝:", self.prompt())

    def test_full_record_for_older_head_is_rerun(self):
        self.orch("test", "T-002", "--full")
        self.commit_work("tests/grp/b.test.ts", "[T-002] test: 더 쓴 통합 테스트")
        out = json.loads(self.review_group().stdout)
        self.assertEqual(out["tests"], {"full": "passed"})
        self.assertEqual(read_test_args(self), [["--all"], ["--all"]])

    def test_qa_report_commit_does_not_block_full_reuse(self):
        self.orch("test", "T-002", "--full")
        self.commit_work("docs/qa/grp.md", "[T-002] docs: QA 리포트")
        out = json.loads(self.review_group().stdout)
        self.assertEqual(out["tests"], {"full": "passed"})
        self.assertEqual(read_test_args(self), [["--all"]])

    def test_full_failure_never_auto_rejects(self):
        set_test_modes(self, "fail")
        self.modes("changes")
        out = json.loads(self.review_group().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]),
                         ("REQUEST_CHANGES", "codex", {"full": "failed"}))
        self.assertEqual(len(self.runner_calls()), 1)
        review = (self.root / "docs/reviews/T-002/r1.md").read_text(encoding="utf-8")
        self.assertNotIn("orch 사전 검사", review)
        self.assertEqual(read_test_args(self), [["--all"]])


def review_body(findings: str, verdict: str, rnd: int = 1, prior: str = "- 해당 없음") -> str:
    return (f"# T-001 r{rnd} 리뷰\n## 판정 근거\n근거\n## 이전 지적 확인\n{prior}\n## 지적 사항\n{findings}\n"
            "## 후속\n- 없음\n## 기획 누락\n- 없음\n## 회귀\n- 해당 없음\n## 직접 확인한 것\n- 읽음\n\n"
            f"VERDICT: {verdict}\n")


class ReviewRoundTest(ReviewCase):
    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def round_part(self, prompt):
        start = prompt.index("## 라운드 2 확인 범위")
        end = prompt.index("\n## ", start + 1)
        return prompt[start:end]

    def body(self, text):
        (self.fake_dir / "review_body.md").write_text(text, encoding="utf-8")

    def test_round_one_has_no_round_section_and_new_format(self):
        self.commit_work()
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 라운드", prompt)
        self.assertNotIn("{round_section}", prompt)
        for text in ("## 등급과 판정", "P1(막는다)", "## 후속", "## 기획 누락", "## 회귀", "## 이전 지적 확인",
                     "r1-F1 [P1]", "- r1-F4 실패한 테스트 — 원인으로 보이는 태스크 ID. 근거.",
                     "orch 는 `## 지적 사항` 의 `[P1]` 이 든 줄과 `## 이전 지적 확인` 의 `미해결` 줄 수로 판정을 맞춘다"):
            self.assertIn(text, prompt)
        self.assertNotIn("P1 이 하나라도 있으면", prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_round_two_lists_only_new_commits_and_records_head(self):
        self.commit_work()
        first = self.git("rev-parse", "HEAD").strip()
        self.modes("changes", "approve")
        self.review()
        self.assertEqual(self.state("T-001")["history"][-1]["head"], first)
        self.orch("fix", "T-001")
        self.commit_work("src/b.ts", "[T-001] fix: 리뷰 반영")
        self.review()
        part = self.round_part(self.prompt())
        self.assertIn("[T-001] fix: 리뷰 반영", part)
        self.assertNotIn("[T-001] feat: 로그인", part)
        self.assertIn(f"`{first[:10]}`", part)
        self.assertIn("`r1-F1: 해결`", part)
        self.assertIn("docs/reviews/T-001/r1.md", part)

    def test_orch_only_prior_round_gets_full_review(self):
        self.review()  # 작업 커밋 없음 → orch 사전 검사
        self.orch("fix", "T-001")
        self.commit_work()
        self.review()
        self.assertNotIn("## 라운드", self.prompt())

    def test_missing_head_in_old_history_lists_all(self):
        self.commit_work()
        self.modes("changes", "approve")
        self.review()
        history = self.state("T-001")["history"]
        for entry in history:
            entry.pop("head", None)
        self.set_state("T-001", history=history)
        self.orch("fix", "T-001")
        self.commit_work("src/b.ts", "[T-001] fix: 리뷰 반영")
        self.review()
        self.assertIn("커밋 기록이 없다", self.round_part(self.prompt()))

    def test_request_changes_without_p1_becomes_approve(self):
        self.commit_work()
        self.body(review_body("- 없음", "REQUEST_CHANGES"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["verdict_adjusted"]), ("APPROVE", "REQUEST_CHANGES→APPROVE"))
        self.assertEqual(self.state("T-001")["status"], "approved")
        self.assertEqual(self.state("T-001")["history"][-1]["verdict_adjusted"], "REQUEST_CHANGES→APPROVE")
        text = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith("VERDICT: APPROVE"))
        self.assertIn("orch 판정 맞추기", text)
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[T-001] review r1: APPROVE")
        self.assertIn("판정 맞추기: REQUEST_CHANGES→APPROVE", self.git("log", "-1", "--format=%B"))

    def test_approve_with_p1_becomes_request_changes(self):
        self.commit_work()
        self.body(review_body("- r1-F1 [P1] src/login.ts:1 — 입력 검증이 없다. 검증한다.", "APPROVE"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["verdict_adjusted"]), ("REQUEST_CHANGES", "APPROVE→REQUEST_CHANGES"))
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_unresolved_prior_finding_keeps_request_changes(self):
        self.commit_work()
        self.body(review_body("- 없음", "REQUEST_CHANGES", rnd=2, prior="- r1-F1: 미해결 — 입력 검증이 아직 없다"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")
        self.assertNotIn("verdict_adjusted", out)

    def test_star_bullet_p1_keeps_request_changes(self):
        self.commit_work()
        self.body(review_body("* r1-F1 [P1] src/login.ts:1 — 입력 검증이 없다. 검증한다.", "REQUEST_CHANGES"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")
        self.assertNotIn("verdict_adjusted", out)

    def assert_approve_stays(self, findings, prior):
        self.commit_work()
        self.body(review_body(findings, "APPROVE", rnd=2, prior=prior))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "APPROVE")
        self.assertNotIn("verdict_adjusted", out)

    def test_approve_stays_with_none_finding_mentioning_p1(self):
        self.assert_approve_stays("- 없음 (r1-F1 [P1] 은 고쳐졌다)", "- r1-F1: 해결")

    def test_approve_stays_with_not_applicable_finding_mentioning_p1(self):
        self.assert_approve_stays("1. **해당 없음** (r1-F1 [P1] 은 고쳐졌다)", "- r1-F1: 해결")

    def test_approve_stays_with_no_unresolved_phrase(self):
        self.assert_approve_stays("- 없음", "- r1-F1: 해결\n- 미해결 지적 없음")

    def test_approve_stays_with_unresolved_word_inside_resolved(self):
        self.assert_approve_stays("- 없음", "- r1-F1: 해결 (r1 에서 미해결이던 입력 검증을 고쳤다)")

    def test_approve_stays_with_unresolved_p2(self):
        self.assert_approve_stays("- 없음", "- r1-F2 [P2]: 미해결 — P2 라 막지 않는다")

    def test_approve_stays_when_findings_mention_old_p1(self):
        self.assert_approve_stays("- 없음. r1 의 [P1] 은 모두 해결됐다", "- r1-F1: 해결")

    def test_strict_count_only_matches_id_shaped_lines(self):
        strict = review.strict_p1_count
        for line in ("- r1-F1 [P1] a", "* r1-F1 [P1] a", "1. r1-F1 [P1] a", "### r1-F1 [P1] a", "  - r1-F1 [P1] a"):
            self.assertEqual(strict(review_body(line, "APPROVE")), 1, line)
        self.assertEqual(strict(review_body("- 없음. r1 의 [P1] 은 해결됐다", "APPROVE")), 0)
        self.assertEqual(strict(review_body("- r2-F1 [P2] r1-F1 [P1] 와 같은 문제", "APPROVE")), 0)
        for prior in ("- r1-F1: 미해결 — 근거", "* r1-F1：미해결", "1. r1-F1: 미해결"):
            self.assertEqual(strict(review_body("- 없음", "APPROVE", prior=prior)), 1, prior)
        for prior in ("- r1-F1: 해결\n- 미해결 지적 없음", "- r1-F1: 해결 (미해결이던 것을 고쳤다)"):
            self.assertEqual(strict(review_body("- 없음", "APPROVE", prior=prior)), 0, prior)
        self.assertIsNone(strict("# 리뷰\n## 이전 지적 확인\n- r1-F1: 미해결\n\nVERDICT: APPROVE\n"))

    def test_strict_count_accepts_format_variants(self):
        strict = review.strict_p1_count
        for line in ("- **r2-F1** [P1] a", "- r2-F1 **[P1]** a", "- `r2-F1` [P1] a", "- [P1] r2-F1 a", "+ r2-F1 [P1] a",
                     "1) r2-F1 [P1] a", "- r2-F1 [p1] a"):
            self.assertEqual(strict(review_body(line, "APPROVE")), 1, line)
        self.assertEqual(strict(review_body("- r2-F3 [P2] a r1-F1 [P1] 관련", "APPROVE")), 0)
        for prior in ("- r1-F1 [P1]: 미해결 — a", "- **r1-F1**: 미해결", "- r1-F1: **미해결**"):
            self.assertEqual(strict(review_body("- 없음", "APPROVE", prior=prior)), 1, prior)
        self.assertEqual(strict(review_body("- 없음", "APPROVE", prior="- r1-F2 [P2]: 미해결")), 0)

    def assert_approve_raised(self, findings, prior):
        self.commit_work()
        self.body(review_body(findings, "APPROVE", rnd=2, prior=prior))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")

    def test_bold_id_p1_raises_approve(self):
        self.assert_approve_raised("- **r2-F1** [P1] a", "- r1-F1: 해결")

    def test_tag_before_id_p1_raises_approve(self):
        self.assert_approve_raised("- [P1] r2-F1 a", "- r1-F1: 해결")

    def test_prior_p1_tag_unresolved_raises_approve(self):
        self.assert_approve_raised("- 없음", "- r1-F1 [P1]: 미해결")

    def test_broad_count_sees_bare_p1_mentions(self):
        for line in ("- r1-F1 **P1** a", "- r1-F1 (P1) a", "- r1-F1 [p1] a", "### P1\n- r1-F1 a"):
            self.assertEqual(review.p1_count(review_body(line, "REQUEST_CHANGES")), 1, line)
        self.assertEqual(review.p1_count(review_body("- r1-F1 [P12] a, API1 b, P1x", "REQUEST_CHANGES")), 0)

    def test_p1_count_ignores_line_shape_and_counts_unresolved(self):
        for line in ("- r1-F1 [P1] a", "* r1-F1 [P1] a", "1. r1-F1 [P1] a", "### r1-F1 [P1] a", "  - r1-F1 [P1] a"):
            with self.subTest(line=line):
                self.assertEqual(review.p1_count(review_body(line, "APPROVE")), 1)
        self.assertEqual(review.p1_count(review_body("- r1-F2 [P2] a\n* r1-F3 [P3] b", "REQUEST_CHANGES")), 0)
        self.assertEqual(review.p1_count(review_body("- 없음", "APPROVE", prior="- r1-F1: 해결\n- r1-F2: 미해결 — 근거")), 1)
        self.assertEqual(review.p1_count(review_body("- r2-F1 [P1] a", "APPROVE", prior="- r1-F1: 미해결 — 근거")), 2)
        self.assertIsNone(review.p1_count("# 리뷰\n## 이전 지적 확인\n- r1-F1: 미해결\n\nVERDICT: APPROVE\n"))

    def test_old_format_is_not_adjusted(self):
        self.commit_work()
        self.body("# 리뷰\n지적 없음\n\nVERDICT: REQUEST_CHANGES\n")
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")
        self.assertNotIn("verdict_adjusted", out)

    def test_fix_spec_tells_worker_to_fix_only_blocking(self):
        self.commit_work()
        self.modes("changes")
        self.review()
        self.orch("fix", "T-001")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("막는 지적(`## 지적 사항` 의 P1, `## 이전 지적 확인` 의 미해결, orch 사전 검사 사유)을 모두 고친다", spec)
        self.assertIn("`## 후속`·`## 기획 누락`·`## 회귀` 는 고치지 않는다", spec)


if __name__ == "__main__":
    unittest.main()
