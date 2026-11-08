"""테스트 관문. docs/planning/tasks.toml 의 [test] 명령을 orch 가 직접 돌리고 결과를 남긴다.

quick 은 태스크가 바꾼 파일과 관련된 테스트, full 은 전체 테스트다. 결과는 state/tests/ 에, 출력 전체는
~/.cache/orca-team/runs/<프로젝트>/ 의 로그에 남긴다. [test] 가 없으면 관문이 꺼진다."""
from __future__ import annotations

import fcntl
import os
import shlex
import signal
import subprocess
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .globs import matches_any
from .paths import OrchError, Project
from .util import git, git_ok, now, read_json, write_json

PLAN_FILE = "docs/planning/tasks.toml"  # plan.PLAN_FILE 과 같다(plan 이 이 모듈을 import 한다)
KEYS = ("quick", "full", "timeout_min", "ok_exit", "exclusive")
DEFAULT_TIMEOUT_MIN = 30
DEVELOPER_ROLE = "developer"
TESTER_ROLE = "tester"
GATED_ROLES = (DEVELOPER_ROLE, TESTER_ROLE)
PASSED = ("passed", "flaky", "skipped")
TAIL_LINES = 60
TAIL_WIDTH = 300
TAIL_BYTES = 65536
KILL_GRACE_SEC = 5.0  # 시간을 넘긴 명령에 SIGTERM 을 보낸 뒤 SIGKILL 까지 기다리는 초


@dataclass(frozen=True)
class GateConfig:
    quick: str
    full: str
    timeout_min: int = DEFAULT_TIMEOUT_MIN
    ok_exit: tuple[int, ...] = (0,)
    exclusive: bool = False


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def config_problems(raw) -> list[str]:
    """[test] 표의 형식 오류. 없으면 빈 목록."""
    if not isinstance(raw, dict):
        return ["[test] 는 표여야 한다"]
    problems = []
    unknown = sorted(set(raw) - set(KEYS))
    if unknown:
        problems.append(f"[test]: 모르는 키 {', '.join(unknown)}")
    for name in ("quick", "full"):
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"[test]: {name} 는 비지 않은 명령 문자열이어야 한다")
    quick, full = raw.get("quick"), raw.get("full")
    if isinstance(quick, str) and quick.strip() and quick.count("{files}") != 1:
        problems.append("[test]: quick 은 {files} 를 정확히 한 번 담는다")
    if isinstance(full, str) and "{files}" in full:
        problems.append("[test]: full 은 {files} 를 담지 않는다")
    timeout = raw.get("timeout_min", DEFAULT_TIMEOUT_MIN)
    if not _int(timeout) or not 1 <= timeout <= 240:
        problems.append("[test]: timeout_min 은 1–240 정수")
    ok_exit = raw.get("ok_exit", [0])
    if not isinstance(ok_exit, list) or not ok_exit or not all(_int(x) for x in ok_exit):
        problems.append("[test]: ok_exit 는 정수 목록")
    if not isinstance(raw.get("exclusive", False), bool):
        problems.append("[test]: exclusive 는 true 또는 false")
    return problems


def parse_config(raw) -> GateConfig:
    problems = config_problems(raw)
    if problems:
        raise OrchError(f"{PLAN_FILE} [test] 검증 실패: " + "; ".join(problems), 2)
    return GateConfig(raw["quick"].strip(), raw["full"].strip(), raw.get("timeout_min", DEFAULT_TIMEOUT_MIN),
                      tuple(raw.get("ok_exit", [0])), raw.get("exclusive", False))


def load_config(p: Project) -> GateConfig | None:
    """커밋된 HEAD 의 tasks.toml [test]. 작업 트리에서 고치는 중인 tasks.toml(증분 기획)이 관문을 바꾸지 않게 한다.
    HEAD 에 파일이 없거나(커밋이 없을 때 포함) 표가 없으면 None(관문 꺼짐). 읽지 못하거나 형식이 틀리면 exit 2."""
    if not git_ok(p.root, "cat-file", "-e", f"HEAD:{PLAN_FILE}"):
        return None
    try:
        data = tomllib.loads(git(p.root, "show", f"HEAD:{PLAN_FILE}"))
    except (OrchError, ValueError) as exc:  # TOMLDecodeError 는 ValueError 하위
        raise OrchError(f"{PLAN_FILE} 읽기 실패: {exc}", 2) from exc
    if "test" not in data:
        return None
    return parse_config(data["test"])


def require_config(p: Project) -> GateConfig:
    cfg = load_config(p)
    if cfg is None:
        raise OrchError(f"{PLAN_FILE} 에 [test] 가 없다. 테스트 관문이 꺼져 있다", 2)
    return cfg


def tests_dir(p: Project) -> Path:
    return p.state / "tests"


def record_path(p: Project, name: str) -> Path:
    """name: 태스크 ID 또는 "final"."""
    return tests_dir(p) / f"{name}.json"


def load_records(p: Project, name: str) -> dict:
    data = read_json(record_path(p, name), {})
    return data if isinstance(data, dict) else {}


def save_record(p: Project, name: str, kind: str, entry: dict) -> None:
    data = load_records(p, name)
    data[kind] = entry
    write_json(record_path(p, name), data)


def log_path(p: Project, name: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / "orca-team" / "runs" / p.root.name / f"{name}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_tail(path: Path) -> str:
    """로그 마지막 TAIL_LINES 줄. 빈 줄은 빼고 줄마다 TAIL_WIDTH 자로 자른다."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return ""
    lines = [line[:TAIL_WIDTH] for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return "\n".join(lines[-TAIL_LINES:])


def kill_group(proc: subprocess.Popen) -> None:
    """시간을 넘긴 명령의 프로세스 그룹을 끝낸다. SIGTERM 을 보내고 그룹이 사라지기를 KILL_GRACE_SEC 초까지 기다린 뒤,
    남았든 아니든 SIGKILL 을 보낸다. sh 가 먼저 끝나도 SIGTERM 을 무시한 그룹 프로세스(브라우저·e2e 도우미 등)가 남지 않게 한다."""
    # macOS 는 좀비만 남은 그룹에 EPERM(PermissionError)을 돌려준다. 같은 사용자 프로세스 그룹이라 사라진 것으로 본다.
    gone = (ProcessLookupError, PermissionError)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except gone:
        pass
    deadline = time.monotonic() + KILL_GRACE_SEC
    while time.monotonic() < deadline:
        proc.poll()  # 끝난 sh 를 거둬야 그룹이 비었는지 알 수 있다
        try:
            os.killpg(proc.pid, 0)
        except gone:
            break
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except gone:
        pass
    proc.wait()


def execute(root: Path, command: str, log: Path, timeout_sec: float, ok_exit: tuple[int, ...]) -> dict:
    """명령 하나를 /bin/sh 로 돌리고 출력을 log 에 쓴다. status: passed|failed|timeout|unrunnable(종료 코드 126·127)."""
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as fh:
        fh.write(f"$ {command}\n")
        fh.flush()
        try:
            proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=root, stdin=subprocess.DEVNULL, stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            raise OrchError(f"[test] 명령을 실행하지 못했다: {exc}", 2) from exc
        try:
            code = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            code = None
    seconds = round(time.monotonic() - started, 1)
    if code is None:
        status = "timeout"
    elif code in (126, 127):
        status = "unrunnable"
    else:
        status = "passed" if code in ok_exit else "failed"
    return {"status": status, "exit": code, "seconds": seconds, "tail": read_tail(log)}


@contextmanager
def run_lock(p: Project, cfg: GateConfig, wait_sec: float):
    """exclusive 면 state/tests/.lock 을 잡는다. wait_sec 안에 못 잡으면 exit 2."""
    if not cfg.exclusive:
        yield
        return
    path = tests_dir(p) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        deadline = time.monotonic() + wait_sec
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OrchError(f"테스트 잠금(exclusive)을 {wait_sec:g}초 안에 얻지 못했다. 다른 테스트 실행이 "
                                    "끝난 뒤 다시 한다", 2) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def attempt(p: Project, cfg: GateConfig, command: str, name: str, timeout_sec: float | None = None,
            lock_wait: float | None = None) -> dict:
    """명령 한 번. 시간 상한과 잠금 대기는 기본 timeout_min 분이다."""
    limit = cfg.timeout_min * 60
    log = log_path(p, name)
    with run_lock(p, cfg, limit if lock_wait is None else lock_wait):
        result = execute(p.root, command, log, limit if timeout_sec is None else timeout_sec, cfg.ok_exit)
    return {"command": command, "log": str(log), **result}


def run_quick(p: Project, cfg: GateConfig, files: list[str], name: str) -> dict:
    """관련 테스트. 대상 파일이 없으면 skipped. 실패하면 한 번 더 돌려 통과하면 flaky."""
    if not files:
        return {"status": "skipped", "exit": None, "seconds": 0.0, "command": None, "log": None, "tail": "",
                "files": []}
    command = cfg.quick.replace("{files}", " ".join(shlex.quote(f) for f in files))
    first = attempt(p, cfg, command, f"{name}-quick-a1")
    if first["status"] != "failed":
        return {**first, "files": files}
    second = attempt(p, cfg, command, f"{name}-quick-a2")
    if second["status"] == "passed":
        return {**second, "status": "flaky", "files": files, "first": {"exit": first["exit"], "log": first["log"]}}
    return {**second, "files": files}


def unrunnable(result: dict) -> OrchError:
    return OrchError(f"[test] 명령을 실행할 수 없다(exit {result['exit']}, 명령: {result['command']}). "
                     f"기획 태스크로 {PLAN_FILE} 의 [test] 를 고치거나 사용자가 직접 고친다. "
                     f"출력 끝: {result['tail'][-300:]}", 2)


def task_target(p: Project, task_id: str) -> tuple[dict, list[tuple[str, str]]]:
    """(태스크 상태, 작업 커밋). review·tasks 가 이 모듈을 import 하므로 실행 시점에 읽는다."""
    from .review import task_commits
    from .tasks import TASK_ID_RE, must_state

    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = must_state(p, task_id)
    if not st.get("base"):
        raise OrchError(f"{task_id}: 아직 시작하지 않은 태스크다(base 없음)", 2)
    return st, task_commits(p.root, task_id, st["base"])


def code_head(p: Project, task_id: str, commits: list[tuple[str, str]]) -> str | None:
    """리포트(docs/tasks/T-xxx/)만 바꾼 커밋을 뺀 마지막 작업 커밋. 테스트 결과를 다시 쓸지 가르는 기준이다.
    워커는 테스트를 돌린 뒤 리포트를 커밋하므로 리포트 커밋이 재사용을 막지 않게 한다."""
    from .review import commit_paths

    report = f"docs/tasks/{task_id}/"
    for sha, _ in reversed(commits):
        if any(not f.startswith(report) for f in commit_paths(p.root, sha)):
            return sha
    return None


def quick_files(p: Project, task_id: str, commits: list[tuple[str, str]]) -> list[str]:
    """작업 커밋이 바꾼 파일 중 작업 트리에 있는 것. 리포트 경로는 뺀다."""
    from .review import changed_files

    report = f"docs/tasks/{task_id}/"
    return [f for f in changed_files(p.root, [sha for sha, _ in commits])
            if not f.startswith(report) and (p.root / f).is_file()]


def dirty_files(p: Project, allowed: list[str]) -> list[str]:
    """allowed 밖에서 커밋되지 않은 추적 파일 변경(실행 직후). 다른 워커의 작업 중 파일 탓인 실패를 가르는 데 쓴다."""
    from .review import worktree_status

    tracked, _ = worktree_status(p.root, untracked=False)
    return sorted(path for path in tracked if not matches_any(path, allowed))


def unsaved_owned(p: Project, task_id: str, owns: list[str]) -> list[str]:
    """소유 경로 안에서 커밋하지 않은 추적 파일 변경과 추적 안 된 새 파일. 리포트 경로(docs/tasks/T-xxx/)는 뺀다."""
    from .review import worktree_status

    tracked, new = worktree_status(p.root)
    report = f"docs/tasks/{task_id}/"
    return sorted(path for path in set(tracked) | new if matches_any(path, owns) and not path.startswith(report))


def check_committed(p: Project, task_id: str, st: dict, commits: list[tuple[str, str]]) -> None:
    """quick 은 커밋된 변경만 본다. 커밋 전에 돌린 quick 이 통과로 보이지 않게 막는다(exit 2)."""
    if not commits:
        raise OrchError(f"아직 `[{task_id}]` 작업 커밋이 없다. 작업을 먼저 커밋한 뒤 orch test 를 돌린다"
                        "(quick 은 커밋된 변경만 본다)", 2)
    pending = unsaved_owned(p, task_id, st.get("owns", []))
    if pending:
        raise OrchError(f"{task_id}: 소유 경로에 커밋하지 않은 변경이 있다: {', '.join(pending)}. quick 은 커밋된 변경만 "
                        "본다. 워커는 커밋하거나 지운 뒤 orch test 를 다시 돌린다. 리뷰에서 나왔으면 "
                        f"`orch fix {task_id} --note \"커밋 안 된 파일을 커밋하거나 지운다\"` 로 되돌려 보낸다", 2)


def run_task(p: Project, task_id: str, kind: str) -> dict:
    """태스크 하나의 quick 또는 full. 결과를 state/tests/T-xxx.json 에 남긴다. 실행 불가면 기록 없이 exit 2."""
    cfg = require_config(p)
    st, commits = task_target(p, task_id)
    rnd = st.get("round", 1)
    name = f"{task_id}-r{rnd}"
    if kind == "quick":
        check_committed(p, task_id, st, commits)
        result = run_quick(p, cfg, quick_files(p, task_id, commits), name)
    else:
        result = attempt(p, cfg, cfg.full, f"{name}-full")
    if result["status"] == "unrunnable":
        raise unrunnable(result)
    entry = {"round": rnd, "head": code_head(p, task_id, commits), **result,
             "dirty": dirty_files(p, [*st.get("owns", []), f"docs/tasks/{task_id}/**"]), "at": now()}
    save_record(p, task_id, kind, entry)
    return {"task": task_id, "kind": kind, **entry}


def run_final(p: Project) -> dict:
    """태스크 없는 full(마지막 확인). state/tests/final.json 에 남긴다."""
    cfg = require_config(p)
    result = attempt(p, cfg, cfg.full, "final-full")
    if result["status"] == "unrunnable":
        raise unrunnable(result)
    entry = {"head": git(p.root, "rev-parse", "HEAD").strip(), **result, "dirty": dirty_files(p, []), "at": now()}
    save_record(p, "final", "full", entry)
    return {"task": None, "kind": "full", **entry}


def exit_code(result: dict) -> int:
    return 0 if result["status"] in PASSED else 1


def gate_results(p: Project, task_id: str, role: str, rnd: int, head: str | None) -> dict | None:
    """리뷰 관문 결과. [test] 가 없거나 관문 직무가 아니면 None. 실행 불가면 exit 2.

    developer 는 {"quick": 기록}. 같은 head(code_head)의 통과 기록을 다시 쓴다.
    tester 는 {"full": 기록}. 같은 라운드 기록을 다시 쓰고, 없으면 한 번 돌린다. quick 은 돌리지 않는다. 묶음 통합 테스트는
    제품 회귀를 잡는 것이 일이라 테스트 결과로 자동 반려하지 않고, 묶음 리뷰(Codex)가 실패를 테스트 잘못과 제품 회귀로 나눈다."""
    if role not in GATED_ROLES or load_config(p) is None:
        return None
    records = load_records(p, task_id)
    if role == TESTER_ROLE:
        full = records.get("full")
        if not (isinstance(full, dict) and full.get("round") == rnd):
            full = run_task(p, task_id, "full")
        return {"full": full}
    quick = records.get("quick")
    if not (isinstance(quick, dict) and quick.get("head") == head and quick.get("status") in PASSED):
        quick = run_task(p, task_id, "quick")
    return {"quick": quick}
