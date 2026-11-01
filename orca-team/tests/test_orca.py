import json
import os
import unittest
from unittest.mock import patch

from helpers import FAKES, ProjectCase

from orch import orca
from orch.paths import OrchError


def fake_env(case: ProjectCase) -> dict:
    return {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(case.fake_dir)}


class LastJsonTest(unittest.TestCase):
    def test_skips_keepalive_objects(self):
        text = '{"_keepalive": true}\n{"ok": true, "n": 1}\n{"_keepalive": true}\n'
        self.assertEqual(orca.last_json(text), {"ok": True, "n": 1})

    def test_returns_last_non_keepalive_object(self):
        self.assertEqual(orca.last_json('{"n": 1}{"n": 2}'), {"n": 2})

    def test_returns_last_good_object_when_cut_mid_object(self):
        self.assertEqual(orca.last_json('{"n": 1}\n{"n": 2}\n{"n": '), {"n": 2})

    def test_none_for_non_json_text(self):
        self.assertIsNone(orca.last_json("not json at all"))
        self.assertIsNone(orca.last_json(""))


class CallTest(ProjectCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, fake_env(self))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_binary_raises_code_12(self):
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            with self.assertRaises(OrchError) as cm:
                orca.call(["status"])
        self.assertEqual(cm.exception.code, 12)

    def test_json_flag_is_appended(self):
        orca.call(["status"])
        self.assertEqual(self.calls()[-1], ["status", "--json"])

    def test_check_true_with_ok_false_raises(self):
        self.respond("status", {"ok": False, "error": "boom"})
        with self.assertRaises(OrchError):
            orca.call(["status"])

    def test_check_false_with_ok_false_returns_data(self):
        self.respond("status", {"ok": False, "error": "boom"})
        code, data = orca.call(["status"], check=False)
        self.assertEqual(code, 0)
        self.assertFalse(data["ok"])


class TerminalAliveTest(ProjectCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, fake_env(self))
        patcher.start()
        self.addCleanup(patcher.stop)

    def show(self, **overrides):
        term = {"connected": True, "orphaned": False, "agentIdentity": "claude", **overrides}
        self.respond("terminal show", {"ok": True, "result": {"terminal": term}})

    def test_connected_claude_terminal_is_alive(self):
        self.show()
        self.assertTrue(orca.terminal_alive("term_x"))
        self.assertIn(["terminal", "show", "--terminal", "term_x", "--json"], self.calls())

    def test_none_handle_is_not_alive(self):
        self.assertFalse(orca.terminal_alive(None))
        self.assertEqual(self.calls(), [])

    def test_disconnected_is_not_alive(self):
        self.show(connected=False)
        self.assertFalse(orca.terminal_alive("term_x"))

    def test_orphaned_connected_claude_is_alive(self):
        # Orca 1.4.240 의 orphaned 는 렌더러 pane 마운트 여부라 생존과 무관하다.
        self.show(orphaned=True)
        self.assertTrue(orca.terminal_alive("term_x"))

    def test_non_claude_agent_is_not_alive(self):
        self.show(agentIdentity="codex")
        self.assertFalse(orca.terminal_alive("term_x"))

    def test_unparseable_output_is_not_alive(self):
        self.respond_raw("terminal show", "<<not json>>")
        self.assertFalse(orca.terminal_alive("term_x"))

    def test_missing_binary_still_raises_code_12(self):
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            with self.assertRaises(OrchError) as cm:
                orca.terminal_alive("term_x")
        self.assertEqual(cm.exception.code, 12)


class WorkerTerminalTest(ProjectCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, fake_env(self))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_well_formed_response(self):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w"},
            "projection": {"liveness": {"verdict": "alive"}},
        }})
        self.assertEqual(orca.worker_terminal("d1"), ("term_w", "alive"))
        self.assertIn(["orchestration", "worker-show", "--dispatch", "d1", "--json"], self.calls())

    def test_worker_info_has_stage_and_state(self):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w", "stage": "settled", "state": "succeeded"},
            "projection": {"liveness": {"verdict": "live"}}}})
        self.assertEqual(orca.worker_info("d1"), {"handle": "term_w", "verdict": "live",
                                                  "stage": "settled", "state": "succeeded"})

    def test_worker_info_none_on_failure(self):
        self.respond("orchestration worker-show", {"ok": False, "error": "nope"})
        self.assertIsNone(orca.worker_info("d1"))

    def test_worker_info_missing_binary_raises_code_12(self):
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            with self.assertRaises(OrchError) as cm:
                orca.worker_info("d1")
        self.assertEqual(cm.exception.code, 12)

    def test_ok_false_gives_none_pair(self):
        self.respond("orchestration worker-show", {"ok": False, "error": "nope"})
        self.assertEqual(orca.worker_terminal("d1"), (None, None))

    def test_unparseable_output_gives_none_pair(self):
        self.respond_raw("orchestration worker-show", "<<not json>>")
        self.assertEqual(orca.worker_terminal("d1"), (None, None))

    def test_missing_binary_still_raises_code_12(self):
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            with self.assertRaises(OrchError) as cm:
                orca.worker_terminal("d1")
        self.assertEqual(cm.exception.code, 12)


class FindKeyTest(unittest.TestCase):
    def test_finds_nested_key_depth_first(self):
        node = {"a": {"b": {"dispatchId": 1}}, "c": {"dispatchId": 2}}
        self.assertEqual(orca.find_key(node, ("dispatchId",)), 1)

    def test_finds_key_inside_list(self):
        node = {"wrap": {"items": [{"other": 1}, {"dispatchId": "d9"}]}}
        self.assertEqual(orca.find_key(node, ("dispatchId",)), "d9")

    def test_name_order_wins_at_same_level(self):
        self.assertEqual(orca.find_key({"b": "two", "a": "one"}, ("a", "b")), "one")

    def test_none_when_absent(self):
        self.assertIsNone(orca.find_key({"x": [{"y": {"z": "v"}}]}, ("missing",)))


if __name__ == "__main__":
    unittest.main()
