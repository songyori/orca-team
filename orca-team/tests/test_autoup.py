import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from helpers import FAKES, ProjectCase
from orch import autoup
from orch.paths import Project
from orch.util import now, process_alive

STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}


class AutoUpCase(ProjectCase):
    """가짜 orca 로만 돈다. 같은 프로세스에서 autoup 을 부르므로 os.environ 을 가짜 orca 로 고정한다."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.orch("setup-done")
        env = {k: v for k, v in os.environ.items() if k != "ORCA_TERMINAL_HANDLE"}
        env.update({"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)

    def set_run(self, **run):
        self.p.run_json.write_text(json.dumps(run), encoding="utf-8")

    def set_auto_up(self, value: str):
        path = self.p.config_path
        path.write_text(path.read_text(encoding="utf-8").replace("auto_up = true", f"auto_up = {value}"), encoding="utf-8")

    def ago(self, seconds: float) -> str:
        return (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat(timespec="seconds")

    def check_record(self) -> dict:
        return json.loads((self.p.state / "auto-up.json").read_text(encoding="utf-8"))

    def set_check(self, **fields):
        """auto-up.json 의 필드를 고친다. 예: 확인 간격이 지난 것처럼 checked_at 을 과거로 돌린다."""
        path = self.p.state / "auto-up.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        path.write_text(json.dumps({**data, **fields}), encoding="utf-8")

    def wait_exit(self, pid: int, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(process_alive(pid), "자동 실행한 orch up 이 끝나지 않았다")


class DecideTest(AutoUpCase):
    def test_dead_drive_spawns(self):
        self.set_run(run_id="run_x")
        self.assertEqual(autoup.decide(self.p), ("spawn", autoup.REVIVED))

    def test_alive_does_nothing(self):
        self.set_run(run_id="run_x")
        self.drive_alive()
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_hung_drive_reports_without_spawn(self):
        self.set_run(run_id="run_x")
        self.drive_alive()
        (self.root / ".orchestration/state/drive/heartbeat").write_text("2000-01-01T00:00:00+00:00", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, autoup.HUNG))

    def test_disabled_reports_only(self):
        self.set_run(run_id="run_x")
        self.set_auto_up("false")
        self.assertEqual(autoup.decide(self.p), (None, autoup.DISABLED))

    def test_starting_or_spawn_running_skips(self):
        self.set_run(run_id="run_x", launched_at=self.ago(10))
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_redead_within_retry_window_reports_failed(self):
        self.set_run(run_id="run_x")
        self.set_check(spawned_at=self.ago(60))
        self.assertEqual(autoup.decide(self.p), (None, autoup.FAILED))

    def test_not_eligible_or_broken_config_skips(self):
        self.set_run()
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.set_run(run_id="run_x")
        (self.root / ".orchestration/config.toml").write_text("[coordinator]\n", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, None))


class CheckTest(AutoUpCase):
    def test_interval_and_force(self):
        with patch.object(autoup, "spawn_up", return_value=True) as spawn, \
                patch.object(autoup, "decide", wraps=autoup.decide) as decide:
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
            self.assertIsNone(autoup.check(self.p))
            self.assertEqual(decide.call_count, 1)
            self.assertEqual(autoup.check(self.p, force=True), autoup.FAILED)
            self.assertEqual(decide.call_count, 2)
            self.set_check(checked_at=self.ago(autoup.CHECK_INTERVAL_SECONDS + 1))
            self.assertIsNone(autoup.check(self.p))  # 확인은 하지만 같은 안내는 다시 넘기지 않는다
            self.assertEqual(decide.call_count, 3)
        self.assertEqual(spawn.call_count, 1)

    def test_redead_within_retry_window_stops_spawning(self):
        with patch.object(autoup, "spawn_up", return_value=True) as spawn:
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
            self.assertIn("spawned_at", self.check_record())
            self.assertEqual(autoup.check(self.p, force=True), autoup.FAILED)
            self.assertEqual(spawn.call_count, 1)
            self.set_check(spawned_at=self.ago(autoup.RETRY_WINDOW_SECONDS + 1))
            self.assertEqual(autoup.check(self.p, force=True), autoup.REVIVED)
        self.assertEqual(spawn.call_count, 2)

    def test_real_spawn_is_always_announced(self):
        with patch.object(autoup, "spawn_up", return_value=True) as spawn:
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
            self.set_check(
                checked_at=self.ago(autoup.CHECK_INTERVAL_SECONDS + 1),
                spawned_at=self.ago(autoup.RETRY_WINDOW_SECONDS + 1),
            )
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
        self.assertEqual(spawn.call_count, 2)

    def test_same_notice_once_until_changed_or_forced(self):
        self.set_auto_up("false")
        self.assertEqual(autoup.check(self.p), autoup.DISABLED)
        self.set_check(checked_at=self.ago(autoup.CHECK_INTERVAL_SECONDS + 1))
        self.assertIsNone(autoup.check(self.p))
        self.assertEqual(autoup.check(self.p, force=True), autoup.DISABLED)
        self.drive_alive()
        self.set_check(checked_at=self.ago(autoup.CHECK_INTERVAL_SECONDS + 1))
        self.assertIsNone(autoup.check(self.p))
        self.assertNotIn("notice", self.check_record())
        self.p.drive_pid.unlink()
        self.set_check(checked_at=self.ago(autoup.CHECK_INTERVAL_SECONDS + 1))
        self.assertEqual(autoup.check(self.p), autoup.DISABLED)

    def test_corrupt_check_file_is_due(self):
        (self.p.state / "auto-up.json").write_text("garbage", encoding="utf-8")
        self.assertTrue(autoup.due(self.p))

    def test_spawn_failure_reports_unknown(self):
        with patch.object(autoup, "spawn_up", return_value=False):
            self.assertEqual(autoup.check(self.p), autoup.FAILED_SPAWN)
        self.assertNotIn("spawned_at", self.check_record())

    def test_not_eligible_writes_nothing(self):
        self.p.setup_mode.write_text("", encoding="utf-8")
        self.assertIsNone(autoup.check(self.p, force=True))
        self.assertFalse((self.p.state / "auto-up.json").exists())


class SpawnTest(AutoUpCase):
    def test_spawn_runs_orch_up_detached(self):
        self.set_run(run_id="run_x", drive_handle="term_old")
        self.respond("terminal show", STALE, code=1)
        self.respond("terminal create", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        self.assertTrue(autoup.spawn_up(self.p))
        pid = int((self.p.state / "auto-up.pid").read_text(encoding="utf-8").split()[0])
        self.wait_exit(pid)
        self.assertEqual(json.loads(self.p.run_json.read_text(encoding="utf-8"))["drive_handle"], "term_new")
        log = (self.p.state / "auto-up.log").read_text(encoding="utf-8")
        self.assertIn("orch up (자동)", log)
        self.assertIn("launched", log)
