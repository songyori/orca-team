import json
import os
import unittest

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
        self.assertEqual(call[call.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(call[call.index("--effort") + 1], "xhigh")
        self.assertEqual(call[call.index("--cwd") + 1], str(self.root))
        self.assertEqual(call[call.index("--profile") + 1], "run")
        self.assertTrue(self.calls_for("orchestration worker-retain"))

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

    def test_worker_retain_failure_is_best_effort(self):
        self.commit_work()
        self.respond_raw("orchestration worker-retain", "not json at all")
        self.modes("approve")
        self.assertEqual(json.loads(self.review().stdout)["verdict"], "APPROVE")



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


if __name__ == "__main__":
    unittest.main()
