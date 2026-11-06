"""코디네이터 자동 부활. 메인 세션의 SessionStart·UserPromptSubmit 훅이 check() 를 부른다.

확실히 죽은(dead) 코디네이터만 `orch up` 을 분리된 프로세스로 다시 띄운다. 확인할 수 없으면(unknown) 띄우지 않고
메인에게 알린다. 요청은 Run 메일함에 쌓이므로 새 코디네이터가 붙을 때까지 기다리지 않는다."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import orca
from .config import Config
from .paths import OrchError, Project
from .setup import starting
from .util import load_run, now, pid_alive, read_json, write_json

CHECK_INTERVAL_SECONDS = 60  # 프롬프트 때 확인 간격. 세션 시작 때는 force 로 항상 확인한다
RETRY_WINDOW_SECONDS = 600  # 자동으로 띄운 뒤 이 안에 다시 꺼지면 더 띄우지 않는다(시작 실패 반복 방지)
PID_STALE_SECONDS = 60  # orch up 은 대기 없이 몇 초 안에 끝난다. 더 오래된 pid 파일은 진행 중으로 보지 않는다
PREFIX = "orca-team: "
REVIVED = (PREFIX + "코디네이터가 꺼져 있어 자동으로 다시 띄우는 중이다. 답변 첫 줄에 사용자에게 이 사실을 한 줄로 알린다. "
           "요청은 평소대로 orch tell 로 넘긴다(Run 메일함에 쌓였다가 코디네이터가 붙으면 처리된다). "
           "메시지 watcher 가 꺼져 있으면 띄우고, 답하지 않은 질문이 있으면 orch reask 로 다시 보여 준다.")
UNKNOWN = (PREFIX + "Orca 에 연결하지 못해 코디네이터가 살아 있는지 확인하지 못했다. 답변 첫 줄에 사용자에게 "
           "Orca 가 켜져 있는지 확인해 달라고 한 줄로 알린다. 확인되면 /orca-team:up 을 실행한다.")
NO_HANDLE = (PREFIX + "코디네이터 실행 기록이 불확실하다(run.json 에 코디네이터 터미널이 없다). 답변 첫 줄에 "
             "사용자에게 한 줄로 알리고 /orca-team:up 결과를 확인한다.")
DISABLED = (PREFIX + "코디네이터가 꺼져 있다(config.toml 의 auto_up = false). 답변 첫 줄에 사용자에게 한 줄로 "
            "알리고 /orca-team:up 을 안내한다.")
FAILED = (PREFIX + "자동으로 다시 띄운 코디네이터가 10분 안에 다시 꺼졌다(기록: .orchestration/state/auto-up.log). "
          "답변 첫 줄에 사용자에게 한 줄로 알리고, 원인을 확인한 뒤 /orca-team:up 을 실행하도록 안내한다. "
          "자동으로는 더 띄우지 않는다.")
# sh 가 orch up 을 백그라운드로 띄우고 pid 를 출력한 뒤 바로 끝난다. 훅이 시간 제한으로 끊겨도 orch up 은 계속 돈다.
LAUNCHER = '"$0" "$@" </dev/null >>"$ORCA_TEAM_AUTO_UP_LOG" 2>&1 & echo $!'


def check_file(p: Project) -> Path:
    return p.state / "auto-up.json"


def pid_file(p: Project) -> Path:
    return p.state / "auto-up.pid"


def log_file(p: Project) -> Path:
    return p.state / "auto-up.log"


def eligible(p: Project) -> bool:
    """세팅이 끝난 프로젝트인가(run.json 에 run_id 가 있고 세팅 중이 아님). 메인 세션인지는 훅이 본다."""
    return bool(load_run(p).get("run_id")) and not p.setup_mode.exists()


def check_record(p: Project) -> dict:
    """auto-up.json: checked_at(마지막 확인), spawned_at(마지막 자동 실행), notice(마지막으로 넘긴 문구)."""
    data = read_json(check_file(p), {})
    return data if isinstance(data, dict) else {}


def within(stamp, seconds: float) -> bool:
    """ISO 8601 시각 stamp 뒤 seconds 가 아직 지나지 않았는가. 없거나 읽을 수 없거나 미래 시각이면 False."""
    try:
        age = (datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return False
    return 0 <= age < seconds


def due(p: Project) -> bool:
    """마지막 확인 뒤 CHECK_INTERVAL_SECONDS 가 지났는가. 기록이 없거나 읽을 수 없으면 True."""
    return not within(check_record(p).get("checked_at"), CHECK_INTERVAL_SECONDS)


def spawn_running(p: Project) -> bool:
    """자동으로 띄운 orch up 이 아직 도는가. pid 파일이 PID_STALE_SECONDS 보다 오래됐으면 다른 프로세스가 재사용한
    pid 일 수 있어 보지 않는다."""
    try:
        age = time.time() - pid_file(p).stat().st_mtime
    except OSError:
        return False
    return 0 <= age < PID_STALE_SECONDS and pid_alive(pid_file(p))


def decide(p: Project) -> tuple[str | None, str | None]:
    """(action, context). action 은 "spawn" 또는 None, context 는 메인에게 넘길 문구 또는 None.
    파일은 쓰지 않는다(orca 조회만 한 번 한다). 판정 순서는 설계 §4.1 을 따른다."""
    if not eligible(p):
        return None, None
    try:
        enabled = Config.load(p.config_path).auto_up
    except OrchError:
        return None, None
    run = load_run(p)
    if starting(run) or spawn_running(p):
        return None, None
    handle = run.get("coordinator_handle")
    if not handle:
        return None, NO_HANDLE
    state = orca.terminal_state(handle)
    if state == "alive":
        return None, None
    if state == "unknown":
        return None, UNKNOWN
    if within(check_record(p).get("spawned_at"), RETRY_WINDOW_SECONDS):
        return None, FAILED
    return ("spawn", REVIVED) if enabled else (None, DISABLED)


def spawn_up(p: Project) -> bool:
    """`orch up` 을 분리된 프로세스로 띄우고 pid 를 auto-up.pid 에 남긴다. 기다리지 않는다. 띄우지 못하면 False.
    환경을 그대로 물려줘 orch up 이 메인 자신의 터미널(ORCA_TERMINAL_HANDLE)을 코디네이터로 기록하지 않게 한다."""
    orch_bin = Path(__file__).resolve().parents[2] / "bin" / "orch"
    p.ensure_state()
    log = log_file(p)
    try:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {now()} orch up (자동)\n")
        proc = subprocess.run(["/bin/sh", "-c", LAUNCHER, sys.executable, str(orch_bin), "up"], cwd=p.root,
                              env={**os.environ, "ORCA_TEAM_AUTO_UP_LOG": str(log)}, capture_output=True,
                              text=True, timeout=5, start_new_session=True)
        pid = int(proc.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return False
    pid_file(p).write_text(f"{pid}\n", encoding="utf-8")
    return True


def check(p: Project, force: bool = False) -> str | None:
    """훅 진입점. 확인 간격을 지키고(force 면 무시), 결정대로 실행한 뒤 메인에게 넘길 문구를 돌려준다.
    직전에 넘긴 문구(notice)와 같으면 넘기지 않는다(force 면 넘긴다). 문구가 없으면 notice 를 지워 다음 문제는 다시 알린다."""
    if not eligible(p) or not (force or due(p)):
        return None
    record = {**check_record(p), "checked_at": now()}
    write_json(check_file(p), record)
    action, context = decide(p)
    if action == "spawn":
        if spawn_up(p):
            record["spawned_at"] = now()
        else:
            context = UNKNOWN
    repeated = context is not None and context == record.get("notice")
    if context is None:
        record.pop("notice", None)
    else:
        record["notice"] = context
    write_json(check_file(p), record)
    return None if repeated and not force else context
