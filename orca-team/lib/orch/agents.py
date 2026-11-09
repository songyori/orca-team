"""고정 에이전트(config.toml [agents.*]). 상태는 state/agents.json 이고 drive 와 그 명령만 쓴다.

에이전트 하나는 Orca 탭 하나다. 왼쪽 작업 pane 은 --keep-open 으로 띄운 Claude 세션이고 태스크마다 같은 세션에
넣는다(--terminal). 오른쪽은 리뷰 보기 pane(orch review-view)이다."""
from __future__ import annotations

import fcntl
import shlex
from contextlib import contextmanager

from . import orca
from .config import Agent, Config
from .paths import OrchError, Project
from .util import now, read_json, write_json

TEMP = "temp"  # 임시 워커(태스크마다 새 워커)
WAIT = "wait"  # 고정 에이전트를 기다린다
TAB_PREFIX = "orca-team "


def load_all(p: Project) -> dict:
    data = read_json(p.agents_json, {})
    return data if isinstance(data, dict) else {}


def entry(p: Project, name: str) -> dict:
    found = load_all(p).get(name)
    return found if isinstance(found, dict) else {}


@contextmanager
def _locked(p: Project):
    p.agents_json.parent.mkdir(parents=True, exist_ok=True)
    with open(p.agents_json.with_name(p.agents_json.name + ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def update(p: Project, name: str, fn) -> dict:
    """잠그고 다시 읽어 fn(entry) 로 고친 뒤 쓴다. 고친 entry 를 돌려준다."""
    with _locked(p):
        data = load_all(p)
        found = data.get(name) if isinstance(data.get(name), dict) else {}
        fn(found)
        data[name] = found
        write_json(p.agents_json, data)
        return found


def working(p: Project, name: str) -> bool:
    """태스크를 하고 있거나 리셋(인수인계) 중이다."""
    e = entry(p, name)
    return bool(e.get("task") or e.get("resetting"))


def place(p: Project, cfg: Config, role: str) -> str:
    """새 태스크를 보낼 곳: 에이전트 이름, TEMP, WAIT. 수정 라운드 대기열이 있거나 리셋을 기다리면 비어 있지 않다."""
    agent = cfg.agent_for(role)
    if agent is None:
        return TEMP
    e = entry(p, agent.name)
    if not (e.get("task") or e.get("resetting") or e.get("queue") or e.get("reset_pending")):
        return agent.name
    if e.get("resetting") or (e.get("reset_pending") and not e.get("task")):
        return WAIT  # 리셋은 짧다. 다음 세션이 이어 받도록 기다린다
    return TEMP if cfg.overflow else WAIT


def _best_effort(args: list[str]) -> dict | None:
    """화면 배치처럼 실패해도 되는 orca 호출. 성공 응답 또는 None. orca 없음(12)만 올린다."""
    try:
        code, data = orca.call(args, check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return None
    return data if code == 0 and data.get("ok") else None


def start(p: Project, agent: Agent, spec: str, title: str) -> tuple[int, dict, bool]:
    """태스크를 고정 에이전트에 넣는다 → (종료 코드, 영수증, 새로 띄웠는가).
    작업 터미널이 살아 있으면 Orca 태스크를 만들어 그 터미널에 넣고, 아니면 --keep-open 으로 새로 띄운다."""
    handle = entry(p, agent.name).get("handle")
    if handle and orca.terminal_alive(handle):
        code, created = orca.call(["orchestration", "task-create", "--spec", spec, "--task-title", title], check=False)
        orca_task = orca.find_key(created, ("taskId",))
        if code != 0 or not created.get("ok") or not orca_task:
            return code or 1, created, False
        code, receipt = orca.call(["orchestration", "worker-start", "--task", str(orca_task), "--terminal", handle,
                                   "--worktree", "current"], check=False)
        return code, receipt, False
    code, receipt = orca.call(["orchestration", "worker-start", "--spec", spec, "--task-title", title, "--keep-open",
                               "--worktree", "current", "--agent", "claude", "--model", agent.launch.model,
                               "--effort", agent.launch.effort], check=False)
    return code, receipt, True


def review_command(p: Project, name: str) -> str:
    return f"{shlex.quote(str(p.orch / 'bin' / 'orch'))} review-view {shlex.quote(name)}"


def make_layout(p: Project, agent: Agent, handle: str) -> dict:
    """작업 pane 오른쪽에 리뷰 보기 pane 을 만들고 두 pane 이름을 붙인다."""
    split = _best_effort(["terminal", "split", "--terminal", handle, "--direction", "vertical",
                          "--command", review_command(p, agent.name)])
    review = orca.find_key(split, ("handle",)) if split else None
    _best_effort(["terminal", "set-pane-title", "--terminal", handle, "--title", f"{agent.title} (Claude)"])
    if not review:
        return {"error": f"리뷰 보기 pane 을 만들지 못했다: {split}"}
    _best_effort(["terminal", "set-pane-title", "--terminal", str(review), "--title", "리뷰 (Codex)"])
    return {"review_handle": review, "tab_id": orca.find_key(split, ("tabId",))}


def record_start(p: Project, agent: Agent, receipt: dict, fresh: bool, task_id: str) -> dict:
    """start 가 성공한 뒤 상태와 화면을 맞춘다. 새로 띄웠으면 리뷰 pane 을 만든다.
    디스패치가 탭 이름을 spec 첫 줄로 바꾸므로 매번 되돌린다."""
    handle = entry(p, agent.name).get("handle")
    layout: dict = {}
    if fresh:
        handle = orca.find_key(receipt, ("terminal", "agentTerminalHandle", "terminalHandle"))
        layout = make_layout(p, agent, str(handle)) if handle else {"error": "영수증에 작업 터미널 handle 이 없다"}
    if handle:
        _best_effort(["terminal", "rename", "--terminal", str(handle), "--title", TAB_PREFIX + agent.title])

    def write(e: dict) -> None:
        e.update(task=task_id, model=agent.launch.model, effort=agent.launch.effort)
        if fresh:
            e.update(handle=handle, review_handle=layout.get("review_handle"), tab_id=layout.get("tab_id"),
                     launched_at=now(), since_reset=0)
            if layout.get("error"):
                e["layout_error"] = layout["error"]
            else:
                e.pop("layout_error", None)

    return update(p, agent.name, write)


def release_task(p: Project, name: str, task_id: str) -> None:
    """worker_done 뒤 에이전트를 비운다. 처리한 태스크 수(since_reset)를 센다."""
    def write(e: dict) -> None:
        if e.get("task") == task_id:
            e["task"] = None
            e["since_reset"] = int(e.get("since_reset") or 0) + 1
    update(p, name, write)


def summary(p: Project, cfg: Config) -> list[dict]:
    """orch status 용 표. Orca 를 부르지 않는다."""
    rows = []
    for name, agent in cfg.agents.items():
        e = entry(p, name)
        rows.append({"name": name, "title": agent.title, "roles": list(agent.roles), "handle": e.get("handle"),
                     "task": e.get("task"), "queue": list(e.get("queue") or []),
                     "reset_pending": bool(e.get("reset_pending")), "resetting": bool(e.get("resetting")),
                     "since_reset": int(e.get("since_reset") or 0)})
    return rows
