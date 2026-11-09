import io
import json
import os
import time
import unittest
from unittest import mock

from helpers import ProjectCase
from orch import reviewview
from orch.config import Config
from orch.paths import OrchError, Project


class ViewerTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold(agents=True)
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)
        self.cfg = Config.load(self.root / ".orchestration/config.toml")
        self.out = io.StringIO()
        self.viewer = reviewview.Viewer(self.p, self.cfg, "production", out=self.out)

    def state(self, **fields):
        path = self.root / ".orchestration/state/tasks/T-001.json"
        base = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "id": "T-001", "role": "developer", "title": "로그인", "round": 2, "agent": "production", "history": []}
        base.update(fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(base), encoding="utf-8")

    def run_dir(self):
        path = self.tmp / "cache/orca-team/runs" / self.root.name / "T-001-r2-20261109-120000-a1"
        path.mkdir(parents=True)
        return path

    def test_shows_running_review_then_verdict(self):
        self.state(status="reviewing")
        run = self.run_dir()
        (run / "progress.log").write_text("파일 읽는 중\n", encoding="utf-8")
        self.viewer.step()
        text = self.out.getvalue()
        self.assertIn("T-001 r2 리뷰 중 (gpt-6.1-sol, xhigh)", text)
        self.assertIn("파일 읽는 중", text)
        with (run / "progress.log").open("a", encoding="utf-8") as fh:
            fh.write("판정 쓰는 중\n")
        self.viewer.step()
        self.assertEqual(self.out.getvalue().count("파일 읽는 중"), 1)
        self.assertIn("판정 쓰는 중", self.out.getvalue())
        self.state(status="approved", history=[{"stage": "review", "round": 2, "verdict": "APPROVE"}])
        self.viewer.step()
        self.assertIn("T-001 r2 APPROVE — docs/reviews/T-001/r2.md", self.out.getvalue())

    def test_finish_prints_last_log_lines_before_verdict(self):
        self.state(status="reviewing")
        run = self.run_dir()
        (run / "progress.log").write_text("파일 읽는 중\n", encoding="utf-8")
        self.viewer.step()
        with (run / "progress.log").open("a", encoding="utf-8") as fh:
            fh.write("마지막 줄\n")
        self.state(status="approved", history=[{"stage": "review", "round": 2, "verdict": "APPROVE"}])
        self.viewer.step()
        text = self.out.getvalue()
        self.assertIn("마지막 줄", text)
        self.assertLess(text.index("마지막 줄"), text.index("T-001 r2 APPROVE"))

    def test_ignores_stale_run_dir_of_same_round(self):
        old = self.run_dir()
        (old / "progress.log").write_text("옛 기록\n", encoding="utf-8")
        past = time.time() - 3600
        for path in (old, old / "progress.log"):
            os.utime(path, (past, past))
        self.state(status="reviewing")
        self.viewer.step()
        self.assertNotIn("옛 기록", self.out.getvalue())
        new = self.tmp / "cache/orca-team/runs" / self.root.name / "T-001-r2-20261109-130000-a2"
        new.mkdir(parents=True)
        (new / "progress.log").write_text("새 기록\n", encoding="utf-8")
        self.viewer.step()
        self.assertIn("새 기록", self.out.getvalue())
        self.assertNotIn("옛 기록", self.out.getvalue())

    def test_switches_to_retry_run_dir_with_separator_once(self):
        self.state(status="reviewing")
        first = self.run_dir()
        (first / "progress.log").write_text("첫 시도 기록\n", encoding="utf-8")
        self.viewer.step()
        second = self.tmp / "cache/orca-team/runs" / self.root.name / "T-001-r2-20261109-130000-a2"
        second.mkdir(parents=True)
        (second / "progress.log").write_text("재시도 기록\n", encoding="utf-8")
        self.viewer.step()
        self.viewer.step()
        text = self.out.getvalue()
        self.assertEqual(text.count("--- 다시 시도 (a2) ---"), 1)
        self.assertLess(text.index("첫 시도 기록"), text.index("--- 다시 시도 (a2) ---"))
        self.assertLess(text.index("--- 다시 시도 (a2) ---"), text.index("재시도 기록"))
        self.assertEqual(text.count("재시도 기록"), 1)

    def test_finish_shows_log_when_viewer_never_found_run_dir(self):
        self.state(status="reviewing")
        self.viewer.step()
        run = self.run_dir()
        (run / "progress.log").write_text("끝난 리뷰 기록\n", encoding="utf-8")
        self.state(status="approved", history=[{"stage": "review", "round": 2, "verdict": "APPROVE"}])
        self.viewer.step()
        text = self.out.getvalue()
        self.assertIn("끝난 리뷰 기록", text)
        self.assertLess(text.index("끝난 리뷰 기록"), text.index("T-001 r2 APPROVE"))

    def test_reports_error_again_after_a_good_iteration(self):
        script = [RuntimeError("boom"), None, RuntimeError("boom"), KeyboardInterrupt()]

        def fake_step():
            item = script.pop(0)
            if isinstance(item, BaseException):
                raise item

        self.viewer.step = fake_step
        with mock.patch.object(reviewview.time, "sleep"):
            self.viewer.loop()
        self.assertEqual(self.out.getvalue().count("리뷰 보기 오류: RuntimeError: boom"), 2)

    def test_reports_abort_when_review_goes_back_to_working(self):
        self.state(status="reviewing")
        self.viewer.step()
        self.state(status="working")
        self.viewer.step()
        self.assertIn("T-001 r2 리뷰 중단", self.out.getvalue())

    def test_header_without_model_for_unknown_role_and_non_integer_round(self):
        self.state(status="reviewing", role="nobody", round="x")
        self.viewer.step()
        self.assertIn("T-001 r1 리뷰 중\n", self.out.getvalue())

    def test_header_honors_task_front_matter_override(self):
        spec = self.p.tasks_dir / "T-001.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("---\nid: T-001\nrole: developer\ncodex_model: gpt-override\n---\n\n## 목적\n본문\n",
                        encoding="utf-8")
        self.state(status="reviewing")
        self.viewer.step()
        self.assertIn("(gpt-override, xhigh)", self.out.getvalue())

    def test_loop_reports_each_new_error_once_and_keeps_polling(self):
        script = [RuntimeError("boom"), RuntimeError("boom"), ValueError("bad"), KeyboardInterrupt()]

        def fake_step():
            item = script.pop(0)
            if isinstance(item, BaseException):
                raise item

        self.viewer.step = fake_step
        with mock.patch.object(reviewview.time, "sleep"):
            self.viewer.loop()
        text = self.out.getvalue()
        self.assertEqual(text.count("리뷰 보기 오류: RuntimeError: boom"), 1)
        self.assertEqual(text.count("리뷰 보기 오류: ValueError: bad"), 1)
        self.assertEqual(script, [])

    def test_ignores_other_agents_and_unknown_name(self):
        self.state(status="reviewing", agent="temp")
        self.viewer.step()
        self.assertEqual(self.out.getvalue(), "")
        with self.assertRaises(OrchError):
            reviewview.main(self.p, self.cfg, "nobody")


if __name__ == "__main__":
    unittest.main()
