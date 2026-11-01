"""테스트 공용: 임시 git 프로젝트와 가짜 orca/codex/Codex 래퍼 환경."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
FAKES = PLUGIN / "tests" / "fakes"
ORCH = PLUGIN / "bin" / "orch"
sys.path.insert(0, str(PLUGIN / "lib"))


class ProjectCase(unittest.TestCase):
    """임시 git 프로젝트. 기본 환경은 코디네이터 터미널(ORCA_TERMINAL_HANDLE=term_coord)이다."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orca-team-test-")).resolve()
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.fake_dir = self.tmp / "fake"
        self.fake_dir.mkdir()
        self.env = {
            **os.environ,
            "ORCA_TEAM_ORCA": str(FAKES / "orca"),
            "ORCA_TEAM_CODEX": str(FAKES / "codex"),
            "ORCA_TEAM_CODEX_RUN": str(FAKES / "codex-run.sh"),
            "ORCA_TEAM_PLUGINS_JSON": str(self.tmp / "installed_plugins.json"),
            "ORCA_TEAM_KNOWN_MARKETPLACES_JSON": str(self.tmp / "known_marketplaces.json"),
            "ORCA_TEAM_PLUGIN_ROOT": str(PLUGIN),
            "FAKE_DIR": str(self.fake_dir),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "ORCA_TERMINAL_HANDLE": "term_coord",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            # 사용자 git 설정(서명, hooksPath, diff.renames 등)이 테스트에 섞이지 않게 한다.
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        }
        for key in ("ORCA_CLI_COMMAND", "CLAUDE_PROJECT_DIR"):
            self.env.pop(key, None)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, check=True).stdout

    def orch(self, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([sys.executable, str(ORCH), *args], cwd=self.root,
                              env={**self.env, **(env or {})}, capture_output=True, text=True)
        if check and proc.returncode != 0:
            self.fail(f"orch {' '.join(args)} exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
        return proc

    def respond(self, key: str, body: dict, code: int = 0) -> None:
        """가짜 orca 응답 등록. key 예: 'orchestration worker-start', 'status'."""
        path = self.fake_dir / (key.replace(" ", "_") + ".json")
        path.write_text(json.dumps({"code": code, "body": body}), encoding="utf-8")

    def respond_raw(self, key: str, text: str, code: int = 0) -> None:
        """가짜 orca 가 JSON 이 아닌 원문 text 를 그대로 출력하도록 등록한다."""
        path = self.fake_dir / (key.replace(" ", "_") + ".json")
        path.write_text(json.dumps({"code": code, "raw": text}), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.fake_dir / "orca.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def calls_for(self, key: str) -> list[list[str]]:
        parts = key.split()
        return [c for c in self.calls() if c[:len(parts)] == parts]

    def scaffold(self) -> None:
        """orch scaffold 후 코디네이터가 Run 을 만든 상태로 둔다."""
        desc = self.tmp / "desc.txt"
        desc.write_text("테스트 프로젝트. 할 일 웹앱.", encoding="utf-8")
        self.orch("scaffold", "--description-file", str(desc))
        (self.root / ".orchestration/state/run.json").write_text(
            json.dumps({"run_id": "run_x", "coordinator_handle": "term_coord"}), encoding="utf-8")

    def remove_role(self, role: str) -> None:
        """config.toml 에서 [roles.<role>] 표를 지운다(그 직무가 없던 이전 버전 프로젝트)."""
        path = self.root / ".orchestration/config.toml"
        text = path.read_text(encoding="utf-8")
        trimmed = re.sub(rf"\n\[roles\.{re.escape(role)}\]\n(?:[^\[\n].*\n|\n)*", "\n", text)
        if trimmed == text:
            raise AssertionError(f"config.toml 에 [roles.{role}] 가 없다")
        path.write_text(trimmed, encoding="utf-8")
