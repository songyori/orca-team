import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from orch.config import Config, Launch
from orch.paths import OrchError

DATA = {
    "review": {"max_rounds": 2},
    "defaults": {"claude_model": "sonnet", "claude_effort": "xhigh", "codex_model": "gpt-6.1-sol", "codex_effort": "high"},
    "roles": {
        "developer": {"title": "개발", "claude_model": "opus", "codex_model": "gpt-6-astra", "codex_effort": "xhigh",
                      "owns": ["src/**"]},
        "empty": {"title": "빈 직무"},
    },
}


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(DATA)

    def test_role_values_then_defaults(self):
        self.assertEqual(self.cfg.claude("developer"), Launch("opus", "xhigh"))
        self.assertEqual(self.cfg.codex("developer"), Launch("gpt-6-astra", "xhigh"))

    def test_overrides_win(self):
        self.assertEqual(self.cfg.claude("developer", {"claude_model": "fable"}), Launch("fable", "xhigh"))
        self.assertEqual(self.cfg.claude("developer", {"claude_model": "", "claude_effort": "max"}), Launch("opus", "max"))

    def test_unknown_role_is_argument_error(self):
        with self.assertRaises(OrchError) as ctx:
            self.cfg.role("nope")
        self.assertEqual(ctx.exception.code, 2)

    def test_auto_up_defaults_true_and_rejects_non_bool(self):
        self.assertTrue(Config({}).auto_up)
        self.assertTrue(Config({"drive": {"auto_up": True}}).auto_up)
        self.assertFalse(Config({"drive": {"auto_up": False}}).auto_up)
        with self.assertRaises(OrchError):
            Config({"drive": {"auto_up": "yes"}}).auto_up  # noqa: B018

    def test_owns(self):
        self.assertEqual(self.cfg.owns("developer"), ["src/**"])
        self.assertEqual(self.cfg.owns("developer", ["src/a/**"]), ["src/a/**"])
        with self.assertRaises(OrchError):
            self.cfg.owns("empty")

    def test_title_rounds(self):
        self.assertEqual(self.cfg.title("developer"), "개발")
        self.assertEqual(self.cfg.max_rounds, 2)

    def test_malformed_max_rounds_is_orch_error(self):
        for bad in ("abc", [1], None):
            data = {**DATA, "review": {"max_rounds": bad}}
            if bad is None:
                continue  # 값이 없으면 기본 3 이다
            with self.assertRaises(OrchError) as cm:
                Config(data).max_rounds
            self.assertEqual(cm.exception.code, 2)

    def test_non_utf8_config_is_orch_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_bytes(b"\xff\xfe\x00bad")
            with self.assertRaises(OrchError) as cm:
                Config.load(path)
            self.assertEqual(cm.exception.code, 2)

    def test_max_parallel_default_and_value(self):
        self.assertEqual(self.cfg.max_parallel, 4)
        self.assertEqual(Config({**DATA, "max_parallel": 2}).max_parallel, 2)

    def test_malformed_max_parallel_is_orch_error(self):
        for bad in ("abc", 0, True, [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(OrchError) as cm:
                    Config({**DATA, "max_parallel": bad}).max_parallel
                self.assertEqual(cm.exception.code, 2)

    def test_user_approval_roles(self):
        # 키가 없으면 0.2.0 처럼 사용자 승인 게이트가 없다. 새 프로젝트는 templates/config.toml 이 ["designer"] 를 적는다.
        self.assertEqual(self.cfg.user_approval_roles, [])
        self.assertEqual(Config({**DATA, "user_approval_roles": []}).user_approval_roles, [])
        self.assertEqual(Config({**DATA, "user_approval_roles": ["developer", " empty "]}).user_approval_roles,
                         ["developer", "empty"])
        for bad in ("designer", [1], [""]):
            with self.subTest(bad=bad):
                with self.assertRaises(OrchError) as cm:
                    Config({**DATA, "user_approval_roles": bad}).user_approval_roles
                self.assertEqual(cm.exception.code, 2)

    def test_user_approval_roles_must_be_defined_roles(self):
        with self.assertRaises(OrchError) as cm:
            Config({**DATA, "user_approval_roles": ["developer", "designer"]}).user_approval_roles
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("designer", str(cm.exception))


class AgentsConfigTest(unittest.TestCase):
    BASE = {"defaults": {"claude_model": "sonnet", "claude_effort": "xhigh"},
            "roles": {"planner": {"owns": ["docs/planning/**"]}, "developer": {"owns": ["src/**"]},
                      "tester": {"owns": ["tests/**"]}}}

    def cfg(self, agents):
        return Config({**self.BASE, "agents": agents})

    def test_agents_and_lookup(self):
        cfg = self.cfg({"production": {"title": "생산", "roles": ["planner", "developer"], "claude_model": "opus"},
                        "test": {"roles": ["tester"]}})
        prod = cfg.agents["production"]
        self.assertEqual((prod.title, prod.roles, prod.launch), ("생산", ("planner", "developer"), Launch("opus", "xhigh")))
        self.assertEqual((cfg.agents["test"].title, cfg.agents["test"].launch), ("test", Launch("sonnet", "xhigh")))
        self.assertEqual(cfg.agent_for("developer").name, "production")
        self.assertIsNone(Config(self.BASE).agent_for("developer"))

    def test_agents_validation(self):
        for bad in ({"a": {"roles": ["planner"]}, "b": {"roles": ["planner"]}},
                    {"a": {"roles": ["nobody"]}},
                    {"a": {"roles": []}},
                    {"Bad Name": {"roles": ["planner"]}},
                    {"a": "planner"}):
            with self.subTest(bad=bad), self.assertRaises(OrchError):
                self.cfg(bad).agents  # noqa: B018

    def test_overflow_and_drive_auto_up(self):
        self.assertTrue(Config({}).overflow)
        self.assertFalse(Config({"overflow": {"enabled": False}}).overflow)
        self.assertFalse(Config({"drive": {"auto_up": False}}).auto_up)
        for bad in ({"overflow": {"enabled": "no"}}, {"drive": {"auto_up": 1}}):
            with self.subTest(bad=bad), self.assertRaises(OrchError):
                cfg = Config(bad)
                cfg.overflow  # noqa: B018
                cfg.auto_up  # noqa: B018

    def test_load_rejects_legacy_coordinator(self):
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text('[coordinator]\nmodel = "opus"\n', encoding="utf-8")
        with self.assertRaises(OrchError) as ctx:
            Config.load(path)
        self.assertIn("orch upgrade", str(ctx.exception))
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
