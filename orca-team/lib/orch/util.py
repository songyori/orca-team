"""공용 함수: 시간, JSON 파일, 템플릿 치환, git, pid, Run 상태, 코디네이터 확인."""
from __future__ import annotations

import fcntl
import json
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .paths import OrchError, Project

PLACEHOLDER = re.compile(r"\{(\w+)\}")
READ_ONLY_GIT = frozenset({"status", "diff", "log", "rev-parse", "diff-tree", "merge-base", "show", "cat-file"})
COMMIT_LOCK_BUDGET = 30.0  # 다른 워커의 index.lock 을 기다리는 최대 초
DRIVE_STALE_SECONDS = 120  # drive heartbeat 가 이보다 오래되면 죽은 것으로 본다


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def record_plugin_root(state: Path, root: Path) -> None:
    """state/plugin-root 에 플러그인 루트를 원자적으로 적는다. shim 이 이 코드를 따라간다. 쓰기 실패는 무시한다."""
    try:
        state.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=state, prefix=".plugin-root.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(root) + "\n")
            os.replace(tmp, state / "plugin-root")
        except OSError:
            Path(tmp).unlink(missing_ok=True)
    except OSError:
        pass


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def render(template: str, values: dict) -> str:
    """`{key}` 를 한 번에 치환한다. 넣은 값 안의 중괄호는 다시 치환하지 않고, 모르는 키는 그대로 둔다."""
    return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def git_env(args) -> dict | None:
    """읽기 전용 git 호출은 선택적 잠금(index 새로 고침)을 하지 않는다. 같은 체크아웃의 워커 커밋과 index.lock 을 다투지 않는다."""
    if args and args[0] in READ_ONLY_GIT:
        return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    return None


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=git_env(args))
    if check and proc.returncode != 0:
        raise OrchError(f"git {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc.stdout


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=git_env(args)).returncode == 0


def git_commit_paths(root: Path, paths: list[str], message: str) -> None:
    """지정 경로만 커밋한다(--only). orch 기록 커밋이라 프로젝트 git 훅은 건너뛴다(--no-verify).

    다른 워커와 index.lock 이 겹치면 지터를 둔 지수 대기로 약 30초까지 다시 한다."""
    deadline = time.monotonic() + COMMIT_LOCK_BUDGET
    delay = 0.1
    while True:
        add = subprocess.run(["git", "-C", str(root), "add", "--", *paths], capture_output=True, text=True)
        err = add.stderr
        if add.returncode == 0:
            commit = subprocess.run(["git", "-C", str(root), "commit", "-q", "--no-verify", "-m", message,
                                     "--only", "--", *paths], capture_output=True, text=True)
            if commit.returncode == 0:
                return
            err = commit.stderr + commit.stdout
        remaining = deadline - time.monotonic()
        if "index.lock" not in err or remaining <= 0:
            raise OrchError(f"커밋 실패({', '.join(paths)}): {err.strip()}")
        time.sleep(min(delay * random.uniform(0.5, 1.5), remaining))
        delay = min(delay * 2, 2.0)


def process_alive(pid) -> bool:
    """pid 프로세스가 살아 있는가. 양의 정수가 아니면 False(0·음수는 프로세스 그룹을 가리킨다)."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, OverflowError):
        return False


def pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return process_alive(pid)


def drive_alive(p: Project) -> bool:
    """관제(orch drive)가 살아 있는가: pid 가 살아 있고 heartbeat 가 DRIVE_STALE_SECONDS 안이다."""
    if not pid_alive(p.drive_pid):
        return False
    try:
        stamp = p.drive_heartbeat.read_text(encoding="utf-8").strip()
        age = (datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()
    except (OSError, ValueError, TypeError):  # TypeError: offset 없는 시각은 빼기가 안 된다
        return False
    return 0 <= age < DRIVE_STALE_SECONDS


def acquire_pid(path: Path, name: str) -> None:
    """pid 파일을 O_EXCL 로 만든다. 살아 있는 인스턴스가 있으면 exit 3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        if pid_alive(path):
            raise OrchError(f"{name} 가 이미 실행 중이다(pid {path.read_text(encoding='utf-8').strip()})", 3)
        path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n")
        return
    raise OrchError(f"{name} 가 이미 실행 중이다", 3)


def release_pid(path: Path) -> None:
    try:
        if path.read_text(encoding="utf-8").split()[:1] == [str(os.getpid())]:
            path.unlink()
    except OSError:
        pass


def load_run(p: Project) -> dict:
    run = read_json(p.run_json, {})
    return run if isinstance(run, dict) else {}


def save_run(p: Project, run: dict) -> None:
    write_json(p.run_json, run)


def update_run(p: Project, fn) -> dict:
    """run.json 을 잠그고 다시 읽어 fn(run) 으로 고친 뒤 원자적으로 저장한다. 갱신된 dict 를 돌려준다."""
    p.run_json.parent.mkdir(parents=True, exist_ok=True)
    with open(p.run_json.with_name(p.run_json.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run = load_run(p)
        before = json.dumps(run, sort_keys=True)
        fn(run)
        if json.dumps(run, sort_keys=True) != before or not p.run_json.exists():
            save_run(p, run)
        return run


def require_coordinator(p: Project) -> None:
    """코디네이터 터미널에서만 도는 명령을 보호한다. 메인이 Run 에 묶이는 사고를 막는다."""
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle or handle != load_run(p).get("coordinator_handle"):
        raise OrchError("코디네이터 터미널에서만 실행한다(ORCA_TERMINAL_HANDLE 이 run.json 의 coordinator_handle 과 다르다). "
                        "메인은 orch tell 을 쓴다", 2)
