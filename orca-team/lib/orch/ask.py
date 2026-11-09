"""사용자 직통 질문: 기획·디자인 디렉터 워커 → 메인 질문(ask-user)과 메인의 답(answer).

질문은 outbox 에 `type: "ask"` 줄로 쓰고(메인의 orch watch 가 그대로 전한다), 답은 `state/answers/<out-id>.json` 으로 받는다.
질문한 워커는 답 파일을 2초 간격으로 확인하고, 540초가 지나면 exit 4 로 끝난다(Bash 포그라운드 상한이 10분이다).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from . import comm, queue
from .paths import OrchError, Project
from .tasks import TASK_ID_RE, load_state
from .util import now, read_json

ASK_ROLES = ("planner", "director")  # 메인을 거쳐 사용자에게 직접 묻는 직무. hooks/orca_team_hook.py ASK_WORKER_RE 와 같게 둔다
WAITING_EXIT = 4
POLL_SECONDS = 2.0
WAIT_SECONDS = 540.0
DEFAULT_HEADERS = {"planner": "기획", "director": "디자인"}  # 질문한 직무별 header 기본값
DEFAULT_HEADER = "기획"  # 그 밖의 직무(코디네이터의 notify --json 등)
EXAMPLE_PREFIX_RE = re.compile(r"\A(?:(?:예시:|예:|예\)|e\.g\.)\s*)+", re.I)  # build_render 가 "예: " 를 붙이므로 모두 뺀다
MAX_TEXT = 300
MAX_LABEL = 30
MAX_HEADER = 12
MAX_CONTEXT = 4000
REPLY_TYPES = comm.REPLY_TYPES


def wait_seconds() -> float:
    """답을 기다리는 초. 테스트는 ORCA_TEAM_ASK_WAIT 로 줄인다."""
    try:
        return float(os.environ.get("ORCA_TEAM_ASK_WAIT") or WAIT_SECONDS)
    except ValueError:
        return WAIT_SECONDS


def _text(value, name: str, limit: int | None, problems: list[str]) -> str:
    """앞뒤 공백을 뺀 문자열. 비었거나 문자열이 아니거나 limit 을 넘으면 problems 에 적는다."""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{name}: 필수")
        return ""
    value = value.strip()
    if limit is not None and len(value) > limit:
        problems.append(f"{name}: {limit}자 이하(지금 {len(value)}자)")
    return value


def validate_question(data, role: str | None = None) -> dict:
    """질문 JSON 을 검사해 정리한 dict 를 돌려준다. 어긴 필드를 모두 모아 exit 2 로 알린다.
    role 은 질문을 보낸 직무다. header 가 없으면 DEFAULT_HEADERS 의 그 직무 값, 모르는 직무면 DEFAULT_HEADER 를 쓴다.
    example 앞의 `예:`·`예)`·`예시:`·`e.g.` 는 겹쳐 있어도 모두 뺀다(build_render 가 붙인다)."""
    if not isinstance(data, dict):
        raise OrchError("질문은 JSON 객체여야 한다", 2)
    problems: list[str] = []
    header, example = data.get("header"), data.get("example")
    if isinstance(example, str):
        example = EXAMPLE_PREFIX_RE.sub("", example.strip(), count=1)
    q = {"question": _text(data.get("question"), "question", MAX_TEXT, problems),
         "example": _text(example, "example", MAX_TEXT, problems),
         "header": DEFAULT_HEADERS.get(role, DEFAULT_HEADER) if header is None
         else _text(header, "header", MAX_HEADER, problems)}
    if "\n" in q["question"]:
        problems.append("question: 한 문장(줄바꿈 없음)")
    context = data.get("context")  # 선택. 질문 전에 메인이 그대로 보여 줄 마크다운(확정 질문의 계획 요약 등)
    if context is not None:
        if not isinstance(context, str):
            problems.append("context: 마크다운 문자열")
        elif len(context.strip()) > MAX_CONTEXT:
            problems.append(f"context: {MAX_CONTEXT}자 이하(지금 {len(context.strip())}자)")
        elif context.strip():
            q["context"] = context.strip()
    options = data.get("options")
    if options is not None:
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            problems.append("options: 없거나 2~4개")
        else:
            q["options"] = []
            for i, option in enumerate(options):
                option = option if isinstance(option, dict) else {}
                q["options"].append({
                    "label": _text(option.get("label"), f"options[{i}].label", MAX_LABEL, problems),
                    "description": _text(option.get("description"), f"options[{i}].description", None, problems)})
            rec = data.get("recommended")
            if isinstance(rec, bool) or not isinstance(rec, int) or not 0 <= rec < len(options):
                problems.append(f"recommended: options 인덱스(0~{len(options) - 1})")
            q["recommended"] = rec
            q["reason"] = _text(data.get("reason"), "reason", None, problems)
    if problems:
        raise OrchError("질문 형식 오류: " + "; ".join(problems), 2)
    return q


def build_render(q: dict) -> dict:
    """메인이 손대지 않고 AskUserQuestion 에 넘기는 형태. 질문 뒤에 예시를 붙이고, 추천 선택지를 맨 앞으로 옮겨
    label 끝에 ` (추천)`, description 앞에 `추천 사유: <reason>. ` 를 붙인다. 선택지가 없으면 options 를 두지 않는다.
    context 가 있으면 render.context 로 싣는다(메인이 질문 전에 평문으로 그대로 보여 준다)."""
    render = {"header": q["header"], "question": f"{q['question']}\n예: {q['example']}"}
    if q.get("context"):
        render["context"] = q["context"]
    options = q.get("options")
    if options:
        rec = q["recommended"]
        first = {"label": f"{options[rec]['label']} (추천)",
                 "description": f"추천 사유: {q['reason'].rstrip('. ')}. {options[rec]['description']}"}
        rest = [{"label": o["label"], "description": o["description"]} for i, o in enumerate(options) if i != rec]
        render["options"] = [first, *rest]
        render["multiSelect"] = False
    return render


def load_question(source: str):
    """질문 JSON 을 파일에서, `-` 면 표준 입력에서 읽는다."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OrchError(f"질문 파일을 읽지 못했다({source}): {exc}", 2) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OrchError(f"질문 JSON 파싱 실패: {exc}", 2) from exc


def notify_question(p: Project, source: str) -> dict:
    """코디네이터의 구조화 질문. ask-user 와 같은 검증·render 로 outbox `question` 줄을 쓴다."""
    q = validate_question(load_question(source))
    return comm.post(p, "question", q["question"], {"render": build_render(q)})


def require_ask_role(p: Project, task_id: str) -> dict:
    """ask-user 는 working 상태의 ASK_ROLES 태스크만 쓴다."""
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = load_state(p, task_id) or {}
    if st.get("role") not in ASK_ROLES or st.get("status") != "working":
        raise OrchError(f"{task_id}: ask-user 는 working 상태의 {'·'.join(ASK_ROLES)} 태스크만 쓴다"
                        f"(role={st.get('role')}, status={st.get('status')})", 2)
    return st


def answer_path(p: Project, out_id: str) -> Path:
    return p.answers_dir / f"{out_id}.json"


def read_marker(p: Project, out_id: str) -> Path:
    """질문한 워커가 답을 받아 갔다는 표시."""
    return p.answers_dir / f"{out_id}.read"


def load_answer(p: Project, out_id: str) -> str | None:
    data = read_json(answer_path(p, out_id), None)
    return data["answer"] if isinstance(data, dict) and isinstance(data.get("answer"), str) else None


def mark_read(p: Project, out_id: str) -> None:
    p.answers_dir.mkdir(parents=True, exist_ok=True)
    read_marker(p, out_id).touch()


def write_answer(p: Project, out_id: str, text: str) -> Path:
    """답 파일을 원자적으로 만든다. 다 쓴 임시 파일을 link 로 붙이므로 기다리는 쪽은 완성된 파일만 본다. 있으면 exit 3."""
    p.answers_dir.mkdir(parents=True, exist_ok=True)
    target = answer_path(p, out_id)
    fd, tmp = tempfile.mkstemp(dir=p.answers_dir, prefix=f".{out_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"id": out_id, "answer": text, "at": now()}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        try:
            os.link(tmp, target)
        except FileExistsError:
            raise OrchError(f"{out_id} 에는 이미 답했다({target})", 3) from None
    finally:
        Path(tmp).unlink(missing_ok=True)
    return target


def wait_answer(p: Project, out_id: str, timeout: float | None = None, poll: float = POLL_SECONDS,
                clock=time.monotonic, sleep=time.sleep) -> dict:
    """답 파일이 생기면 {"id", "answer"} 를 돌려주고 읽음으로 기록한다. timeout(기본 540초)이 지나면 {"id", "status": "waiting"}."""
    deadline = clock() + (wait_seconds() if timeout is None else timeout)
    while True:
        found = load_answer(p, out_id)
        if found is not None:
            mark_read(p, out_id)
            return {"id": out_id, "answer": found}
        remaining = deadline - clock()
        if remaining <= 0:
            return {"id": out_id, "status": "waiting"}
        sleep(min(poll, remaining))


def find_entry(p: Project, out_id: str) -> dict | None:
    return next((e for e in comm.read_outbox(p) if e.get("id") == out_id), None)


def asks_of(entries: list[dict], task_id: str) -> list[dict]:
    return [e for e in entries if e.get("type") == "ask" and e.get("task") == task_id and isinstance(e.get("id"), str)]


def pending_answers(p: Project, task_id: str) -> dict:
    """답은 왔지만 아직 받아 가지 않은 질문을 돌려주고 읽음으로 기록한다. 답을 기다리는 질문 id 는 waiting 에 싣는다."""
    pending, waiting = [], None
    for entry in asks_of(comm.read_outbox(p), task_id):
        found = load_answer(p, entry["id"])
        if found is None:
            waiting = entry["id"]
        elif not read_marker(p, entry["id"]).exists():
            pending.append({"id": entry["id"], "question": entry.get("body"), "answer": found})
            mark_read(p, entry["id"])
    return {"task": task_id, "pending": pending, "waiting": waiting}


def ask_user(p: Project, task_id: str, source: str | None = None, wait_id: str | None = None,
             pending: bool = False) -> dict:
    """새 질문(source), 이미 올린 질문의 답 대기(wait_id), 읽지 않은 답 목록(pending) 중 하나를 한다."""
    role = require_ask_role(p, task_id).get("role")
    if pending:
        return pending_answers(p, task_id)
    if wait_id is not None:
        entry = find_entry(p, wait_id)
        if entry is None or entry.get("type") != "ask" or entry.get("task") != task_id:
            raise OrchError(f"{wait_id} 는 {task_id} 의 ask 질문이 아니다", 2)
        return wait_answer(p, wait_id)
    if source is None:
        raise OrchError("--json, --wait, --pending 중 하나가 필요하다", 2)
    q = validate_question(load_question(source), role)

    def one_at_a_time(entries: list[dict]) -> None:
        waiting = [e["id"] for e in asks_of(entries, task_id) if load_answer(p, e["id"]) is None]
        if waiting:
            raise OrchError(f"{task_id}: 답을 기다리는 질문({waiting[-1]})이 있다. 질문은 한 번에 하나다. "
                            f"orch ask-user --task {task_id} --wait {waiting[-1]} 로 그 답부터 기다린다", 3)

    entry = comm.post(p, "ask", q["question"], {"task": task_id, "render": build_render(q)}, check=one_at_a_time)
    return wait_answer(p, entry["id"])


def answer(p: Project, out_id: str, body: str) -> dict:
    """메인의 답. ask 는 답 파일로(질문한 워커가 받아 간다). decide 는 state/answered.json 에 남겨 닫고,
    worker_question 이면 답을 drive 큐로 워커에게 보낸다(orca orchestration reply). 같은 id 에 두 번 답하면 exit 3.
    approval 은 orch design-decide 로 답한다."""
    body = body.strip()
    if not body:
        raise OrchError("답이 비었다", 2)
    entry = find_entry(p, out_id)
    if entry is None:
        raise OrchError(f"{out_id}: outbox 에 없는 id 다", 2)
    kind = entry.get("type")
    if kind == "ask":
        path = write_answer(p, out_id, body)
        return {"id": out_id, "type": "ask", "task": entry.get("task"), "answer_file": str(path.relative_to(p.root))}
    if kind == "approval":
        raise OrchError(f"{out_id} 는 디자인 승인 질문이다. 화면별 결정을 "
                        f"`orch design-decide {out_id} --approve T-xxx,… --fix 'T-yyy=<요청>'` 으로 넘긴다", 2)
    if kind == "decide":
        with comm.answered_lock(p):
            if out_id in comm.load_answered(p)["ids"]:
                raise OrchError(f"{out_id} 에는 이미 답했다(state/answered.json)", 3)
            result = {"id": out_id, "type": "decide", "kind": entry.get("kind")}
            if entry.get("kind") == "worker_question":
                if not entry.get("message_id"):
                    raise OrchError(f"{out_id}: 워커 질문의 Orca 메시지 id 가 없다", 2)
                result["sent"] = queue.submit(p, "reply", {"message_id": entry["message_id"], "body": body})
            comm.record_answered(p, out_id)  # 보내지 못하면(예외) 기록하지 않는다(다시 답할 수 있다)
        return result
    raise OrchError(f"{out_id} 는 {kind} 메시지다. ask·decide 에만 답한다", 2)


def open_questions(p: Project) -> list[dict]:
    """메인이 아직 답하지 않은 질문 [{"id", "type", "task"?}]. 메인이 답하기 전에 재시작하면 outbox 커서가 이미 넘어가
    watch 가 다시 주지 않으므로 status 와 reask 가 이것으로 찾는다.

    - ask: 답 파일이 없고, 그 태스크가 working 상태의 ASK_ROLES 직무일 때(질문한 워커가 아직 기다린다)
    - question·approval: answered.json 에 없을 때. 기록 시작점(since)보다 앞선 줄은 뺀다(0.2.0 에서 올라온 질문)."""
    answered = comm.load_answered(p)
    since, done = comm.out_number(answered["since"]), set(answered["ids"])
    found = []
    for entry in comm.read_outbox(p):
        out_id, kind = entry.get("id"), entry.get("type")
        number = comm.out_number(out_id)
        if number is None:
            continue
        if kind == "ask":
            task = entry.get("task")
            st = (load_state(p, task) or {}) if isinstance(task, str) and TASK_ID_RE.match(task) else {}
            if st.get("role") in ASK_ROLES and st.get("status") == "working" and load_answer(p, out_id) is None:
                found.append({"id": out_id, "type": kind, "task": task})
        elif kind in REPLY_TYPES and since is not None and number >= since and out_id not in done:
            found.append({"id": out_id, "type": kind})
    return found


def reask(p: Project) -> list[dict]:
    """watch 가 이미 전한(커서 앞) 열린 질문의 outbox 줄을 watch 와 같은 형식으로 돌려준다. 커서는 그대로 둔다.
    아직 전하지 않은 질문은 watch 가 준다(두 번 묻지 않게)."""
    ids = {q["id"] for q in open_questions(p)}
    return [entry for entry in comm.read_outbox(p)[:comm.cursor(p)] if entry.get("id") in ids]
