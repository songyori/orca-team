#!/usr/bin/env python3
"""orca-team 훅. 사용법: orca_team_hook.py <role-mark|guard|session-start|stop>

`.orchestration/config.toml` 이 없는 프로젝트에서는 아무것도 하지 않는다.
훅 오류로 세션이 막히면 안 되므로 예외는 state/hook-errors.log 에 남기고 통과시킨다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from orch.comm import read_outbox, unread  # noqa: E402
from orch.paths import Project  # noqa: E402
from orch.util import load_run, now, pid_alive, read_json, record_plugin_root, write_json  # noqa: E402

WORKER_MARKS = ("Your task ID is:", "=== TASK ===", "Please carry out this task from my Orca coordinator")
# templates/worker-spec.md 첫 줄 "너는 orca-team 의 {role_title}({role}) 워커다." 의 직무가 orch ask-user 로 사용자에게 직접 묻는
# 직무(orch.ask.ASK_ROLES)면 세션을 worker:<직무> 로 표시한다. 역할 표시는 매 첫 프롬프트에 돌므로 orch.ask 를 불러오지 않고 여기 적는다.
ASK_WORKER_RE = re.compile(r"orca-team 의 [^\n(]*\((planner|director)\) 워커다")
# 세션 표시 → 거부 안내에 쓰는 직무 이름. AskUserQuestion 거부 안내만 다르고 나머지 규칙은 워커와 같다
ASK_WORKER_TITLES = {"worker:planner": "기획", "worker:director": "디자인 디렉터"}
SETUP_PREFIXES = (".claude/", ".orchestration/", "docs/superpowers/")
SETUP_FILES = {"CLAUDE.md", "AGENTS.md", ".gitignore"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ORCA_ORCHESTRATION_RE = re.compile(r"(^|[\s;&|(`\"'])(?:\S*/)?orca[\"']?\s+orchestration\b")
STATE_DIR = ".orchestration/state"
CONTEXT_DENIED = "context-denied.json"  # state/ 안. render.context 미표시로 한 번 거부한 outbox id(같은 id 는 두 번째에 통과)
TRANSCRIPT_TAIL_LINES = 400
TRANSCRIPT_TAIL_BYTES = 1 << 20  # 끝 1MiB 안에서만 줄을 본다. 훅은 빨리 끝나야 한다
MARKDOWN_NOISE_RE = re.compile(r"[\s#*_>|\-`]+")
# 정규화한 render.context 의 앞 80자가 메인 텍스트에 있으면 보여 준 것으로 본다. 확정 질문 context 는 제목·첫 항목만으로도
# 80자를 넘어 다른 질문과 겹치지 않고, 표·제목을 다시 그린 것은 맞지만 요약하면 맞지 않는다.
CONTEXT_FINGERPRINT = 80
ORCH_DRIVE_RE = re.compile(r"(^|[\s;&|(`\"'])(?:\S*/)?orch[\"']?(?:\s+-\S+)*\s+drive(?![\w-])")
# 세션 시작 안내의 관제 줄(자동 부활 문구가 없을 때). /orca-team:up 은 수동 복구로 남긴다.
DEFAULT_UP_LINE = ("- 관제(orch drive)는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다. 메시지 watcher 가 꺼져 있으면 Bash "
                   "`run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. 관제 상태가 의심되면 "
                   "`/orca-team:up` 을 실행한다.")


def find_project(payload: dict) -> Project | None:
    start = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()
    for folder in (start, *start.parents):
        if (folder / ".orchestration" / "config.toml").is_file():
            return Project(folder)
    return None


SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def valid_session_id(session_id) -> bool:
    return isinstance(session_id, str) and bool(SESSION_ID_RE.match(session_id)) and ".." not in session_id


def env_agent(p: Project) -> bool:
    """ORCA_TERMINAL_HANDLE 이 고정 에이전트 작업 터미널(state/agents.json 의 handle)인가.
    /clear 로 세션 id 가 바뀌어 표시가 없어도 워커로 본다."""
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        return False
    data = read_json(p.agents_json, {})
    return isinstance(data, dict) and any(isinstance(e, dict) and e.get("handle") == handle for e in data.values())


def role_of(p: Project, session_id: str | None) -> str:
    if valid_session_id(session_id):
        marker = p.sessions / session_id
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip() or "main"
    return "worker" if env_agent(p) else "main"


def worker_role(prompt: str) -> str | None:
    """디스패치 프리앰블이면 worker 또는 worker:<직무>(orch ask-user 로 묻는 직무)."""
    if not any(mark in prompt for mark in WORKER_MARKS):
        return None
    found = ASK_WORKER_RE.search(prompt)
    return f"worker:{found.group(1)}" if found else "worker"


def log_hook_error(p: Project, label: str) -> None:
    """훅 예외를 state/hook-errors.log 에 남긴다. 기록도 실패하면 조용히 넘어간다."""
    try:
        p.ensure_state()
        with (p.state / "hook-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now()} {label}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def auto_up_context(p: Project, force: bool) -> str | None:
    """관제 자동 부활(orch.autoup.check). 실패해도 세션을 막지 않고 hook-errors.log 에 남긴다.
    import 는 여기서 한다. guard 훅은 매 도구 호출마다 돌아 무거운 모듈을 미리 불러오지 않는다."""
    try:
        from orch import autoup

        return autoup.check(p, force=force)
    except Exception:  # noqa: BLE001  훅은 세션을 막지 않는다
        log_hook_error(p, "auto-up")
        return None


def role_mark(p: Project, payload: dict):
    session_id = payload.get("session_id")
    if not valid_session_id(session_id):
        return None
    marker = p.sessions / session_id
    current = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    found = worker_role(payload.get("prompt") or "")
    # 고정 에이전트는 한 세션에서 여러 직무 태스크를 받는다. 워커 세션은 프리앰블마다 직무를 다시 적는다. 메인은 그대로다.
    if current is None or (found and current.startswith("worker")):
        p.ensure_state()
        marker.write_text((found or ("worker" if env_agent(p) else "main")) + "\n", encoding="utf-8")
    if role_of(p, session_id) != "main":
        return None
    context = auto_up_context(p, force=False)
    if not context:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def project_rel(p: Project, raw: str) -> str | None:
    """프로젝트 안 경로면 상대 경로(posix), 밖이면 None."""
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = p.root / path
    try:
        return path.resolve().relative_to(p.root.resolve()).as_posix()
    except ValueError:
        return None


def product_path(p: Project, raw: str) -> str | None:
    """프로젝트 안 산출물 파일이면 상대 경로, 아니면 None."""
    rel = project_rel(p, raw)
    if rel is None or rel in SETUP_FILES or rel.startswith(SETUP_PREFIXES):
        return None
    return rel


def in_state_dir(rel: str | None) -> bool:
    return rel is not None and (rel == STATE_DIR or rel.startswith(STATE_DIR + "/"))


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def normalize(text: str) -> str:
    """공백과 마크다운 기호(#*_>|-`)를 뺀다. 표·제목·목록을 다시 그려도 같은 값이 된다."""
    return MARKDOWN_NOISE_RE.sub("", text)


def asked_questions(tool_input: dict) -> set[str]:
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return set()
    return {q["question"].strip() for q in questions if isinstance(q, dict) and isinstance(q.get("question"), str)}


def context_items(p: Project, asked: set[str]) -> list[dict]:
    """이번 AskUserQuestion 이 묻는 열린 질문 중 render.context 가 있는 outbox 줄. 열린 질문은 ask.open_questions 가 정한다."""
    from orch import ask

    open_ids = {q["id"] for q in ask.open_questions(p)}
    found = []
    for entry in read_outbox(p):
        render = entry.get("render")
        if entry.get("id") not in open_ids or not isinstance(render, dict):
            continue
        context, question = render.get("context"), render.get("question")
        if isinstance(context, str) and normalize(context) and isinstance(question, str) and question.strip() in asked:
            found.append(entry)
    return found


def assistant_text(transcript) -> str | None:
    """transcript(JSONL) 끝부분의 assistant text 블록을 이어 붙인다. 경로가 없거나, 읽지 못하거나, 깨진 줄이 있으면 None."""
    if not isinstance(transcript, str) or not transcript:
        return None
    path = Path(transcript)
    try:
        if not path.is_file():
            return None
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            data = fh.read()
    except OSError:
        return None
    # splitlines() 는 U+2028·U+2029·U+0085 에서도 끊는다. Claude Code 는 이것을 이스케이프하지 않으므로 JSON 한 줄이 깨진다.
    lines = [raw.decode("utf-8", errors="replace") for raw in data.split(b"\n")]
    if size > TRANSCRIPT_TAIL_BYTES:
        lines = lines[1:]  # 중간에서 잘린 첫 줄
    texts = []
    for line in lines[-TRANSCRIPT_TAIL_LINES:]:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return None
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            texts += [b["text"] for b in content
                      if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return "\n".join(texts)


def context_guard(p: Project, payload: dict, tool_input: dict):
    """메인이 render.context 를 먼저 보여 주지 않고 그 질문을 AskUserQuestion 으로 부르면 한 번 거부한다(F1).

    같은 outbox id 는 한 번만 거부한다(transcript 기록이 늦어도 메인이 거부에 갇히지 않게). 판단할 수 없으면 통과시킨다."""
    asked = asked_questions(tool_input)
    if not asked:
        return None
    denied_path = p.state / CONTEXT_DENIED
    data = read_json(denied_path, None)
    ids = data.get("ids") if isinstance(data, dict) else None
    denied = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
    items = [e for e in context_items(p, asked) if e["id"] not in denied]
    if not items:
        return None
    text = assistant_text(payload.get("transcript_path"))
    if text is None:
        return None
    shown = normalize(text)
    unshown = [e for e in items if normalize(e["render"]["context"])[:CONTEXT_FINGERPRINT] not in shown]
    if not unshown:
        return None
    try:
        write_json(denied_path, {"ids": [*denied, *(e["id"] for e in unshown)]})
    except OSError:
        return None  # 거부를 기록하지 못하면 다음에도 거부할 수 있으므로 통과시킨다
    blocks = "\n\n".join(f"[{e['id']}]\n{e['render']['context']}" for e in unshown)
    return deny("이 질문에는 사용자에게 먼저 보여 줄 `render.context` 가 있다. 아래 내용을 요약하거나 바꾸지 말고 평문으로 "
                "그대로 출력한 뒤 같은 render 로 AskUserQuestion 을 다시 부른다([out-id] 줄은 출력하지 않는다)."
                f"\n\n{blocks}")


def guard(p: Project, payload: dict):
    role = role_of(p, payload.get("session_id"))
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if tool == "AskUserQuestion" and role == "main":
        return context_guard(p, payload, tool_input)
    if tool == "AskUserQuestion" and role in ASK_WORKER_TITLES:
        return deny(f"{ASK_WORKER_TITLES[role]} 워커는 AskUserQuestion 을 쓰지 않는다. 질문 JSON 을 "
                    "`.orchestration/bin/orch ask-user --task T-xxx --json -` 의 표준 입력으로 보내 메인을 거쳐 사용자에게 묻는다"
                    "(직무 정의 \"질문 보내기\", Bash `timeout: 600000`).")
    if tool == "AskUserQuestion" and role == "worker":
        return deny("워커는 사용자에게 직접 묻지 않는다. 프리앰블의 ask 명령으로 묻는다(메인이 답한다).")
    if tool in EDIT_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if in_state_dir(project_rel(p, raw)):
            return deny("`.orchestration/state/` 는 orch 가 관리한다. 직접 고치지 않는다. "
                        "복구는 `.orchestration/bin/orch status` 의 next, 중단은 `orch drop` 을 쓴다.")
        rel = product_path(p, raw)
        if role == "main" and rel and not p.setup_mode.exists():
            return deny(f"메인은 산출물({rel})을 직접 수정하지 않는다. 태스크 spec(`.orchestration/tasks/`)을 쓰고 커밋한 뒤 "
                        "`.orchestration/bin/orch start T-xxx` 로 맡긴다(CLAUDE.md \"메인 규칙\").")
    command = (tool_input.get("command") or "") if tool == "Bash" else ""
    if command and role == "main" and ORCA_ORCHESTRATION_RE.search(command):
        return deny("메인은 `orca orchestration` 을 직접 부르지 않는다. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 "
                    "들어온다. Orca 를 부르는 일은 관제(orch drive)가 한다. 같은 일을 하는 `.orchestration/bin/orch` 명령을 쓴다.")
    if command and role == "main" and ORCH_DRIVE_RE.search(command):
        return deny("`orch drive` 는 관제 탭 셸에서만 돈다. 메인 터미널이 관제가 되면 Orca 알림이 사용자 입력란에 들어온다. "
                    "관제를 띄우려면 `/orca-team:up` 을 쓴다.")
    return None


def session_start(p: Project, payload: dict):
    record_plugin_root(p.state, Path(__file__).resolve().parents[1])
    if role_of(p, payload.get("session_id")) != "main":
        return None
    lines = ["orca-team 프로젝트다. 이 안내는 사용자와 대화하는 메인 세션에만 해당한다. Orca 워커 작업 지시라면 이 안내를 무시한다.",
             "- 메인은 사용자와 대화하고 판단한다. 산출물은 태스크로 맡기고, 관제가 올린 decide 를 처리한다(CLAUDE.md \"메인 규칙\")."]
    context = auto_up_context(p, force=True)
    lines.append(f"- {context}" if context else DEFAULT_UP_LINE)
    pending = len(unread(p))
    if pending:
        lines.append(f"- 아직 전하지 않은 관제·워커 메시지가 {pending}건 있다. watcher 를 띄우면 바로 받는다.")
    asked = open_question_count(p)
    if asked:
        lines.append(f"- 답하지 않은 질문이 {asked}건 있다. `/orca-team:up` 이 `orch reask` 로 다시 보여 준다.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}


def open_question_count(p: Project) -> int:
    """메인이 답하지 않은 질문 수. 세지 못하면 0(안내 한 줄을 빼고 나머지 안내는 그대로 낸다)."""
    try:
        from orch import ask

        return len(ask.open_questions(p))
    except Exception:  # noqa: BLE001  훅은 세션을 막지 않는다
        return 0


def stop(p: Project, payload: dict):
    if payload.get("stop_hook_active") or role_of(p, payload.get("session_id")) != "main":
        return None
    if p.setup_mode.exists() or not load_run(p).get("run_id") or pid_alive(p.watch_pid):
        return None
    return {"decision": "block",
            "reason": "orca-team: 관제·워커 메시지 watcher 가 꺼져 있다. Bash `run_in_background: true` 로 "
                      "`.orchestration/bin/orch watch` 를 띄우고 턴을 끝낸다. 이미 받은 메시지가 있으면 사용자에게 먼저 전한다."}


HANDLERS = {"role-mark": role_mark, "guard": guard, "session-start": session_start, "stop": stop}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in HANDLERS:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        p = find_project(payload)
    except (ValueError, OSError, TypeError):  # 깨진 입력·UnicodeDecodeError·경로 오류는 통과시킨다
        return 0
    if p is None:
        return 0
    try:
        out = HANDLERS[sys.argv[1]](p, payload)
    except Exception:  # noqa: BLE001  훅은 세션을 막지 않는다
        log_hook_error(p, sys.argv[1])
        return 0
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
