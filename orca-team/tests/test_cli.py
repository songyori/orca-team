import json
import unittest

from helpers import PLUGIN, ProjectCase


class CliTest(ProjectCase):
    def test_version(self):
        self.assertIn("orca-team 0.5.1", self.orch("--version").stdout)

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
