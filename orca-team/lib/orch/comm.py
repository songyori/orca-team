"""메인 ⇄ 코디네이터·기획·디자인 디렉터 워커 소통.

코디네이터 → 메인: outbox.jsonl(파일). 메인은 Run 에 묶이지 않으므로 Orca 알림이 메인 입력란에 들어오지 않는다.
메인 → 코디네이터: 코디네이터 Run 메일함(orca orchestration send --to run:<id>).
기획·디자인 디렉터 워커 → 메인: outbox 의 ask 줄(orch ask-user). 메인 → 그 워커: state/answers/ 답 파일(orch answer). ask.py 참고.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from collections.abc import Callable
from contextlib import contextmanager

from . import orca
from .paths import OrchError, Project
from .util import acquire_pid, load_run, now, read_json, release_pid, write_json, write_text_atomic

TYPES = ("progress", "question", "done")  # orch notify 가 받는 타입
POST_TYPES = (*TYPES, "ask", "approval")  # outbox 에 쓰는 모든 타입. ask·approval 은 메인이 그대로 쓰는 render 를 싣는다
REPLY_TYPES = ("question", "approval")  # 메인 답이 코디네이터 Run 으로 가는 타입. 답한 id 는 state/answered.json 에 남긴다
WAIT_TYPES = "worker_done,escalation,question,status"
MAIN_SUBJECT = "[main]"
MAIN_IDS_KEEP = 1000


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
    """orch answer 가 question·approval 에 한 번만 답하게 확인·전송·기록을 묶는다."""
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


@contextmanager
def _sigterm_exits():
    """SIGTERM 을 SystemExit 으로 바꿔 finally(pid 정리)가 돌게 한다."""
    def handler(signum, frame):
        raise SystemExit(143)
    try:
        old = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # 메인 스레드가 아니면 설치하지 않는다
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)


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


def tell(p: Project, body: str, reply_to: str | None = None) -> dict:
    run_id = load_run(p).get("run_id")
    if not run_id:
        raise OrchError("코디네이터가 아직 Run 을 만들지 않았다. /orca-team:up 으로 확인한다", 1)
    if not body.strip():
        raise OrchError("메시지 본문이 비었다", 2)
    subject = f"[main] reply {reply_to}" if reply_to else f"[main] {body.strip().splitlines()[0][:60]}"
    _, data = orca.call(["orchestration", "send", "--to", f"run:{run_id}", "--type", "status",
                         "--subject", subject, f"--body={body.strip()}"])  # '-' 로 시작하는 본문이 플래그로 읽히지 않게
    return {"sent": True, "subject": subject, "receipt": data.get("result")}


def main_message_ids(p: Project) -> list[str]:
    ids = read_json(p.main_msg_ids, [])
    return [str(i) for i in ids] if isinstance(ids, list) else []


def remember_main_messages(p: Project, data: dict) -> None:
    """받은 `[main]` 메시지 id 를 기록한다. 훅이 이 id 로의 reply(메인 입력란을 깨움)를 막는다."""
    result = data.get("result")
    messages = result.get("messages") if isinstance(result, dict) else None
    found = []
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict) and str(message.get("subject") or "").startswith(MAIN_SUBJECT):
            msg_id = orca.find_key(message, ("id",))
            if msg_id is not None:
                found.append(str(msg_id))
    if found:
        known = main_message_ids(p)
        write_json(p.main_msg_ids, list(dict.fromkeys(known + found))[-MAIN_IDS_KEEP:])


def wait(p: Project, ack: str | None, timeout_ms: int) -> dict:
    """코디네이터 메일함 대기. 한 번에 하나만 돈다. 호출 전 require_coordinator 를 확인한다."""
    p.ensure_state()
    acquire_pid(p.wait_pid, "wait")
    try:
        with _sigterm_exits():
            args = ["orchestration", "check"]
            if ack:
                args += ["--ack", ack]
            args += ["--wait", "--types", WAIT_TYPES, "--timeout-ms", str(timeout_ms)]
            _, data = orca.call(args)
            remember_main_messages(p, data)
            return data
    finally:
        release_pid(p.wait_pid)
