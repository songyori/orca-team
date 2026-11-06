import json
import os
import subprocess
import sys
import time
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

    def test_timeout_raises_orch_error(self):
        self.respond("status", {"ok": True}, sleep=3)
        started = time.monotonic()
        with self.assertRaises(OrchError) as cm:
            orca.call(["status"], timeout=0.5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("orca status 시간 초과(0.5초)", str(cm.exception))
        self.assertNotEqual(cm.exception.code, 12)


class HelpersSafetyTest(unittest.TestCase):
    def test_helpers_import_defaults_orca_to_fake(self):
        """같은 프로세스 테스트가 가짜 orca 설정을 빠뜨려도 실제 Orca 에 닿지 않는다."""
        env = {k: v for k, v in os.environ.items() if k not in ("ORCA_TEAM_ORCA", "ORCA_CLI_COMMAND")}
        proc = subprocess.run([sys.executable, "-c", "import helpers\nfrom orch.paths import orca_bin\nprint(orca_bin())"],
                              cwd=FAKES.parent, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(proc.stdout.strip(), str(FAKES / "orca"))


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


class TerminalStateTest(ProjectCase):
    """자동 부활은 dead 일 때만 다시 띄운다. 확인할 수 없으면 unknown 이다."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, fake_env(self))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_states(self):
        cases = [
            ({"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}, 0, "alive"),
            ({"ok": False, "error": {"code": "terminal_handle_stale"}}, 1, "dead"),
            ({"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}, 0, "dead"),
            ({"ok": True, "result": {"terminal": {"connected": False, "agentIdentity": "claude"}}}, 0, "dead"),
            ({"ok": True, "result": {}}, 0, "unknown"),
            ({"ok": True, "result": "x"}, 0, "unknown"),
            ({"ok": False, "error": {"code": "runtime_unavailable"}}, 1, "unknown"),
        ]
        for body, code, expected in cases:
            with self.subTest(body=body):
                self.respond("terminal show", body, code=code)
                self.assertEqual(orca.terminal_state("term_x"), expected)

    def test_unparseable_and_missing_binary_are_unknown(self):
        self.respond_raw("terminal show", "<<not json>>")
        self.assertEqual(orca.terminal_state("term_x"), "unknown")
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            self.assertEqual(orca.terminal_state("term_x"), "unknown")

    def test_unexecutable_binary_is_unknown(self):
        noexec = self.tmp / "orca-noexec"
        noexec.write_text("#!/bin/sh\n", encoding="utf-8")
        noexec.chmod(0o644)
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(noexec)}):
            self.assertEqual(orca.terminal_state("term_x"), "unknown")

    def test_slow_orca_times_out_as_unknown(self):
        """훅(10초 제한) 안에서 도는 확인이 느린 Orca 에 묶이지 않는다."""
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}},
                     sleep=3)
        started = time.monotonic()
        with patch.object(orca, "TERMINAL_STATE_TIMEOUT", 0.5):
            self.assertEqual(orca.terminal_state("term_x"), "unknown")
        self.assertLess(time.monotonic() - started, 2)

    def test_undecodable_output_is_unknown(self):
        err = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")
        with patch.object(orca, "call", side_effect=err):
            self.assertEqual(orca.terminal_state("term_x"), "unknown")

    def test_no_handle_is_unknown_without_calling_orca(self):
        self.assertEqual(orca.terminal_state(None), "unknown")
        self.assertEqual(self.calls(), [])

    def test_alive_uses_same_verdict(self):
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}})
        self.assertFalse(orca.terminal_alive("term_x"))
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertFalse(orca.terminal_alive("term_x"))


if __name__ == "__main__":
    unittest.main()
