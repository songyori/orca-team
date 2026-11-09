"""테스트 공용: 임시 git 프로젝트와 가짜 orca/codex/Codex 래퍼 환경."""
from __future__ import annotations

import json
import os
import re
import shlex
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
# 같은 프로세스 테스트가 가짜 orca 설정을 빠뜨려도 실제 Orca 에 닿지 않는다(FAKE_DIR 이 없으면 가짜 orca 가 실패할 뿐).
os.environ.setdefault("ORCA_TEAM_ORCA", str(FAKES / "orca"))


class ProjectCase(unittest.TestCase):
    """임시 git 프로젝트. 기본 환경은 관제 터미널(ORCA_TERMINAL_HANDLE=term_drive)이다."""

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
            "ORCA_TERMINAL_HANDLE": "term_drive",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            # 사용자 git 설정(서명, hooksPath, diff.renames 등)이 테스트에 섞이지 않게 한다.
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        }
        for key in ("ORCA_CLI_COMMAND", "CLAUDE_PROJECT_DIR", "CLAUDECODE"):
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

    def respond(self, key: str, body: dict, code: int = 0, sleep: float = 0) -> None:
        """가짜 orca 응답 등록. key 예: 'orchestration worker-start', 'status'. sleep 초만큼 늦게 응답한다."""
        path = self.fake_dir / (key.replace(" ", "_") + ".json")
        spec = {"code": code, "body": body, **({"sleep": sleep} if sleep else {})}
        path.write_text(json.dumps(spec), encoding="utf-8")

    def respond_raw(self, key: str, text: str, code: int = 0) -> None:
        """가짜 orca 가 JSON 이 아닌 원문 text 를 그대로 출력하도록 등록한다."""
        path = self.fake_dir / (key.replace(" ", "_") + ".json")
        path.write_text(json.dumps({"code": code, "raw": text}), encoding="utf-8")

    def respond_seq(self, key: str, *bodies, code: int = 0) -> None:
        """가짜 orca 순서 응답. 호출마다 bodies 를 앞에서 하나씩 쓰고, 다 쓰면 respond 로 등록한 응답(없으면 기본)을 쓴다.
        body 대신 (body, code) 튜플을 주면 그 종료 코드를 쓴다."""
        items = []
        for body in bodies:
            value, exit_code = body if isinstance(body, tuple) else (body, code)
            items.append({"code": exit_code, "body": value})
        path = self.fake_dir / (key.replace(" ", "_") + ".seq.json")
        path.write_text(json.dumps(items), encoding="utf-8")

    def calls(self) -> list[list[str]]:
        log = self.fake_dir / "orca.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def calls_for(self, key: str) -> list[list[str]]:
        parts = key.split()
        return [c for c in self.calls() if c[:len(parts)] == parts]

    def scaffold(self, agents: bool = False) -> None:
        """orch scaffold 후 Run 이 있는 상태로 둔다. agents=False 면 config.toml 의 [agents.*] 를 지워
        모든 태스크가 임시 워커로 돈다(0.6.0 까지의 흐름)."""
        desc = self.tmp / "desc.txt"
        desc.write_text("테스트 프로젝트. 할 일 웹앱.", encoding="utf-8")
        self.orch("scaffold", "--description-file", str(desc))
        (self.root / ".orchestration/state/run.json").write_text(
            json.dumps({"run_id": "run_x", "drive_handle": "term_drive"}), encoding="utf-8")
        if not agents:
            path = self.root / ".orchestration/config.toml"
            text = path.read_text(encoding="utf-8")
            path.write_text(re.sub(r"(?m)^\[agents\.[a-z0-9-]+\]\n(?:[^\[\n].*\n|\n)*", "", text), encoding="utf-8")

    def drive_alive(self) -> None:
        """관제가 살아 있는 것으로 보이게 한다(이 테스트 프로세스 pid 와 지금 heartbeat)."""
        drive_dir = self.root / ".orchestration/state/drive"
        drive_dir.mkdir(parents=True, exist_ok=True)
        (drive_dir / "drive.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        from orch.util import now
        (drive_dir / "heartbeat").write_text(now(), encoding="utf-8")

    def remove_role(self, role: str) -> None:
        """config.toml 에서 [roles.<role>] 표를 지운다(그 직무가 없던 이전 버전 프로젝트)."""
        path = self.root / ".orchestration/config.toml"
        text = path.read_text(encoding="utf-8")
        trimmed = re.sub(rf"\n\[roles\.{re.escape(role)}\]\n(?:[^\[\n].*\n|\n)*", "\n", text)
        if trimmed == text:
            raise AssertionError(f"config.toml 에 [roles.{role}] 가 없다")
        path.write_text(trimmed, encoding="utf-8")


FAKE_TEST = """import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
with (here / "test-args.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
modes_file = here / "test_modes.txt"
modes = modes_file.read_text(encoding="utf-8").split() if modes_file.exists() else []
mode = modes.pop(0) if modes else "pass"
modes_file.write_text("".join(m + "\\n" for m in modes), encoding="utf-8")
print("fake test", mode)
print()
print("done")
if mode == "fail":
    print("FAIL tests/a.test.ts > 더하기")
    sys.exit(1)
sys.exit({"five": 5, "missing": 127}.get(mode, 0))
"""


def fake_test_commands(case) -> tuple[str, str]:
    """가짜 테스트 명령 (quick, full). 인자는 $FAKE_DIR/test-args.jsonl 에 한 줄씩 남고,
    행동은 $FAKE_DIR/test_modes.txt 의 첫 단어(pass|fail|five|missing, 없으면 pass)다."""
    script = case.fake_dir / "fake_test.py"
    script.write_text(FAKE_TEST, encoding="utf-8")
    run = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    return f"{run} {{files}}", f"{run} --all"


def write_test_config(root, quick: str, full: str, extra: str = "") -> None:
    """docs/planning/tasks.toml 을 [test] 표 하나로 덮어쓴다. extra 는 [test] 안에 더할 줄(끝 줄바꿈 포함)."""
    path = Path(root) / "docs/planning/tasks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[test]\nquick = {json.dumps(quick)}\nfull = {json.dumps(full)}\n{extra}", encoding="utf-8")


def set_test_modes(case, *modes: str) -> None:
    (case.fake_dir / "test_modes.txt").write_text("".join(m + "\n" for m in modes), encoding="utf-8")


def read_test_args(case) -> list[list[str]]:
    log = case.fake_dir / "test-args.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
