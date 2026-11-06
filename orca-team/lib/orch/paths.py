"""경로와 외부 도구 위치. 테스트는 환경변수로 덮어쓴다."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

VERSION = "0.5.1"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = PLUGIN_ROOT / "templates"


class OrchError(Exception):
    """사용자에게 보여줄 오류. code 는 종료 코드다(1 실패, 2 인자·상태, 3 중복, 12 의존성 없음).

    종료 코드 4(ask-user 답 대기 중)는 오류가 아니라 cli 가 결과 JSON 과 함께 돌려준다."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def orca_bin() -> str:
    return os.environ.get("ORCA_TEAM_ORCA") or os.environ.get("ORCA_CLI_COMMAND") or "orca"


def codex_bin() -> str:
    return os.environ.get("ORCA_TEAM_CODEX") or "codex"


def codex_runner() -> Path:
    """플러그인에 들어 있는 Codex 래퍼(scripts/codex-run.sh). 테스트는 ORCA_TEAM_CODEX_RUN 으로 바꾼다."""
    override = os.environ.get("ORCA_TEAM_CODEX_RUN")
    runner = Path(override) if override else PLUGIN_ROOT / "scripts" / "codex-run.sh"
    if not runner.is_file():
        raise OrchError(f"Codex 래퍼가 없다({runner}). 플러그인 설치가 깨졌다. orca-team 을 다시 설치한다", 12)
    return runner


def git_toplevel(start: Path) -> Path | None:
    proc = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def find_root(start: Path | None = None) -> Path:
    """`.orchestration/config.toml` 이 있는 가장 가까운 상위 폴더."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".orchestration" / "config.toml").is_file():
            return folder
    raise OrchError("orca-team 프로젝트가 아니다(.orchestration/config.toml 없음). /orca-team:init 을 먼저 실행한다", 2)


class Project:
    """프로젝트 안의 orca-team 경로 모음."""

    def __init__(self, root: Path):
        self.root = root
        self.orch = root / ".orchestration"
        self.config_path = self.orch / "config.toml"
        self.tasks_dir = self.orch / "tasks"
        self.state = self.orch / "state"
        self.task_state_dir = self.state / "tasks"
        self.sessions = self.state / "sessions"
        self.run_json = self.state / "run.json"
        self.outbox = self.state / "outbox.jsonl"
        self.outbox_cursor = self.state / "outbox.cursor"
        self.watch_pid = self.state / "watch.pid"
        self.wait_pid = self.state / "wait.pid"
        self.main_msg_ids = self.state / "main-msg-ids.json"
        self.setup_mode = self.state / "setup-mode"
        self.answers_dir = self.state / "answers"  # orch answer 가 쓰는 ask 답 파일과 읽음 표시
        self.plan_json = self.state / "plan.json"  # plan apply 의 key → T-id 와 지문
        self.design_review_json = self.state / "design-review.json"  # design-review 질문 이력
        self.answered_json = self.state / "answered.json"  # orch answer 가 답한 question·approval id 와 기록 시작점

    def ensure_state(self) -> None:
        for folder in (self.state, self.task_state_dir, self.sessions):
            folder.mkdir(parents=True, exist_ok=True)
