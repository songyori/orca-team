"""메인 ⇄ 관제·기획·디자인 디렉터 워커 소통.

관제 → 메인: outbox.jsonl(파일). 메인은 Run 에 묶이지 않으므로 Orca 알림이 메인 입력란에 들어오지 않는다.
메인 → 관제: 요청 큐(queue.py). 기획·디자인 디렉터 워커 → 메인: outbox 의 ask 줄(orch ask-user).
메인 → 그 워커: state/answers/ 답 파일(orch answer). ask.py 참고.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from collections.abc import Callable
from contextlib import contextmanager

from .paths import OrchError, Project
from .util import acquire_pid, now, read_json, release_pid, write_json, write_text_atomic

TYPES = ("progress", "done")  # orch notify 가 받는 타입. 질문은 decide(관제)·ask(orch ask-user)로만 올린다(orch answer 가 받는 타입)
# outbox 에 쓰는 모든 타입. ask·approval 은 메인이 그대로 쓰는 render 를 싣는다. question 은 0.6 이하 outbox 줄과의 호환으로만 남긴다
POST_TYPES = (*TYPES, "question", "ask", "approval", "decide", "result")
DECIDE_KINDS = ("worker_failed", "worker_question", "escalation", "max_rounds", "repeat_test_failure",
                "test_config", "planning_gaps", "unknown_regression", "token_update")
REPLY_TYPES = ("approval", "decide")  # 메인이 답해 닫는 타입. 답한 id 는 state/answered.json 에 남긴다


def read_outbox(p: Project) -> list[dict]:
    """완전한 줄(개행으로 끝남)만 읽고, 파싱 실패한 줄은 건너뛴다."""
    try:
        text = p.outbox.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    complete = text[: text.rfind("\n") + 1]
    entries = []
    for line in complete.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def cursor(p: Project) -> int:
    try:
        return int(p.outbox_cursor.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def unread(p: Project) -> list[dict]:
    return read_outbox(p)[cursor(p):]


def out_number(out_id) -> int | None:
    """`out-0012` → 12. 형식이 아니면 None."""
    tail = str(out_id or "").removeprefix("out-")
    return int(tail) if tail.isdigit() else None


def _next_id(entries: list[dict]) -> str:
    top = max((n for entry in entries if (n := out_number(entry.get("id"))) is not None), default=0)
    return f"out-{top + 1:04d}"


@contextmanager
def _file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _outbox_lock(p: Project):
    return _file_lock(p.outbox.with_name(p.outbox.name + ".lock"))


def answered_lock(p: Project):
    """approval·decide 에 한 번만 답하게 answered.json 의 확인과 기록을 각각 짧게 잠근다(Orca 호출·큐 대기 동안은 잡지 않는다)."""
    return _file_lock(p.answered_json.with_name(p.answered_json.name + ".lock"))


def load_answered(p: Project) -> dict:
    """state/answered.json: {"since": 기록을 시작한 첫 question·approval id, "ids": 답한 id}. 없거나 깨졌으면 since None.

    since 보다 앞선 question·approval(0.2.0 에서 올라와 답 기록이 없던 줄)은 열린 질문으로 보지 않는다."""
    data = read_json(p.answered_json, None)
    data = data if isinstance(data, dict) else {}
    since = data.get("since") if out_number(data.get("since")) is not None else None
    ids = data.get("ids") if isinstance(data.get("ids"), list) else []
    return {"since": since, "ids": [i for i in ids if isinstance(i, str)]}


def record_answered(p: Project, out_id: str) -> None:
    """답한 id 를 덧붙여 원자적으로 쓴다(answered_lock 안에서 부른다). 기록이 없었으면 지금 outbox 다음 id 부터 센다."""
    data = load_answered(p)
    since = data["since"] or _next_id(read_outbox(p))
    write_json(p.answered_json, {"since": since, "ids": [*data["ids"], out_id]})


def post(p: Project, kind: str, body: str, extra: dict | None = None,
         check: Callable[[list[dict]], None] | None = None) -> dict:
    """outbox 에 한 줄 쓴다. check 는 잠금 안에서 기존 줄을 보고, 거부하려면 OrchError 를 던진다."""
    if kind not in POST_TYPES:
        raise OrchError(f"outbox 타입이 아니다: {kind}", 2)
    if not body.strip():
        raise OrchError("메시지 본문이 비었다", 2)
    p.ensure_state()
    with _outbox_lock(p):
        entries = read_outbox(p)
        if check is not None:
            check(entries)
        entry = {"id": _next_id(entries), "type": kind, "body": body.strip(), **(extra or {}), "at": now()}
        if kind in REPLY_TYPES and load_answered(p)["since"] is None:
            # 답 기록의 시작점. 이 줄부터 답하지 않은 question·approval 을 열린 질문으로 본다(ask.open_questions).
            write_json(p.answered_json, {"since": entry["id"], "ids": load_answered(p)["ids"]})
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # 이전 쓰기가 개행 없이 끊겼다면 새 줄이 그 뒤에 붙지 않게 먼저 개행으로 끊는다.
        try:
            with p.outbox.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell():
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        line = "\n" + line
        except OSError:
            pass
        with p.outbox.open("a", encoding="utf-8") as fh:
            fh.write(line)
    return entry


def notify(p: Project, kind: str, body: str) -> dict:
    if kind not in TYPES:
        raise OrchError(f"--type 은 {'|'.join(TYPES)} 중 하나다", 2)
    return post(p, kind, body)


def post_decide(p: Project, kind: str, task: str | None, summary: str, files=(), commands=(),
                extra: dict | None = None) -> dict:
    """메인에 판단을 맡긴다(drive). 메인은 files 만 읽고 commands 중 하나로 처리한 뒤 orch answer 로 닫는다."""
    if kind not in DECIDE_KINDS:
        raise OrchError(f"decide 종류가 아니다: {kind}", 2)
    summary = summary.strip() or "(요약 없음)"
    return post(p, "decide", f"[{kind}] {task or '-'}: {summary}",
                {"kind": kind, "task": task, "summary": summary, "files": list(files), "commands": list(commands),
                 **(extra or {})})


@contextmanager
def _sigterm_exits():
    """SIGTERM·SIGHUP(탭 닫힘)을 SystemExit 으로 바꿔 finally(pid 정리)가 돌게 한다."""
    def handler(signum, frame):
        raise SystemExit(128 + signum)
    try:
        olds = {sig: signal.signal(sig, handler) for sig in (signal.SIGTERM, signal.SIGHUP)}
    except ValueError:  # 메인 스레드가 아니면 설치하지 않는다
        yield
        return
    try:
        yield
    finally:
        for sig, old in olds.items():
            signal.signal(sig, old)


def watch(p: Project, poll: float = 2.0, timeout: float | None = None,
          deliver: Callable[[list[dict]], None] | None = None) -> list[dict]:
    """새 outbox 줄이 생기면 돌려주고 끝난다. Orca 를 호출하지 않는다.

    deliver 가 있으면 줄을 거기에 넘겨 출력이 끝난 뒤에만 커서를 올린다(전달 전에 죽어도 유실 없음)."""
    p.ensure_state()
    acquire_pid(p.watch_pid, "watch")
    deadline = None if timeout is None else time.monotonic() + timeout
    parent = os.getppid()
    try:
        with _sigterm_exits():
            while True:
                if os.getppid() != parent:  # 띄운 셸(세션)이 죽었다. 고아로 남지 않고 커서를 그대로 둔 채 끝난다
                    return []
                lines, seen = read_outbox(p), cursor(p)
                if len(lines) > seen:
                    fresh = lines[seen:]
                    if deliver is not None:
                        deliver(fresh)
                    write_text_atomic(p.outbox_cursor, str(len(lines)))
                    return fresh
                if deadline is None:
                    time.sleep(poll)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return []
                    time.sleep(min(poll, remaining))
    finally:
        release_pid(p.watch_pid)
