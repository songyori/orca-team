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
        self.assertIn("orca-team 0.6.1", self.orch("--version").stdout)

    def test_version_matches_manifests(self):
        from orch.paths import VERSION
        plugin = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        market = json.loads((PLUGIN.parent / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        entry = next(p for p in market["plugins"] if p["name"] == "orca-team")
        self.assertEqual((plugin["version"], entry["version"]), (VERSION, VERSION))

    def test_outside_project_exits_2(self):
        proc = self.orch("status", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("/orca-team:init", proc.stderr)

    def test_unknown_command_exits_2(self):
        self.assertEqual(self.orch("nope", check=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
