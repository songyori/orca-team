"""설치 점검, 프로젝트 스캐폴드, 세팅 모드."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from . import orca
from .ask import open_questions
from .comm import unread
from .config import Config, Launch
from .paths import PLUGIN_ROOT, TEMPLATES, OrchError, Project, codex_bin, codex_runner, git_toplevel
from .tasks import all_states, launch_in_progress, next_action
from .util import load_run, now, pid_alive, record_plugin_root, require_coordinator, update_run, write_json

MARK = "<!-- orca-team -->"
COORDINATOR_PROMPT = ("[orca-team:coordinator] 너는 이 프로젝트의 orca-team 코디네이터다. "
                      ".orchestration/coordinator.md 를 읽고 '시작' 절차부터 따른다.")
SCAFFOLD_FILES = [
    ("config.toml", ".orchestration/config.toml"),
    ("coordinator.md", ".orchestration/coordinator.md"),
    ("design-rules.md", ".orchestration/design-rules.md"),
    ("text-rules.md", ".orchestration/text-rules.md"),
    ("roles/planner.md", ".orchestration/roles/planner.md"),
    ("roles/director.md", ".orchestration/roles/director.md"),
    ("roles/designer.md", ".orchestration/roles/designer.md"),
    ("roles/developer.md", ".orchestration/roles/developer.md"),
    ("roles/tester.md", ".orchestration/roles/tester.md"),
    ("shim.py", ".orchestration/bin/orch"),
]
APPEND_FILES = [("CLAUDE.section.md", "CLAUDE.md"), ("AGENTS.md", "AGENTS.md")]
LAUNCH_GRACE_SECONDS = 180
STARTING_MESSAGE = (f"코디네이터가 아직 시작 중이다({LAUNCH_GRACE_SECONDS}초 안에 다시 띄우지 않는다). "
                    "`orch status` 로 확인한다.")
GITIGNORE_LINE = ".orchestration/state/"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def doctor(start: Path) -> list[Check]:
    checks = [Check("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0])]
    top = git_toplevel(start)
    checks.append(Check("git 저장소", top is not None, str(top) if top else "없음(init 이 git init 한다)", required=False))
    try:
        _, data = orca.call(["status"], check=False)
        state = ((data.get("result") or {}).get("runtime") or {}).get("state")
        checks.append(Check("orca runtime", state == "ready", f"runtime.state={state}"))
    except OrchError as exc:
        checks.append(Check("orca runtime", False, str(exc)))
    codex = shutil.which(codex_bin())
    if codex:
        proc = subprocess.run([codex, "login", "status"], capture_output=True, text=True)
        out = (proc.stdout + proc.stderr).strip()
        checks.append(Check("codex 로그인", "Logged in" in out, out.splitlines()[0] if out else f"exit {proc.returncode}"))
        checks.append(image_generation_check(codex))
    else:
        checks.append(Check("codex 로그인", False, "codex 없음(brew install codex)"))
    checks.append(wrapper_check())
    return checks


def image_generation_check(codex: str) -> Check:
    """`codex features list` 의 image_generation 이 true 인가. 꺼져 있으면 /orca-team:image 만 막히므로 경고다."""
    proc = subprocess.run([codex, "features", "list"], capture_output=True, text=True)
    row = next((line.split() for line in proc.stdout.splitlines() if line.split()[:1] == ["image_generation"]), None)
    if row and row[-1] == "true":
        return Check("codex image_generation", True, " ".join(row), required=False)
    found = " ".join(row) if row else f"항목 없음(exit {proc.returncode})"
    return Check("codex image_generation", False, f"{found}. codex features enable image_generation 을 실행한다. "
                 "/orca-team:image 만 막힌다", required=False)


def wrapper_check() -> Check:
    """플러그인에 들어 있는 Codex 래퍼가 있고 실행 가능하며 --bypass-hook-trust 를 지원하는가."""
    try:
        runner = codex_runner()
    except OrchError as exc:
        return Check("Codex 래퍼", False, str(exc))
    if not os.access(runner, os.X_OK):
        return Check("Codex 래퍼", False, f"{runner} — 실행 권한이 없다. 플러그인 설치가 깨졌다. orca-team 을 다시 설치한다")
    try:
        supported = "--bypass-hook-trust" in runner.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Check("Codex 래퍼", False, f"{runner} — 읽지 못했다: {exc}")
    if not supported:
        return Check("Codex 래퍼", False, f"{runner} — --bypass-hook-trust 를 지원하지 않는다. 플러그인 설치가 깨졌다")
    return Check("Codex 래퍼", True, str(runner))


def scaffold(root: Path, description: str) -> dict:
    if not description.strip():
        raise OrchError("프로젝트 설명이 비었다", 2)
    created, skipped = [], []
    for src, dst in SCAFFOLD_FILES:
        target = root / dst
        if target.exists():
            skipped.append(dst)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES / src, target)
        created.append(dst)
    (root / ".orchestration/bin/orch").chmod(0o755)
    project_md = root / ".orchestration/project.md"
    if project_md.exists():
        skipped.append(".orchestration/project.md")
    else:
        project_md.write_text(f"# 프로젝트 설명\n\n{description.strip()}\n", encoding="utf-8")
        created.append(".orchestration/project.md")
    keep = root / ".orchestration/tasks/.gitkeep"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.touch()
    for src, dst in APPEND_FILES:
        target = root / dst
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if MARK in current:
            skipped.append(dst)
            continue
        section = (TEMPLATES / src).read_text(encoding="utf-8")
        target.write_text((current.rstrip() + "\n\n" if current.strip() else "") + section, encoding="utf-8")
        created.append(dst)
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if GITIGNORE_LINE not in text.splitlines():
        gitignore.write_text(text + ("\n" if text and not text.endswith("\n") else "") + GITIGNORE_LINE + "\n",
                             encoding="utf-8")
        created.append(".gitignore")
    p = Project(root)
    p.ensure_state()
    p.setup_mode.touch()
    record_plugin_root(p.state, PLUGIN_ROOT)  # Claude Code 가 ${CLAUDE_PLUGIN_ROOT} 로 실제 실행한 코드를 shim 이 따라가게 한다
    return {"root": str(root), "created": created, "skipped": skipped}


def setup_done(p: Project) -> dict:
    p.setup_mode.unlink(missing_ok=True)
    return {"setup_mode": False}


def launch_age(run: dict) -> float | None:
    """마지막 기동 시도 후 경과 초. 기록이 없거나 읽을 수 없으면 None."""
    try:
        return (datetime.now().astimezone() - datetime.fromisoformat(run["launched_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def starting(run: dict) -> bool:
    age = launch_age(run)
    return age is not None and 0 <= age < LAUNCH_GRACE_SECONDS


def launch_handle(receipt: dict):
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    terminal = result.get("terminal") if isinstance(result.get("terminal"), dict) else {}
    for found in (terminal.get("handle"), result.get("terminalHandle"),
                  result.get("agentTerminalHandle"), result.get("handle")):
        if isinstance(found, str) and found:
            return found
    return orca.find_key(receipt, ("terminalHandle", "agentTerminalHandle", "handle"))


def up(p: Project, cfg: Config, wait_run: int = 0) -> dict:
    """코디네이터가 살아 있으면 그대로 두고, 없으면 고정 플래그로 띄운다(메인이 호출)."""
    handle = load_run(p).get("coordinator_handle")
    alive = orca.terminal_alive(handle)
    if alive:
        update_run(p, lambda r: r.pop("launched_at", None))
        result = {"status": "running", "handle": handle}
    elif starting(load_run(p)):
        result = {"status": "starting", "handle": load_run(p).get("coordinator_handle"), "message": STARTING_MESSAGE}
    else:
        result = launch_coordinator(p, cfg.coordinator(), handle)
    deadline = time.monotonic() + wait_run
    while not load_run(p).get("run_id") and time.monotonic() < deadline:
        time.sleep(1)
    result["run_id"] = load_run(p).get("run_id")
    if wait_run and not result["run_id"]:
        raise OrchError(f"코디네이터가 {wait_run}초 안에 Run 을 만들지 않았다. "
                         + (f"코디네이터 터미널({result['handle']})을 확인한다" if result["handle"]
                            else "`orch status` 로 코디네이터를 확인한다"))
    return result


def launch_coordinator(p: Project, launch: Launch, checked_handle: str | None = None) -> dict:
    """run.json 잠금 안에서 기동을 선점한 뒤 코디네이터를 띄운다. 선점은 시작 유예를 잠금 안에서 다시 확인해
    동시에 돈 `orch up` 이 코디네이터를 둘 띄우지 않게 한다. 선점에 지면 띄우지 않고 starting 을 돌려준다."""
    p.ensure_state()
    receipt_path = p.state / "coordinator-launch.json"
    launched_at = now()
    claimed = []

    def claim(r: dict) -> None:
        if starting(r) or r.get("coordinator_handle") != checked_handle:
            return
        r.pop("coordinator_handle", None)
        r.update(launched_at=launched_at)
        claimed.append(True)

    run = update_run(p, claim)
    if not claimed:
        return {"status": "starting", "handle": run.get("coordinator_handle"), "message": STARTING_MESSAGE}
    try:
        code, receipt = orca.call(["agent", "launch", "--agent", "claude", "--surface", "terminal",
                                   "--worktree", f"path:{p.root}", "--model", launch.model,
                                   "--effort", launch.effort, "--prompt", COORDINATOR_PROMPT], check=False)
    except OrchError as exc:
        if exc.code != 12:
            write_json(receipt_path, {"ok": False, "parse_error": str(exc), "launched_at": launched_at})
        raise
    write_json(receipt_path, receipt)
    handle = launch_handle(receipt)
    if code != 0 or not receipt.get("ok"):
        update_run(p, lambda r: r.pop("launched_at", None))
        raise OrchError(f"코디네이터 실행 실패. 영수증: {receipt_path}")
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    if handle and handle == own:
        # 영수증 깊은 곳의 호출자(메인) 핸들을 코디네이터로 기록하면 메인이 코디네이터 권한을 얻는다.
        raise OrchError(f"코디네이터 실행 결과가 불확실하다(영수증에서 찾은 핸들 {handle} 이 메인 자신의 터미널이다). "
                        f"다시 띄우기 전에 영수증과 `orch status` 를 확인한다: {receipt_path}")
    if not handle:
        raise OrchError(f"코디네이터 실행 결과가 불확실하다(핸들 없음). 다시 띄우기 전에 영수증과 `orch status` 를 확인한다: {receipt_path}")
    update_run(p, lambda r: r.update(coordinator_handle=handle))
    return {"status": "launched", "handle": handle}


def project_objective(p: Project) -> str:
    path = p.orch / "project.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return (lines[0] if lines else p.root.name)[:120]


def bind(p: Project) -> dict:
    """코디네이터 터미널을 Run 에 묶는다. 처음이면 만들고, 있으면 다시 붙는다."""
    require_coordinator(p)
    run = load_run(p)
    if run.get("run_id"):
        orca.call(["orchestration", "run-use", "--id", run["run_id"]])
        return {"status": "rebound", "run_id": run["run_id"]}
    _, data = orca.call(["orchestration", "run-create", "--objective", project_objective(p)])
    result = data.get("result") or {}
    run_id = result.get("runId") or (result.get("run") or {}).get("id") or orca.find_key(result, ("runId",))
    if not run_id:
        raise OrchError(f"run-create 응답에서 Run ID 를 찾지 못했다: {data}")
    update_run(p, lambda r: r.update(run_id=run_id))
    return {"status": "created", "run_id": run_id}


def status(p: Project, cfg: Config | None, config_error: str | None = None) -> dict:
    """현황. 태스크마다 다음 행동(next)을 붙인다. 코디네이터가 살아 있으면 run.json 의 launched_at(기동 유예 표시)을 지운다.
    open_questions 는 메인이 아직 답하지 않은 질문이다(재시작한 메인은 orch reask 로 다시 보여 준다).

    config.toml 이 깨져도(cfg None) 복구 안내는 나와야 하므로 config_error 를 싣고 최대 라운드 검사 없이 next 를 만든다."""
    handle = load_run(p).get("coordinator_handle")
    try:
        alive = orca.terminal_alive(handle)
    except OrchError:
        alive = None
    if alive:
        run = update_run(p, lambda r: r.pop("launched_at", None))
    else:
        run = load_run(p)
    return {"run_id": run.get("run_id"),
            "coordinator": {"handle": run.get("coordinator_handle"), "alive": alive,
                            "starting": alive is False and starting(run)},
            "watch_alive": pid_alive(p.watch_pid), "unread_outbox": len(unread(p)),
            "open_questions": open_questions(p),
            "setup_mode": p.setup_mode.exists(), "config_error": config_error,
            "tasks": [task_row(p, cfg, st) for st in all_states(p)]}


def task_row(p: Project, cfg: Config | None, st: dict) -> dict:
    """status 의 태스크 한 줄. launch 가 아직 Orca 를 부르는 중이면 status(launch_failed)는 그대로 두고 launching 을 더한다."""
    row = {**{k: st.get(k) for k in ("id", "role", "title", "status", "stage", "round")}, "next": next_action(p, cfg, st)}
    if launch_in_progress(st):
        row["launching"] = True
    return row
