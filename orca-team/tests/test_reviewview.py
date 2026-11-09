import io
import json
import os
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

    def test_ignores_other_agents_and_unknown_name(self):
        self.state(status="reviewing", agent="temp")
        self.viewer.step()
        self.assertEqual(self.out.getvalue(), "")
        with self.assertRaises(OrchError):
            reviewview.main(self.p, self.cfg, "nobody")


if __name__ == "__main__":
    unittest.main()
