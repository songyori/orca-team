import os
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from helpers import ProjectCase
from orch import util
from orch.paths import OrchError

READ_ONLY = {"status", "diff", "log", "rev-parse", "diff-tree", "merge-base"}
LOCKED = subprocess.CompletedProcess([], 128, "", "fatal: Unable to create '/x/.git/index.lock': File exists.")


class QueuedCommandsTest(unittest.TestCase):
    def test_cli_set_and_message_text_come_from_one_constant(self):
        from orch import cli
        self.assertEqual(cli.QUEUED, set(util.QUEUED_COMMAND_NAMES))
        for name in util.QUEUED_COMMAND_NAMES:
            self.assertIn("agent reset" if name == "agent" else name, util.QUEUED_COMMANDS)


class GitCommitPathsTest(ProjectCase):
    def setUp(self):
        super().setUp()
        (self.root / "a.txt").write_text("a\n", encoding="utf-8")

    def commit(self, message="msg"):
        with mock.patch.dict(os.environ, self.env):
            util.git_commit_paths(self.root, ["a.txt"], message)

    def test_waits_for_index_lock_released_by_another_process(self):
        lock = self.root / ".git/index.lock"
        lock.write_text("", encoding="utf-8")
        timer = threading.Timer(0.3, lock.unlink)
        timer.start()
        try:
            self.commit("잠금 뒤 커밋")
        finally:
            timer.cancel()
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "잠금 뒤 커밋")

    def test_retry_budget_is_about_30_seconds_with_jitter(self):
        clock, sleeps = [0.0], []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        with mock.patch.object(util.time, "sleep", fake_sleep), \
                mock.patch.object(util.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(util.subprocess, "run", return_value=LOCKED):
            with self.assertRaises(OrchError) as ctx:
                util.git_commit_paths(self.root, ["a.txt"], "m")
        self.assertIn("index.lock", str(ctx.exception))
        self.assertGreaterEqual(sum(sleeps), 25)
        self.assertLessEqual(sum(sleeps), 32)
        self.assertGreater(len({round(s, 6) for s in sleeps}), 3)

    def test_other_errors_fail_without_retry(self):
        with mock.patch.object(util.time, "sleep") as sleep:
            with self.assertRaises(OrchError):
                util.git_commit_paths(self.root, ["no-such-file.txt"], "m")
        sleep.assert_not_called()

    def test_orch_commits_skip_git_hooks(self):
        for name in ("pre-commit", "commit-msg"):
            hook = self.root / ".git/hooks" / name
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        self.commit("훅 무시")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "훅 무시")


class GitOptionalLocksTest(ProjectCase):
    """orch 의 읽기 전용 git 호출은 GIT_OPTIONAL_LOCKS=0 이다(다른 워커의 index.lock 과 다투지 않는다)."""

    def spy_env(self):
        bin_dir = self.tmp / "spybin"
        bin_dir.mkdir()
        spy = bin_dir / "git"
        spy.write_text('#!/bin/sh\nprintf \'%s\\t%s\\n\' "${GIT_OPTIONAL_LOCKS:-}" "$*" >> "$GIT_SPY_LOG"\n'
                       f'exec {shutil.which("git")} "$@"\n', encoding="utf-8")
        spy.chmod(0o755)
        self.log = self.tmp / "git-spy.log"
        return {"PATH": f"{bin_dir}{os.pathsep}{self.env['PATH']}", "GIT_SPY_LOG": str(self.log)}

    def test_read_only_calls_during_start_and_review(self):
        from test_tasks import SPEC, STARTED
        self.scaffold()
        self.respond("orchestration worker-start", STARTED)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        for task_id, role in (("T-001", "developer"), ("T-002", "tester")):
            (self.root / f".orchestration/tasks/{task_id}.md").write_text(
                SPEC.format(id=task_id, role=role, title="x", extra=""), encoding="utf-8")
        env = self.spy_env()
        self.orch("start", "T-001", env=env)
        self.orch("start", "T-002", env=env)
        (self.root / "src").mkdir()
        (self.root / "src/login.ts").write_text("x\n", encoding="utf-8")
        self.git("add", "--", "src/login.ts")
        self.git("commit", "-q", "-m", "[T-001] feat: x")
        (self.fake_dir / "codex_run_modes.txt").write_text("other_commit\n", encoding="utf-8")
        self.orch("review", "T-001", env=env)
        calls = [line.split("\t", 1) for line in self.log.read_text(encoding="utf-8").splitlines()]
        seen = set()
        for flag, argv in calls:
            words = argv.split()
            sub = words[2] if words[:1] == ["-C"] else words[0]
            if sub in READ_ONLY and words[:1] == ["-C"] and words[1] == str(self.root):
                seen.add(sub)
                self.assertEqual(flag, "0", argv)
        self.assertTrue({"status", "log", "rev-parse", "diff-tree", "merge-base", "diff"} <= seen, seen)


if __name__ == "__main__":
    unittest.main()
