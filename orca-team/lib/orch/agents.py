"""고정 에이전트(config.toml [agents.*]). 상태는 state/agents.json 이고 drive 와 그 명령만 쓴다.

에이전트 하나는 Orca 탭 하나다. 왼쪽 작업 pane 은 --keep-open 으로 띄운 Claude 세션이고 태스크마다 같은 세션에
넣는다(--terminal). 오른쪽은 리뷰 보기 pane(orch review-view)이다."""
from __future__ import annotations

import fcntl
import shlex
import time
from contextlib import contextmanager
from datetime import datetime

from . import orca
from .config import Agent, Config
from .paths import TEMPLATES, OrchError, Project
from .util import now, read_json, render, write_json

TEMP = "temp"  # 임시 워커(태스크마다 새 워커)
WAIT = "wait"  # 고정 에이전트를 기다린다
TAB_PREFIX = "orca-team "
HANDOFF_DIR = "docs/handoff"
RESET_UNKNOWN_SECONDS = 30 * 60  # 리셋할 작업 터미널의 상태를 이만큼 모르면 리셋 표시를 지우고 메인에 올린다
RESET_SETTLE_SECONDS = 5.0  # /clear 뒤 tui-idle 이 떠도 새 세션 화면이 입력을 받기까지 기다린다(실험 E4)
ACTIVE_OUTPUT_SECONDS = 30  # 작업 터미널에 이만큼 안에 출력이 있었으면 세션이 일하는 중이다(Claude 스피너는 1초 안팎마다 바뀐다)
RESET_FAIL_LIMIT = 3  # 인수인계를 이만큼 넣지 못하면 리셋 표시를 지우고 메인에 올린다(그 전에는 다음 틱에 다시 한다)


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


def terminal_activity(handle: str | None) -> tuple[str, bool]:
    """작업 터미널 → (생존 판정, 일하는 중인가). 일하는 중은 살아 있고 마지막 출력(lastOutputAt)이 ACTIVE_OUTPUT_SECONDS
    안에 있는 것이다. 맡은 태스크 없이 도는 일(자동 압축 "Compacting conversation…")도 일하는 중이다. handle 이 없으면
    dead. lastOutputAt 이 없으면(옛 Orca) 일하는 중으로 보지 않는다."""
    if not handle:
        return "dead", False
    state, last = orca.terminal_status(handle, wait=True)
    if state != "alive" or last is None:
        return state, False
    return state, time.time() * 1000 - last < ACTIVE_OUTPUT_SECONDS * 1000


def close_tab(handle: str) -> dict | str | None:
    """작업 터미널의 탭(작업 pane·리뷰 보기 pane)을 닫는다(`terminal close --tab`). worker-release 는 --keep-open 터미널을
    닫지 않는다. 닫았거나 이미 없으면(terminal_handle_stale) None, 실패면 그 응답. orca 없음(12)은 올린다."""
    try:
        code, data = orca.call(["terminal", "close", "--terminal", handle, "--tab"], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return str(exc)
    error = data.get("error")
    if (code == 0 and data.get("ok")) or (isinstance(error, dict) and error.get("code") == "terminal_handle_stale"):
        return None
    return data


def start(p: Project, agent: Agent, spec: str, title: str, allow_fresh: bool = True) -> tuple[int, dict, bool]:
    """태스크를 고정 에이전트에 넣는다 → (종료 코드, 영수증, 새로 띄웠는가).
    작업 터미널이 살아 있으면 Orca 태스크를 만들어 그 터미널에 넣고, 확실히 죽었으면(dead) --keep-open 으로 새로 띄운다.
    상태를 알 수 없으면(unknown) 살아 있는 세션을 남기지 않게 띄우지 않고 OrchError. allow_fresh=False(인수인계)면
    새로 띄우지 않고 실패를 돌려준다."""
    handle = entry(p, agent.name).get("handle")
    state = orca.terminal_state(handle, wait=True) if handle else "dead"
    if state == "unknown":
        raise OrchError(f"{agent.title} 에이전트 작업 터미널({handle}) 상태를 알 수 없다(orca terminal show). 새 세션을 띄우면 "
                        "살아 있는 세션이 남을 수 있어 띄우지 않았다. 잠시 뒤 다시 한다")
    if state == "alive":
        code, created = orca.call(["orchestration", "task-create", "--spec", spec, "--task-title", title], check=False)
        orca_task = orca.find_key(created, ("taskId",))
        if code != 0 or not created.get("ok") or not orca_task:
            return code or 1, created, False
        code, receipt = orca.call(["orchestration", "worker-start", "--task", str(orca_task), "--terminal", handle,
                                   "--worktree", "current"], check=False)
        if code != 0 or not receipt.get("ok"):
            # 만든 Orca 태스크가 고아로 남지 않게 닫는다(최선 노력, 실패해도 원래 결과를 돌려준다).
            _best_effort(["orchestration", "task-update", "--id", str(orca_task), "--status", "failed",
                          "--result", f"worker-start 실패: {orca.error_text(receipt)}"])
        return code, receipt, False
    if not allow_fresh:
        return 1, {"ok": False, "error": {"code": "terminal_not_alive", "message": f"작업 터미널({handle})이 죽었다"}}, False
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
    """start 가 성공한 뒤 상태와 화면을 맞춘다. 맡은 태스크와 작업 터미널을 먼저 적는다(화면 배치 도중 죽어도 에이전트를
    잃지 않는다). 리뷰 pane 은 새로 띄웠을 때, 리뷰 pane 이 죽었을 때(shell_alive dead), 지난 배치가 실패했을 때(한 번만
    다시) 만든다. 디스패치가 탭 이름을 spec 첫 줄로 바꾸므로 매번 되돌린다."""
    before = entry(p, agent.name)
    handle = orca.find_key(receipt, ("terminal", "agentTerminalHandle", "terminalHandle")) if fresh else before.get("handle")

    def claim(e: dict) -> None:
        e.update(task=task_id, model=agent.launch.model, effort=agent.launch.effort)
        if fresh:
            e.update(handle=handle, review_handle=None, tab_id=None, launched_at=now(), since_reset=0)
            e.pop("layout_error", None)
            e.pop("layout_retried", None)

    recorded = update(p, agent.name, claim)
    retry = False
    if fresh:
        layout = make_layout(p, agent, str(handle)) if handle else {"error": "영수증에 작업 터미널 handle 이 없다"}
    elif handle and before.get("layout_error") and not before.get("layout_retried"):
        layout, retry = make_layout(p, agent, str(handle)), True
    elif handle and before.get("review_handle") and orca.shell_alive(before["review_handle"]) == "dead":
        layout = make_layout(p, agent, str(handle))
    else:
        layout = None
    if handle:
        _best_effort(["terminal", "rename", "--terminal", str(handle), "--title", TAB_PREFIX + agent.title])
    if layout is None:
        return recorded

    def write(e: dict) -> None:
        if layout.get("error"):
            e["layout_error"] = layout["error"]
            if retry:
                e["layout_retried"] = True
        else:
            e.update(review_handle=layout.get("review_handle"), tab_id=layout.get("tab_id"))
            e.pop("layout_error", None)
            e.pop("layout_retried", None)

    return update(p, agent.name, write)


def release_task(p: Project, name: str, task_id: str) -> None:
    """worker_done 뒤 에이전트를 비운다. 처리한 태스크 수(since_reset)를 센다."""
    def write(e: dict) -> None:
        if e.get("task") == task_id:
            e["task"] = None
            e["since_reset"] = int(e.get("since_reset") or 0) + 1
    update(p, name, write)


def enqueue(p: Project, name: str, task_id: str) -> None:
    """수정 라운드를 에이전트 대기열 끝에 넣는다(이미 있으면 끝으로 옮긴다)."""
    def write(e: dict) -> None:
        e["queue"] = [*(t for t in (e.get("queue") or []) if t != task_id), task_id]
    update(p, name, write)


def dequeue(p: Project, name: str, task_id: str) -> None:
    update(p, name, lambda e: e.update(queue=[t for t in (e.get("queue") or []) if t != task_id]))


def detach(p: Project, name: str, task_id: str) -> None:
    """중단(drop)했거나 작업자가 죽은 태스크를 에이전트에서 뗀다. 처리 수는 세지 않는다."""
    def write(e: dict) -> None:
        if e.get("task") == task_id:
            e["task"] = None
        e["queue"] = [t for t in (e.get("queue") or []) if t != task_id]
    update(p, name, write)


def forget(p: Project, name: str) -> None:
    """작업 터미널을 잊는다(다음 태스크 때 새로 띄운다). 맡은 태스크도 뗀다."""
    update(p, name, lambda e: e.update(handle=None, review_handle=None, tab_id=None, task=None))


def forget_dead(p: Project, cfg: Config) -> list[str]:
    """작업 터미널이 확실히 죽은(dead) 에이전트의 handle 을 지운다. unknown 은 두고 본다. 맡은 태스크는 drive 가 처리한다."""
    gone = []
    for name in cfg.agents:
        handle = entry(p, name).get("handle")
        if handle and orca.terminal_state(handle) == "dead":
            update(p, name, lambda e: e.update(handle=None, review_handle=None, tab_id=None))
            gone.append(name)
    return gone


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


def handoff_rel(name: str) -> str:
    return f"{HANDOFF_DIR}/{name}.md"


def handoff_block(p: Project, cfg: Config, role: str) -> str:
    """그 직무의 에이전트 인수인계 파일이 있으면 워커 spec 에 넣을 줄. 임시 워커에도 넣는다."""
    agent = cfg.agent_for(role)
    if agent is None or not (p.root / handoff_rel(agent.name)).is_file():
        return ""
    return (f"먼저 `{handoff_rel(agent.name)}` 를 읽는다. {agent.title} 에이전트가 이 프로젝트에서 정한 결정과 규칙, "
            "남은 일이 적혀 있다.\n\n")


def mark_reset_pending(p: Project, cfg: Config) -> list[str]:
    """묶음이 끝났다. 마지막 리셋 뒤 태스크를 하나라도 한 에이전트는 다음 태스크 전에 리셋한다."""
    names = []
    for name in cfg.agents:
        if int(entry(p, name).get("since_reset") or 0) >= 1:
            update(p, name, lambda e: e.update(reset_pending=True))
            names.append(name)
    return names


def request_reset(p: Project, cfg: Config, name: str) -> dict:
    """사용자가 요청한 리셋(orch agent reset)."""
    if name not in cfg.agents:
        raise OrchError(f"고정 에이전트가 아니다: {name}. 가능한 이름: {', '.join(cfg.agents) or '없음'}", 2)
    update(p, name, lambda e: e.update(reset_pending=True))
    return {"name": name, "reset_pending": True}


def begin_reset(p: Project, cfg: Config, name: str) -> dict | None:
    """비어 있고(맡은 태스크·대기열 없음) reset_pending 이면 인수인계 태스크를 넣는다. 아니면 None.
    작업 터미널이 없으면 비울 세션이 없으므로 표시만 지운다. 세션이 다른 일을 하면(최근 출력) 넣지 않고 None.
    넣지 못하면 {"status": "failed", "error", "count", "gave_up"}: RESET_FAIL_LIMIT 번째(gave_up)에만 리셋 표시를 지운다."""
    agent = cfg.agents[name]
    e = entry(p, name)
    if not e.get("reset_pending") or e.get("task") or e.get("resetting") or e.get("queue"):
        if e.get("reset_unknown_since"):  # 모름이 이어진 구간이 끊겼다. 다음 모름은 처음부터 잰다
            update(p, name, lambda x: x.pop("reset_unknown_since", None))
        return None
    handle = e.get("handle")
    state, active = terminal_activity(handle)
    if state == "unknown":
        # 터미널 상태를 알 수 없다. 다음에 다시 보되, RESET_UNKNOWN_SECONDS 가 지나면 리셋 표시를 지워 직무가 멈추지 않게 한다.
        since = e.get("reset_unknown_since")
        if not since:
            update(p, name, lambda x: x.update(reset_unknown_since=now()))
            return None
        try:
            age = (datetime.now().astimezone() - datetime.fromisoformat(since)).total_seconds()
        except (TypeError, ValueError):
            age = 0
        if age < RESET_UNKNOWN_SECONDS:
            return None
        update(p, name, lambda x: (x.update(reset_pending=False), x.pop("reset_unknown_since", None),
                                   x.pop("reset_failures", None)))
        return {"name": name, "status": "unknown_stuck", "minutes": int(age // 60)}
    if e.get("reset_unknown_since"):
        update(p, name, lambda x: x.pop("reset_unknown_since", None))
    if state == "dead":
        update(p, name, lambda x: (x.update(reset_pending=False, since_reset=0, handle=None),
                                   x.pop("reset_failures", None)))
        return {"name": name, "status": "skipped"}
    if active:
        return None  # 세션이 다른 일(자동 압축 등)을 한다. 지금 넣으면 worker-start 가 실패한다. 다음 틱에 다시 본다
    spec = render((TEMPLATES / "handoff.md").read_text(encoding="utf-8"),
                  {"agent": name, "agent_title": agent.title, "path": handoff_rel(name)})
    # 인수인계는 지금 세션의 맥락을 적는 일이다. 터미널이 그새 죽었으면 새 세션을 띄우지 않는다(맥락 없는 인수인계가 된다).
    code, receipt, _ = start(p, agent, spec, f"[handoff] {agent.title} 인수인계", allow_fresh=False)
    dispatch = orca.find_key(receipt, ("dispatchId",))
    if code != 0 or not receipt.get("ok") or not dispatch:
        # 일시 실패(예: 자동 압축이 막 시작됐다)는 reset_pending 을 두어 다음 틱에 다시 한다. RESET_FAIL_LIMIT 번째에만 포기한다.
        def failed(x: dict) -> None:
            x["reset_failures"] = int(x.get("reset_failures") or 0) + 1
            if x["reset_failures"] >= RESET_FAIL_LIMIT:
                x["reset_pending"] = False
        count = update(p, name, failed)["reset_failures"]
        gave_up = count >= RESET_FAIL_LIMIT
        if gave_up:
            update(p, name, lambda x: x.pop("reset_failures", None))
        return {"name": name, "status": "failed", "receipt": receipt, "error": orca.error_text(receipt),
                "count": count, "gave_up": gave_up}
    update(p, name, lambda x: (x.update(resetting={"dispatch_id": dispatch, "at": now()}),
                               x.pop("reset_failures", None)))
    _best_effort(["terminal", "rename", "--terminal", handle, "--title", TAB_PREFIX + agent.title])
    return {"name": name, "status": "handoff", "dispatch_id": dispatch}


def reset_owner(p: Project, dispatch_id: str | None) -> str | None:
    """이 디스패치가 인수인계 태스크면 그 에이전트 이름."""
    if not dispatch_id:
        return None
    for name, e in load_all(p).items():
        resetting = e.get("resetting") if isinstance(e, dict) else None
        if isinstance(resetting, dict) and resetting.get("dispatch_id") == dispatch_id:
            return name
    return None


def finish_reset(p: Project, name: str, ok: bool, sleep=time.sleep, beat=None) -> dict:
    """인수인계 worker_done 뒤. 성공이면 /clear 로 세션을 비우고 입력 준비를 기다린다. 실패면 세션을 그대로 둔다.
    새 세션 화면을 그리는 동안 넣은 입력은 사라질 수 있어(실험 E4) tui-idle 뒤 RESET_SETTLE_SECONDS 를 더 기다린다.
    beat 는 관제 heartbeat(기다리는 동안 관제가 꺼진 것으로 보이지 않게 단계마다 부른다)."""
    beat = beat or (lambda: None)
    handle = entry(p, name).get("handle")
    if not ok:
        update(p, name, lambda e: (e.update(resetting=None, reset_pending=False), e.pop("reset_failures", None)))
        return {"name": name, "status": "handoff_failed"}
    if handle:
        _best_effort(["terminal", "send", "--terminal", handle, "--text", "/clear", "--enter", "--wait-submit", "10"])
        beat()
        _best_effort(["terminal", "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", "30000"])
        beat()
        sleep(RESET_SETTLE_SECONDS)
        beat()
    update(p, name, lambda e: (e.update(resetting=None, reset_pending=False, since_reset=0, last_reset_at=now()),
                               e.pop("reset_failures", None)))
    return {"name": name, "status": "reset"}
