"""내장 Codex 래퍼(scripts/codex-run.sh) 빠른 점검. 전 경로 회귀 테스트는 test-codex-run.sh(run.sh --wrapper)다."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import PLUGIN
from orch import paths

WRAPPER = PLUGIN / "scripts" / "codex-run.sh"
WRAPPER_TEST = PLUGIN / "tests" / "test-codex-run.sh"


class CodexRunnerTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ORCA_TEAM_CODEX_RUN", None)

    def test_default_is_embedded_wrapper(self):
        self.assertEqual(paths.codex_runner(), WRAPPER)

    def test_env_override(self):
        os.environ["ORCA_TEAM_CODEX_RUN"] = str(WRAPPER_TEST)
        self.assertEqual(paths.codex_runner(), WRAPPER_TEST)

    def test_missing_embedded_wrapper_is_broken_install(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(paths, "PLUGIN_ROOT", Path(tmp)):
            with self.assertRaises(paths.OrchError) as ctx:
                paths.codex_runner()
        self.assertEqual(ctx.exception.code, 12)
        self.assertIn("플러그인 설치가 깨졌다", str(ctx.exception))


class EmbeddedWrapperTest(unittest.TestCase):
    def test_wrapper_is_executable(self):
        self.assertTrue(WRAPPER.is_file(), WRAPPER)
        self.assertTrue(os.access(WRAPPER, os.X_OK), WRAPPER)

    def test_wrapper_parses(self):
        proc = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_wrapper_keeps_flags(self):
        text = WRAPPER.read_text(encoding="utf-8")
        for flag in ("--bypass-hook-trust", "--dangerously-bypass-hook-trust",
                     "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "--json",
                     "--output-last-message", "</dev/null"):
            self.assertIn(flag, text)

    def test_wrapper_comments_match_orca_team(self):
        """래퍼 주석이 orca-team 에 없는 것(doctor 스파이크, run 스킬)을 가리키지 않는다."""
        text = WRAPPER.read_text(encoding="utf-8")
        for stale in ("doctor 가 실물로 확인한다", "스킬(run·image)"):
            self.assertFalse(stale in text, stale)
        for key in ("image 스킬·orch review", "tests/run.sh --wrapper"):
            self.assertTrue(key in text, key)

    def test_wrapper_regression_test_is_present(self):
        self.assertTrue(WRAPPER_TEST.is_file(), WRAPPER_TEST)
        proc = subprocess.run(["bash", "-n", str(WRAPPER_TEST)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
