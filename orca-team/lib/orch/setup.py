"""설치 점검, 프로젝트 스캐폴드, 세팅 모드."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from . import agents, orca
from .ask import open_questions
from .comm import unread
from .config import Config
from .paths import PLUGIN_ROOT, TEMPLATES, OrchError, Project, codex_bin, codex_runner, git_toplevel
from .tasks import all_states, launch_in_progress, next_action
from .util import drive_alive, git, git_ok, load_run, now, pid_alive, record_plugin_root, require_drive, update_run, write_json

MARK = "<!-- orca-team -->"
SCAFFOLD_FILES = [
    ("config.toml", ".orchestration/config.toml"),
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
STARTING_MESSAGE = (f"관제가 아직 시작 중이다({LAUNCH_GRACE_SECONDS}초 안에 다시 띄우지 않는다). "
                    "`orch status` 로 확인한다.")
DRIVE_TITLE = "orca-team 관제"
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


UPGRADE_KEEP = (".orchestration/config.toml",)  # 사용자 설정이라 덮어쓰지 않는다
UPGRADE_NEXT = ("바뀐 파일을 확인하고(git diff -- .orchestration CLAUDE.md) 커밋한다. 그다음 메인에서 /orca-team:up 으로 "
                "관제를 띄운다. 테스트 관문을 쓰려면 docs/planning/tasks.toml 에 [test] 를 넣는다(README \"묶음 검증\").")
# 0.6.0 템플릿의 codex_effort. 이 값 그대로인 표만 0.7.0 값(xhigh)으로 올린다(사용자가 바꾼 값은 둔다).
OLD_CODEX_EFFORT = {"defaults": "high", "roles.planner": "high", "roles.director": "high", "roles.designer": "medium",
                    "roles.developer": "xhigh", "roles.tester": "high"}


def editable_reason(p: Project, rel: str) -> str | None:
    """git 에 추적되고 커밋 안 된 변경이 없으면 None(고쳐도 된다), 아니면 건너뛸 이유."""
    if not git_ok(p.root, "ls-files", "--error-unmatch", "--", rel):
        return "git 에 추적되지 않는다. 템플릿과 직접 비교해 바꾼다"
    if git(p.root, "status", "--porcelain", "--", rel).strip():
        return "커밋 안 된 변경이 있다. 커밋하거나 되돌린 뒤 다시 한다"
    return None


def table_re(header: str) -> re.Pattern:
    """표 하나(헤더 줄부터 다음 표 직전까지). 다음 표 바로 위의 빈 줄·주석 줄은 그 표의 것이라 뺀다."""
    return re.compile(rf"^\[{re.escape(header)}\][ \t]*(?:#[^\n]*)?\n.*?(?=^(?:[ \t]*(?:#[^\n]*)?\n)*(?:\[|\Z))",
                      re.M | re.S)


def refuse_busy(p: Project) -> None:
    busy = [f"{st['id']}({st.get('status')})" for st in all_states(p) if st.get("status") in ("working", "reviewing")]
    if busy:
        raise OrchError("진행 중인 태스크가 있다: " + ", ".join(busy) + ". 끝나기를 기다린 뒤 다시 한다"
                        "(옛 Run 의 메일은 새 관제로 넘어가지 않는다)", 2)
    handle = load_run(p).get("coordinator_handle")
    if handle and orca.terminal_state(handle) == "alive":
        raise OrchError(f"코디네이터가 아직 떠 있다({handle}). 코디네이터 탭을 닫은 뒤 다시 한다", 2)


def migrate_config(p: Project) -> dict:
    """[coordinator] → [drive], [overflow]·[agents] 추가, 0.6.0 기본 codex_effort → xhigh. 글자 단위로 고쳐 주석을 지킨다.
    0.7.0 설정(drive 가 있고 coordinator 가 없다)은 사용자가 정한 값이라 건드리지 않는다."""
    rel = ".orchestration/config.toml"
    reason = editable_reason(p, rel)
    if reason:
        return {"skipped": reason}
    path = p.root / rel
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    if "coordinator" not in data and "drive" in data:
        return {"changed": []}
    changed = []
    auto_up = True
    if "coordinator" in data:
        auto_up = data["coordinator"].get("auto_up", True) is not False
        text, n = table_re("coordinator").subn("", text)
        if not n or "coordinator" in tomllib.loads(text):
            return {"skipped": "[coordinator] 를 글자로 지울 수 없다. 직접 지운 뒤 다시 한다"}
        changed.append("[coordinator] 지움")
    for header, old in OLD_CODEX_EFFORT.items():
        table = data.get("defaults", {}) if header == "defaults" else data.get("roles", {}).get(header[6:], {})
        if table.get("codex_effort") != old:
            continue
        done = []

        def bump(m):
            body, n = re.subn(r'(?m)^codex_effort\s*=\s*"[^"]*"', 'codex_effort = "xhigh"', m.group(0))
            done.append(n)
            return body
        text = table_re(header).sub(bump, text)
        if sum(done):
            changed.append(f"[{header}].codex_effort {old} → xhigh")
    text = text.rstrip() + "\n"
    if "drive" not in data:
        text += f"\n[drive]\nauto_up = {'true' if auto_up else 'false'}\n"
        changed.append("[drive]")
    if "overflow" not in data:
        text += "\n[overflow]\nenabled = true\n"
        changed.append("[overflow]")
    if "agents" not in data:
        roles = set(data.get("roles", {}))
        for name, agent in tomllib.loads((TEMPLATES / "config.toml").read_text(encoding="utf-8"))["agents"].items():
            keep = [r for r in agent["roles"] if r in roles]
            if not keep:
                continue
            text += (f'\n[agents.{name}]\ntitle = "{agent["title"]}"\nroles = {json.dumps(keep, ensure_ascii=False)}\n'
                     f'claude_model = "{agent["claude_model"]}"\nclaude_effort = "{agent["claude_effort"]}"\n')
            changed.append(f"[agents.{name}]")
    tomllib.loads(text)  # 고친 결과도 TOML 이어야 한다
    if changed:
        path.write_text(text, encoding="utf-8")
    return {"changed": changed}


def replace_claude_section(p: Project) -> dict:
    """CLAUDE.md 의 orca-team 절(<!-- orca-team --> 부터 끝까지, scaffold 가 끝에 붙인다)을 새 템플릿으로 바꾼다."""
    path = p.root / "CLAUDE.md"
    if not path.exists():
        return {"skipped": "CLAUDE.md 가 없다"}
    reason = editable_reason(p, "CLAUDE.md")
    if reason:
        return {"skipped": reason}
    text = path.read_text(encoding="utf-8")
    start = text.find(MARK)
    if start < 0:
        return {"skipped": f"orca-team 절 표시({MARK})가 없다"}
    new = text[:start] + (TEMPLATES / "CLAUDE.section.md").read_text(encoding="utf-8")
    if new == text:
        return {"updated": False}
    path.write_text(new, encoding="utf-8")
    return {"updated": True}


def upgrade(p: Project) -> dict:
    """프로젝트를 이 플러그인 버전으로 맞춘다. 복사된 규칙 파일(config.toml 제외)을 템플릿으로 바꾸고, 0.7.0 이전 프로젝트면
    코디네이터 문서를 지우고 config.toml·CLAUDE.md 를 옮긴다. git 에 커밋돼 있고 커밋 안 된 변경이 없는 파일만 고친다.
    커밋하지 않는다. 진행 중인 태스크가 있거나 코디네이터가 떠 있으면 거부한다."""
    refuse_busy(p)
    updated, created, skipped, removed = [], [], [], []
    for src, dst in [(s, d) for s, d in SCAFFOLD_FILES if d not in UPGRADE_KEEP]:
        target = p.root / dst
        new = (TEMPLATES / src).read_bytes()
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new)
            created.append(dst)
            continue
        reason = editable_reason(p, dst)
        if reason:
            skipped.append({"path": dst, "reason": reason})
        elif target.read_bytes() != new:
            target.write_bytes(new)
            updated.append(dst)
    legacy = ".orchestration/coordinator.md"
    if (p.root / legacy).exists():
        reason = editable_reason(p, legacy)
        if reason:
            skipped.append({"path": legacy, "reason": reason})
        else:
            (p.root / legacy).unlink()
            removed.append(legacy)
    config = migrate_config(p)
    claude_md = replace_claude_section(p)
    if p.run_json.exists():
        update_run(p, lambda r: (r.pop("coordinator_handle", None), r.pop("launched_at", None)))
    shim = p.root / ".orchestration/bin/orch"
    if shim.exists():
        shim.chmod(0o755)
    changed = [*updated, *created, *removed, *([".orchestration/config.toml"] if config.get("changed") else []),
               *(["CLAUDE.md"] if claude_md.get("updated") else [])]
    stat = git(p.root, "diff", "--stat", "--", *changed, check=False) if changed else ""
    return {"updated": updated, "created": created, "removed": removed, "skipped": skipped, "config": config,
            "claude_md": claude_md, "diff_stat": stat.strip(), "next": UPGRADE_NEXT}


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


def drive_command(p: Project) -> str:
    return f"{shlex.quote(str(p.orch / 'bin' / 'orch'))} drive"


def up(p: Project, cfg: Config, wait_run: int = 0) -> dict:
    """관제가 살아 있으면 그대로 두고, 없으면 관제 탭을 띄운다(메인과 자동 부활이 호출)."""
    run = load_run(p)
    if drive_alive(p):
        update_run(p, lambda r: r.pop("launched_at", None))
        result = {"status": "running", "handle": run.get("drive_handle")}
    elif pid_alive(p.drive_pid):  # 프로세스는 살아 있는데 heartbeat 가 멈췄다. 같은 터미널에 다시 치지 않는다
        result = {"status": "hung", "handle": run.get("drive_handle"),
                  "message": "관제 프로세스가 살아 있지만 응답이 없다. 관제 탭을 확인한다"}
    elif starting(run):
        result = {"status": "starting", "handle": run.get("drive_handle"), "message": STARTING_MESSAGE}
    else:
        result = launch_drive(p)
    deadline = time.monotonic() + wait_run
    while not load_run(p).get("run_id") and time.monotonic() < deadline:
        time.sleep(1)
    result["run_id"] = load_run(p).get("run_id")
    if wait_run and not result["run_id"]:
        raise OrchError(f"관제가 {wait_run}초 안에 Run 을 만들지 않았다. 관제 탭({DRIVE_TITLE})을 확인한다")
    return result


def launch_drive(p: Project) -> dict:
    """run.json 잠금 안에서 기동을 선점한 뒤 관제를 띄운다. 셸만 남은 옛 관제 터미널이 있으면 거기서 다시 실행하고,
    없으면 관제 탭을 새로 만든다. 선점에 지면 띄우지 않고 starting 을 돌려준다."""
    p.ensure_state()
    p.drive_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = p.drive_dir / "launch.json"
    launched_at = now()
    claimed = []

    def claim(r: dict) -> None:
        if drive_alive(p) or starting(r):  # 다른 up 이 이미 띄웠다
            return
        r.update(launched_at=launched_at)
        claimed.append(True)

    run = update_run(p, claim)
    if not claimed:
        if drive_alive(p):
            return {"status": "running", "handle": run.get("drive_handle")}
        return {"status": "starting", "handle": run.get("drive_handle"), "message": STARTING_MESSAGE}
    old = run.get("drive_handle")
    if old and orca.shell_alive(old) == "alive":
        try:
            code, data = orca.call(["terminal", "send", "--terminal", old, "--text", drive_command(p), "--enter"],
                                   check=False)
        except OrchError as exc:
            if exc.code == 12:
                raise
            code, data = 1, {}
        if code == 0 and data.get("ok"):
            return {"status": "relaunched", "handle": old}
    try:
        code, receipt = orca.call(["terminal", "create", "--worktree", f"path:{p.root}", "--title", DRIVE_TITLE,
                                   "--command", drive_command(p)], check=False)
    except OrchError as exc:
        if exc.code != 12:
            write_json(receipt_path, {"ok": False, "parse_error": str(exc), "launched_at": launched_at})
        raise
    write_json(receipt_path, receipt)
    handle = orca.find_key(receipt, ("handle",))
    if code != 0 or not receipt.get("ok") or not handle:
        update_run(p, lambda r: r.pop("launched_at", None))
        raise OrchError(f"관제 탭을 만들지 못했다. 영수증: {receipt_path}")
    if handle == os.environ.get("ORCA_TERMINAL_HANDLE"):
        raise OrchError(f"관제 실행 결과가 불확실하다(영수증의 handle {handle} 이 메인 자신의 터미널이다). "
                        f"다시 띄우기 전에 영수증과 `orch status` 를 확인한다: {receipt_path}")
    update_run(p, lambda r: r.update(drive_handle=handle))
    return {"status": "launched", "handle": handle}


def project_objective(p: Project) -> str:
    path = p.orch / "project.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return (lines[0] if lines else p.root.name)[:120]


def bind(p: Project) -> dict:
    """관제가 Run 을 만들거나 다시 붙는다. 처음이면 만들고, 있으면 다시 붙는다."""
    require_drive(p)
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
    """현황. 태스크마다 다음 행동(next)을 붙인다. 관제가 살아 있으면 run.json 의 launched_at(기동 유예 표시)을 지운다.
    open_questions 는 메인이 아직 답하지 않은 질문이다(재시작한 메인은 orch reask 로 다시 보여 준다).

    config.toml 이 깨져도(cfg None) 복구 안내는 나와야 하므로 config_error 를 싣고 최대 라운드 검사 없이 next 를 만든다."""
    alive = drive_alive(p)
    run = update_run(p, lambda r: r.pop("launched_at", None)) if alive else load_run(p)
    return {"run_id": run.get("run_id"),
            "drive": {"handle": run.get("drive_handle"), "alive": alive, "starting": not alive and starting(run)},
            "agents": agents.summary(p, cfg) if cfg is not None else [],
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
