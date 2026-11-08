import fcntl
import json
import os
import shlex
import signal
import time
import unittest
from unittest import mock

from helpers import fake_test_commands, read_test_args, set_test_modes, write_test_config
from test_tasks import TaskCase
from orch import plan, testgate
from orch.paths import OrchError, Project


class ConfigTest(unittest.TestCase):
    def test_plan_file_matches_plan_module(self):
        self.assertEqual(testgate.PLAN_FILE, plan.PLAN_FILE)

    def test_defaults(self):
        cfg = testgate.parse_config({"quick": "t {files}", "full": "t"})
        self.assertEqual((cfg.quick, cfg.full, cfg.timeout_min, cfg.ok_exit, cfg.exclusive),
                         ("t {files}", "t", 30, (0,), False))

    def test_problems(self):
        base = {"quick": "t {files}", "full": "t"}
        cases = [
            ("모르는 키 extra", {**base, "extra": 1}),
            ("quick 은 {files} 를 정확히 한 번", {**base, "quick": "t"}),
            ("quick 은 {files} 를 정확히 한 번", {**base, "quick": "t {files} {files}"}),
            ("full 은 {files} 를 담지 않는다", {**base, "full": "t {files}"}),
            ("quick 는 비지 않은 명령 문자열", {**base, "quick": " "}),
            ("full 는 비지 않은 명령 문자열", {"quick": "t {files}"}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": 0}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": 241}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": True}),
            ("ok_exit 는 정수 목록", {**base, "ok_exit": []}),
            ("ok_exit 는 정수 목록", {**base, "ok_exit": ["0"]}),
            ("exclusive 는 true 또는 false", {**base, "exclusive": "yes"}),
        ]
        for needle, raw in cases:
            with self.subTest(needle=needle, raw=raw):
                self.assertTrue(any(needle in item for item in testgate.config_problems(raw)),
                                testgate.config_problems(raw))
        self.assertEqual(testgate.config_problems("x"), ["[test] 는 표여야 한다"])
        self.assertEqual(testgate.config_problems({**base, "timeout_min": 240, "ok_exit": [0, 5], "exclusive": True}), [])
        with self.assertRaises(OrchError) as ctx:
            testgate.parse_config({**base, "quick": "t"})
        self.assertEqual(ctx.exception.code, 2)


class NoConfigTest(TaskCase):
    def test_without_test_table_exit_2(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        proc = self.orch("test", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("커밋된 HEAD 의 docs/planning/tasks.toml 에 [test] 가 없다", proc.stderr)
        self.assertIn("작업 트리에만 있으면 커밋한다", proc.stderr)

    def test_needs_id_or_full(self):
        proc = self.orch("test", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("태스크 ID 또는 --full", proc.stderr)

    def test_uncommitted_test_table_does_not_enable_gate(self):
        quick, full = fake_test_commands(self)
        write_test_config(self.root, quick, full)
        self.assertIsNone(testgate.load_config(Project(self.root)))
        self.write_spec("T-001")
        self.orch("start", "T-001")
        proc = self.orch("test", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("커밋된 HEAD 의 docs/planning/tasks.toml 에 [test] 가 없다", proc.stderr)
        self.assertEqual(read_test_args(self), [])

    def test_repo_without_commits_has_no_gate(self):
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        self.git("-C", str(fresh), "init", "-q")
        write_test_config(fresh, "t {files}", "t")
        self.assertIsNone(testgate.load_config(Project(fresh)))

    def test_committed_bad_toml_is_exit_2(self):
        path = self.root / "docs/planning/tasks.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[test\n", encoding="utf-8")
        self.git("add", "--", "docs/planning/tasks.toml")
        self.git("commit", "-q", "-m", "chore: 깨진 tasks.toml")
        with self.assertRaises(OrchError) as ctx:
            testgate.load_config(Project(self.root))
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("읽기 실패", str(ctx.exception))


class GateCase(TaskCase):
    """T-001(developer)을 시작하고 [test] 를 가짜 테스트 명령으로 둔 프로젝트."""

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: test 설정")
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec: 로그인")
        self.orch("start", "T-001")

    def commit(self, path, text="x\n", message=None):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", message or f"[T-001] feat: {path}")

    def run_test(self, *args):
        return self.orch("test", *args, check=False)

    def record(self, name="T-001"):
        return json.loads((self.root / f".orchestration/state/tests/{name}.json").read_text(encoding="utf-8"))


class RunTaskTest(GateCase):
    def test_quick_passes_existing_changed_files_quoted(self):
        self.commit("src/a b.ts")
        self.commit("src/c.ts")
        self.commit("src/gone.ts")
        self.git("rm", "-q", "--", "src/gone.ts")
        self.git("commit", "-q", "-m", "[T-001] chore: 지움")
        code_sha = self.git("rev-parse", "HEAD").strip()
        self.commit("docs/tasks/T-001/report.md", "# 리포트\n", "[T-001] docs: 리포트")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["task"], out["kind"], out["status"]), ("T-001", "quick", "passed"))
        self.assertEqual(read_test_args(self), [["src/a b.ts", "src/c.ts"]])
        rec = self.record()["quick"]
        self.assertEqual((rec["round"], rec["head"], rec["status"], rec["exit"]), (1, code_sha, "passed", 0))
        self.assertEqual(rec["files"], ["src/a b.ts", "src/c.ts"])
        self.assertTrue(os.path.isfile(rec["log"]))
        self.assertIn(str(self.tmp / "cache/orca-team/runs"), rec["log"])

    def test_no_files_is_skipped(self):
        self.commit("docs/tasks/T-001/report.md", "# 리포트\n", "[T-001] docs: 리포트")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual((out["status"], out["head"], out["files"]), ("skipped", None, []))
        self.assertEqual(read_test_args(self), [])

    def test_fail_twice_is_failed_exit_1(self):
        self.commit("src/a.ts")
        set_test_modes(self, "fail", "fail")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertEqual((out["status"], out["exit"]), ("failed", 1))
        self.assertEqual(len(read_test_args(self)), 2)
        self.assertIn("FAIL tests/a.test.ts", out["tail"])
        self.assertNotIn("\n\n", out["tail"])
        self.assertIn("-quick-a2-", out["log"])

    def test_fail_then_pass_is_flaky(self):
        self.commit("src/a.ts")
        set_test_modes(self, "fail", "pass")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "flaky")
        self.assertEqual(out["first"]["exit"], 1)
        self.assertTrue(os.path.isfile(out["first"]["log"]))

    def test_ok_exit(self):
        write_test_config(self.root, self.quick, self.full, "ok_exit = [0, 5]\n")
        self.git("add", "--", "docs/planning/tasks.toml")
        self.git("commit", "-q", "-m", "chore: ok_exit")
        self.commit("src/a.ts")
        set_test_modes(self, "five")
        out = json.loads(self.run_test("T-001").stdout)
        self.assertEqual((out["status"], out["exit"]), ("passed", 5))

    def test_unrunnable_exit_2_without_record(self):
        self.commit("src/a.ts")
        set_test_modes(self, "missing")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test] 명령을 실행할 수 없다(exit 127", proc.stderr)
        self.assertFalse((self.root / ".orchestration/state/tests/T-001.json").exists())

    def test_dirty_lists_uncommitted_files_outside_task(self):
        self.commit("src/a.ts")
        with (self.root / ".orchestration/config.toml").open("a", encoding="utf-8") as fh:
            fh.write("# 작업 중\n")
        out = json.loads(self.run_test("T-001").stdout)
        self.assertEqual(out["dirty"], [".orchestration/config.toml"])

    def test_quick_before_first_task_commit_is_exit_2(self):
        (self.root / "src").mkdir()
        (self.root / "src/a.ts").write_text("x\n", encoding="utf-8")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("아직 `[T-001]` 작업 커밋이 없다", proc.stderr)
        self.assertIn("quick 은 커밋된 변경만 본다", proc.stderr)
        self.assertEqual(read_test_args(self), [])
        self.assertFalse((self.root / ".orchestration/state/tests/T-001.json").exists())

    def test_uncommitted_owned_files_are_exit_2(self):
        self.commit("src/a.ts")
        (self.root / "src/a.ts").write_text("y\n", encoding="utf-8")
        (self.root / "src/new.ts").write_text("새 파일\n", encoding="utf-8")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("소유 경로에 커밋하지 않은 변경이 있다: src/a.ts, src/new.ts", proc.stderr)
        self.assertEqual(read_test_args(self), [])

    def test_uncommitted_report_does_not_block_quick(self):
        self.commit("src/a.ts")
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# 리포트 초안\n", encoding="utf-8")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(read_test_args(self), [["src/a.ts"]])

    def test_qa_report_commit_and_draft_do_not_block_quick(self):
        self.commit("src/a.ts")
        code_sha = self.git("rev-parse", "HEAD").strip()
        self.commit("docs/qa/x.md", "# QA\n", "[T-001] docs: QA")
        draft = self.root / "docs/qa/y.md"
        draft.write_text("# 초안\n", encoding="utf-8")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(read_test_args(self), [["src/a.ts"]])
        self.assertEqual(self.record()["quick"]["head"], code_sha)

    def test_full_ignores_uncommitted_owned_files(self):
        self.commit("src/a.ts")
        (self.root / "src/a.ts").write_text("y\n", encoding="utf-8")
        proc = self.run_test("T-001", "--full")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_full_for_task_and_final(self):
        self.commit("src/a.ts")
        out = json.loads(self.run_test("T-001", "--full").stdout)
        self.assertEqual((out["kind"], out["status"]), ("full", "passed"))
        self.assertEqual(read_test_args(self), [["--all"]])
        self.assertEqual(self.record()["full"]["round"], 1)
        out = json.loads(self.run_test("--full").stdout)
        self.assertEqual((out["task"], out["kind"]), (None, "full"))
        self.assertEqual(self.record("final")["full"]["head"], self.git("rev-parse", "HEAD").strip())

    def test_bad_task_id_exit_2(self):
        proc = self.run_test("../x")
        self.assertEqual(proc.returncode, 2)

    def test_worktree_edit_of_committed_table_is_ignored(self):
        self.commit("src/a.ts")
        (self.root / "docs/planning/tasks.toml").write_text("[test\n", encoding="utf-8")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "passed")
        self.assertEqual(read_test_args(self), [["src/a.ts"]])
        write_test_config(self.root, "true {files}", "true")
        self.assertEqual(testgate.load_config(Project(self.root)).quick, self.quick)


class ExecuteTest(GateCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)

    def test_timeout_kills_process_group(self):
        started = time.monotonic()
        out = testgate.execute(self.root, "sleep 30 & sleep 30", self.tmp / "t.log", 0.5, (0,))
        self.assertEqual((out["status"], out["exit"]), ("timeout", None))
        self.assertLess(time.monotonic() - started, 10)

    def test_timeout_kills_members_that_ignore_sigterm(self):
        pid_file = self.tmp / "child.pid"
        command = f"(trap '' TERM; exec sleep 30) & echo $! > {shlex.quote(str(pid_file))}; wait"

        def alive(pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        with mock.patch.object(testgate, "KILL_GRACE_SEC", 0.3, create=True):
            out = testgate.execute(self.root, command, self.tmp / "t.log", 1.0, (0,))
        pid = int(pid_file.read_text(encoding="utf-8"))
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        self.assertEqual(out["status"], "timeout")
        deadline = time.monotonic() + 3
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid), "SIGTERM 을 무시한 그룹 프로세스가 남았다")

    def test_exclusive_lock_waits_then_fails(self):
        cfg = testgate.GateConfig("t {files}", "true", exclusive=True)
        lock = self.p.state / "tests" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            with self.assertRaises(OrchError) as ctx:
                testgate.attempt(self.p, cfg, "true", "lock-test", lock_wait=0.3)
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("exclusive", str(ctx.exception))
            free = testgate.attempt(self.p, testgate.GateConfig("t {files}", "true"), "true", "lock-test", lock_wait=0.3)
            self.assertEqual(free["status"], "passed")
        self.assertEqual(testgate.attempt(self.p, cfg, "true", "lock-test", lock_wait=0.3)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
