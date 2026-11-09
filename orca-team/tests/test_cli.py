import json
import unittest
import warnings

from helpers import PLUGIN, ProjectCase


class CompileWarningTest(unittest.TestCase):
    def test_every_python_source_compiles_without_warnings(self):
        files = []
        for sub in ("lib", "hooks", "templates", "tests"):
            files += sorted(p for p in (PLUGIN / sub).rglob("*.py") if "__pycache__" not in p.parts)
        for extra in ("tests/fakes/codex-run.sh", "tests/fakes/orca", "tests/fakes/codex", "bin/orch"):
            path = PLUGIN / extra
            if path.is_file() and "python" in path.read_text(encoding="utf-8").splitlines()[0]:
                files.append(path)
        self.assertGreater(len(files), 10)
        for path in files:
            with self.subTest(path=str(path.relative_to(PLUGIN))):
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")


class CliTest(ProjectCase):
    def test_version(self):
        self.assertIn("orca-team 0.7.0", self.orch("--version").stdout)

    def test_version_matches_manifests(self):
        from orch.paths import VERSION
        plugin = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        market = json.loads((PLUGIN.parent / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        entry = next(p for p in market["plugins"] if p["name"] == "orca-team")
        self.assertEqual((plugin["version"], entry["version"]), (VERSION, VERSION))
        for text in (plugin["description"], entry["description"]):  # 0.7.0: 코디네이터 없이 관제·고정 에이전트
            self.assertNotIn("코디네이터", text)
            self.assertIn("관제", text)

    def test_outside_project_exits_2(self):
        proc = self.orch("status", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("/orca-team:init", proc.stderr)

    def test_unknown_command_exits_2(self):
        self.assertEqual(self.orch("nope", check=False).returncode, 2)


class ParserTest(unittest.TestCase):
    """spec §14: 0.7.0 새 명령의 인자 해석과 없앤 명령의 거부."""

    def parse(self, *argv):
        from orch.cli import build_parser
        return build_parser().parse_args(list(argv))

    def test_drive_and_review_view(self):
        self.assertEqual(self.parse("drive").cmd, "drive")
        args = self.parse("review-view", "production")
        self.assertEqual((args.cmd, args.name), ("review-view", "production"))

    def test_agent_reset(self):
        args = self.parse("agent", "reset", "test")
        self.assertEqual((args.cmd, args.agent_cmd, args.name), ("agent", "reset", "test"))

    def test_design_decide_and_scope(self):
        args = self.parse("design-decide", "out-0001", "--approve", "T-001,T-002", "--fix", "T-003=고쳐 줘")
        self.assertEqual((args.id, args.approve, args.fix), ("out-0001", "T-001,T-002", ["T-003=고쳐 줘"]))
        args = self.parse("scope", "T-001", "추가 범위")
        self.assertEqual((args.id, args.body), ("T-001", "추가 범위"))

    def test_fix_extra_round_and_upgrade_force(self):
        self.assertTrue(self.parse("fix", "T-001", "--extra-round").extra_round)
        self.assertFalse(self.parse("fix", "T-001").extra_round)
        self.assertTrue(self.parse("upgrade", "--force").force)
        self.assertFalse(self.parse("upgrade").force)

    def test_tell_and_wait_are_removed(self):
        import contextlib
        import io
        for name in ("tell", "wait"):
            with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    self.parse(name, "x")
                self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
