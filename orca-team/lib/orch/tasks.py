"""태스크 spec, 상태(pending 포함), 선행 태스크(depends_on), 디스패치(start/fix/accept), 소유 경로 잠금,
사용자 승인 게이트(awaiting_user)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import agents, orca, testgate
from .config import DEFAULT_APPROVAL_ROLES, Config
from .globs import any_overlap
from .paths import TEMPLATES, OrchError, Project
from .util import (acquire_pid, git, now, pid_alive, process_alive, read_json, release_pid, render, require_coordinator,
                   write_json)

TASK_ID_RE = re.compile(r"^T-\d{3,}$")
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
REQUIRED_SECTIONS = ("Target", "Change", "Constraints", "Acceptance")
PLACEHOLDER_RE = re.compile(r"^<.*>$")
# 소유 경로 잠금을 잡는 상태. awaiting_user 는 사용자 수정 요청으로 같은 경로를 다시 고칠 수 있어 잠금을 유지한다.
# pending(plan apply 가 만든, 아직 띄우지 않은 태스크)은 잡지 않는다.
ACTIVE = ("working", "reviewing", "changes", "approved", "launch_failed", "awaiting_user")
FINISHED = ("done", "dropped")  # 소유 경로 잠금을 잡지 않는다
RECOVERY_REF = "orca skills get orchestration --reference references/recovery-and-cleanup.md"
LAUNCH_STALE_SECONDS = 600  # 이보다 오래된 pending 기록은 pid 가 재사용됐을 수 있어 진행 중으로 보지 않는다
LAUNCHING_NEXT = "워커를 띄우는 중이다. 끝날 때까지 기다린 뒤 orch status 로 다시 본다"
UNKNOWN_START_LIMIT = 3  # 같은 태스크에서 start_unknown 이 이만큼이면 다시 띄우지 않고 메인에 올린다
DIED_NOTE = "이전 작업자가 중간에 끝났다. 리포트와 [{task_id}] 커밋을 확인하고 남은 일을 이어서 한다"
FIX_BLOCK = """## 수정 라운드
이전 워커의 대화는 사라졌다. 먼저 `docs/tasks/{task_id}/report.md`(있으면)와 이 태스크의 `[{task_id}]` 커밋(`git log --grep "^\\[{task_id}\\]"`)을 읽어 지금까지 한 일을 파악한다.
{review_line}코디네이터 지시: {note}

"""
REVIEW_LINE = ("`{review}` 리뷰를 읽고 막는 지적(`## 지적 사항` 의 P1, `## 이전 지적 확인` 의 미해결, orch 사전 검사 사유)을 모두 고친다. "
               "고치지 않는 항목은 리포트에 이유를 적는다. `## 후속`·`## 기획 누락`·`## 회귀` 는 고치지 않는다"
               "(코디네이터가 따로 태스크로 만든다).\n")
TEST_BLOCK = """## 테스트
- 순서: 작업을 `[{task_id}]` 커밋으로 먼저 커밋한다 → `.orchestration/bin/orch test {task_id}` 를 돌린다 → 리포트를 따로 커밋한다. quick 은 커밋된 변경만 보므로 커밋하기 전에 돌리면 orch 가 거부한다(exit 2).
- worker_done 전에 `.orchestration/bin/orch test {task_id}` 가 통과해야 한다(exit 0). 이 태스크가 바꾼 파일과 관련된 테스트만 돈다. 실패하면 고쳐서 커밋하고 다시 돌린다.
- 테스트가 소유 경로에 만드는 산출물(`__pycache__`, 스냅숏 등)은 `.gitignore` 에 넣는다. 무시되지 않은 새 파일이 있으면 orch test 가 거부한다.
- 전체 테스트는 돌리지 않는다. 묶음 테스트 태스크와 orch 가 돌린다.
- 리포트의 검증 출력에 orch test 출력의 `status`·`seconds` 를 붙인다. 실패를 고쳤으면 그 `tail` 도 붙인다.

"""
TESTER_TEST_BLOCK = """## 테스트
- 이 태스크는 묶음 테스트다. 선행 태스크(묶음 개발 태스크)의 기능을 잇는 묶음 통합 테스트를 소유 경로에 쓰고 커밋한다.
- 커밋한 뒤 `.orchestration/bin/orch test {task_id} --full` 로 전체 테스트를 한 번 돌린다. 다시 돌리지 않는다. 리뷰는 같은 라운드의 이 결과를 다시 쓴다.
- 실패는 QA 리포트에서 둘로 나눈다. 테스트 코드의 잘못은 고친다(고친 테스트를 커밋한 뒤 `.orchestration/bin/orch test {task_id}` 로 확인할 수 있다). 제품 회귀는 실패한 테스트 이름, 원인으로 보이는 선행 태스크 ID, 근거를 적는다. 제품 회귀는 리뷰가 `## 회귀` 로 넘기고 코디네이터가 회귀 수정 태스크로 만든다.
- 테스트를 통과시키려고 테스트를 약하게 만들거나 건너뛰거나 지우지 않는다. 제품 코드는 고치지 않는다.

"""


@dataclass
class TaskDoc:
    id: str
    role: str
    title: str
    front: dict
    body: str
    sections: dict = field(default_factory=dict)


def task_path(p: Project, task_id: str) -> Path:
    return p.tasks_dir / f"{task_id}.md"


def parse_text(text: str, where: str) -> TaskDoc:
    """태스크 spec 원문을 읽는다. where 는 오류 문구에 쓸 위치다."""
    match = FRONT_RE.match(text)
    if not match:
        raise OrchError(f"{where}: front matter(---) 가 없다", 2)
    front = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
    body = match.group(2)
    sections, current = {}, None
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return TaskDoc(front.get("id", ""), front.get("role", ""), front.get("title", ""), front, body.strip(),
                   {k: "\n".join(v).strip() for k, v in sections.items()})


def parse_task(path: Path) -> TaskDoc:
    return parse_text(path.read_text(encoding="utf-8"), str(path))


def deps_of(doc: TaskDoc) -> list[str]:
    """front matter `depends_on: T-001, T-002` 의 선행 태스크 ID."""
    return [x.strip() for x in doc.front.get("depends_on", "").split(",") if x.strip()]


def dep_met(dep_st: dict | None, role: str | None, approval) -> bool:
    """선행 태스크가 충족됐는가. done 이면 충족이다. 선행 태스크와 후속 태스크(role)가 모두 사용자 승인 대상 직무면
    awaiting_user 도 충족이다(디자인 시스템 → 화면처럼 이어진 디자인을 한 번의 design-review 로 함께 승인받는다).
    승인 대상이 아닌 후속 태스크는 사용자 승인(done)을 기다린다."""
    status = (dep_st or {}).get("status")
    if status == "done":
        return True
    return status == "awaiting_user" and role in approval and (dep_st or {}).get("role") in approval


def unmet_deps(p: Project, deps: list[str], role: str | None = None, approval=()) -> list[tuple[str, str]]:
    """충족되지 않은 선행 태스크와 그 상태. role 은 후속 태스크의 직무, approval 은 사용자 승인 대상 직무.
    상태 파일이 없으면 "없음"."""
    unmet = []
    for dep in deps:
        st = load_state(p, dep)
        if not dep_met(st, role, approval):
            unmet.append((dep, (st or {}).get("status") or "없음"))
    return unmet


def format_unmet(unmet: list[tuple[str, str]]) -> str:
    return ", ".join(f"{dep}({status})" for dep, status in unmet)


def validate(doc: TaskDoc, cfg: Config, expected_id: str) -> None:
    problems = []
    if doc.id != expected_id:
        problems.append(f"front matter id={doc.id or '없음'}")
    if doc.role not in cfg.roles:
        problems.append(f"role={doc.role or '없음'}")
    if not doc.title:
        problems.append("title 없음")
    bad_deps = [dep for dep in deps_of(doc) if not TASK_ID_RE.match(dep) or dep == expected_id]
    if bad_deps:
        problems.append(f"depends_on={', '.join(bad_deps)}")
    for name in REQUIRED_SECTIONS:
        lines = [line.strip() for line in doc.sections.get(name, "").splitlines() if line.strip()]
        if not lines or all(PLACEHOLDER_RE.match(line) for line in lines):
            problems.append(f"## {name} 비었음")
    if problems:
        raise OrchError(f"{expected_id} spec 불완전: {', '.join(problems)}", 2)


def load_doc(p: Project, cfg: Config, task_id: str) -> TaskDoc:
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    path = task_path(p, task_id)
    if not path.is_file():
        raise OrchError(f"태스크 spec 없음: {path}", 2)
    doc = parse_task(path)
    validate(doc, cfg, task_id)
    return doc


def owns_of(doc: TaskDoc, cfg: Config) -> list[str]:
    override = [x.strip() for x in doc.front.get("owns", "").split(",") if x.strip()]
    return cfg.owns(doc.role, override or None)


def state_path(p: Project, task_id: str) -> Path:
    return p.task_state_dir / f"{task_id}.json"


def load_state(p: Project, task_id: str) -> dict | None:
    return read_json(state_path(p, task_id), None)


def save_state(p: Project, st: dict) -> None:
    write_json(state_path(p, st["id"]), st)


def all_states(p: Project) -> list[dict]:
    if not p.task_state_dir.is_dir():
        return []
    return [st for f in sorted(p.task_state_dir.glob("T-*.json")) if (st := read_json(f, None))]


def review_pid(p: Project, task_id: str) -> Path:
    """`orch review` 가 도는 동안 있는 pid 파일. 재시작 복구가 끊긴 리뷰와 진행 중 리뷰를 가른다."""
    return p.task_state_dir / task_id / "review.pid"


def worker_busy(st: dict) -> tuple[bool | None, str]:
    busy, desc, _ = worker_check(st)
    return busy, desc


def worker_check(st: dict) -> tuple[bool | None, str, dict | None]:
    """(워커가 아직 일하는가, 설명). True 일하는 중, False 끝남, None 확인 불가(호출자는 일하는 중으로 취급한다).

    stage 가 settled 면 터미널이 살아 있어도(retained) 턴은 끝났다. 그 밖엔 liveness 가 live·unverifiable 일 때만 일하는 중이다.
    orca 없음(12)은 그대로 올린다."""
    if not st.get("dispatch_id"):
        return None, "디스패치 없음", None
    info = orca.worker_info(st["dispatch_id"])
    if info is None:
        return None, "worker-show 실패", None
    stage, verdict = info["stage"], info["verdict"]
    desc = f"stage={stage}, liveness={verdict}"
    if stage == "settled":
        return False, desc, info
    if verdict is None:
        return None, desc, info
    return verdict in ("live", "unverifiable"), desc, info


def busy_or_unknown(st: dict) -> tuple[bool, str]:
    busy, desc = worker_busy(st)
    return busy is not False, desc


def launch_in_progress(st: dict) -> bool:
    """launch 가 아직 Orca 를 부르는 중인가. status 가 launch_failed 이고, 마지막 history 가 pending 이고, 그 pid 가 살아 있고,
    기록한 지 10분이 지나지 않았을 때다. 아니면 실행이 실패했거나 orch 가 도중에 죽은 것이다."""
    history = st.get("history")
    last = history[-1] if isinstance(history, list) and history else None
    if st.get("status") != "launch_failed" or not isinstance(last, dict) or last.get("pending") is not True:
        return False
    if not process_alive(last.get("pid")):
        return False
    try:
        age = (datetime.now().astimezone() - datetime.fromisoformat(last["at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age < LAUNCH_STALE_SECONDS


def launching_refusal(st: dict) -> str:
    pid = st["history"][-1]["pid"]
    return (f"{st['id']}: 워커를 띄우는 launch 가 아직 실행 중이다(orch pid {pid}). "
            "끝날 때까지 기다린 뒤 orch status 로 다시 본다")


def retry_command(st: dict) -> str:
    """launch_failed 태스크를 다시 띄우는 명령. 실패한 단계(work/fix)에 맞춘다."""
    return f"orch start {st['id']} --retry" if st.get("stage") == "work" else f"orch fix {st['id']} --retry"


def must_state(p: Project, task_id: str) -> dict:
    st = load_state(p, task_id)
    if not st:
        raise OrchError(f"{task_id} 는 시작된 적이 없다(orch start)", 2)
    return st


def max_task_number(p: Project) -> int:
    """`.orchestration/tasks/T-*.md` 의 가장 큰 번호. 없으면 0."""
    return max((int(m.group(1)) for f in p.tasks_dir.glob("T-*.md") if (m := re.match(r"T-(\d+)\.md$", f.name))),
               default=0)


def new_task(p: Project, cfg: Config, role: str, title: str) -> dict:
    require_coordinator(p)
    cfg.role(role)
    if not title.strip():
        raise OrchError("제목이 비었다", 2)
    p.tasks_dir.mkdir(parents=True, exist_ok=True)
    task_id = f"T-{max_task_number(p) + 1:03d}"
    text = render((TEMPLATES / "task.md").read_text(encoding="utf-8"),
                  {"id": task_id, "role": role, "title": title.strip()})
    task_path(p, task_id).write_text(text, encoding="utf-8")
    return {"id": task_id, "path": str(task_path(p, task_id).relative_to(p.root))}


def check_lock(p: Project, task_id: str, owns: list[str]) -> None:
    conflicts = []
    for st in all_states(p):
        if st["id"] == task_id or st.get("status") not in ACTIVE:
            continue
        hits = any_overlap(owns, st.get("owns", []))
        if hits:
            conflicts.append(f"{st['id']}({st.get('status')}): " + ", ".join(f"{a}~{b}" for a, b in hits))
    if conflicts:
        raise OrchError("소유 경로가 진행 중 태스크와 겹친다. 앞 태스크를 accept 한 뒤 시작한다: " + "; ".join(conflicts), 1)


def gate_block(p: Project, doc: TaskDoc) -> str:
    """[test] 가 있고 developer·tester 직무면 워커 spec 의 테스트 절. [test] 형식 오류는 리뷰 관문이 알리므로 빈 문자열."""
    try:
        cfg = testgate.load_config(p)
    except OrchError:
        return ""
    if cfg is None or doc.role not in testgate.GATED_ROLES:
        return ""
    block = TESTER_TEST_BLOCK if doc.role == testgate.TESTER_ROLE else TEST_BLOCK
    return render(block, {"task_id": doc.id})


def compose_spec(p: Project, cfg: Config, doc: TaskDoc, stage: str, rnd: int, owns: list[str], note: str | None) -> str:
    role_file = p.orch / "roles" / f"{doc.role}.md"
    role_doc = role_file.read_text(encoding="utf-8").strip() if role_file.is_file() else f"(직무 정의 없음: {role_file})"
    fix_block = ""
    if stage == "fix":
        review = f"docs/reviews/{doc.id}/r{rnd - 1}.md"  # 워커 실패 보고로 fix 하면 리뷰 파일이 없다
        review_line = render(REVIEW_LINE, {"review": review}) if (p.root / review).is_file() else ""
        fix_block = render(FIX_BLOCK, {"task_id": doc.id, "review_line": review_line, "note": note or "없음"})
    return render((TEMPLATES / "worker-spec.md").read_text(encoding="utf-8"), {
        "role": doc.role, "role_title": cfg.title(doc.role), "role_doc": role_doc, "task_id": doc.id,
        "round": rnd, "task_body": doc.body, "depends_on": ", ".join(deps_of(doc)) or "없음",
        "owns": ", ".join(owns), "fix_block": fix_block,
        "handoff_block": agents.handoff_block(p, cfg, doc.role),
        "test_block": gate_block(p, doc)})


def launch(p: Project, cfg: Config, doc: TaskDoc, st: dict, stage: str, rnd: int, owns: list[str],
           note: str | None, overrides: dict | None, extra: dict | None = None, agent: str | None = None) -> dict:
    """agent 가 [agents] 이름이면 그 고정 에이전트에, None 이면 임시 워커로 띄운다."""
    spec = compose_spec(p, cfg, doc, stage, rnd, owns, note)
    title = f"{doc.id} {stage} r{rnd} [{doc.role}] {doc.title}"
    fixed = cfg.agents[agent] if agent else None
    if fixed is None:
        pref = cfg.claude(doc.role, {**doc.front, **(overrides or {})})
        cmd = ["orchestration", "worker-start", "--spec", spec, "--task-title", title, "--worktree", "current",
               "--agent", "claude", "--model", pref.model, "--effort", pref.effort]
    # Orca 호출 전에 launch_failed 로 먼저 기록한다. orch 가 호출 중 죽어도 재실행이 --retry 게이트에 걸린다.
    # pid 는 orch status 가 실행 중인 launch 와 끊긴 launch 를 가르는 데 쓴다(launch_in_progress).
    st["history"].append({"stage": stage, "round": rnd, "at": now(), "ok": None, "pending": True, "pid": os.getpid()})
    st.update(status="launch_failed", stage=stage, round=rnd, owns=owns, agent=agent or agents.TEMP)
    save_state(p, st)
    try:
        if fixed is None:
            code, receipt = orca.call(cmd, check=False)
            fresh = None
        else:
            code, receipt, fresh = agents.start(p, fixed, spec, title)
    except OrchError:
        # 출력을 해석하지 못했다. 프로세스는 살아 있으므로 pending 을 지워 launching 으로 보이지 않게 한다.
        # 프로세스가 죽는 크래시는 여기에 오지 않아 pending 이 남는다(try/finally 를 쓰지 않는 이유).
        st["history"].pop()
        st["status"] = "launch_failed"
        save_state(p, st)
        raise
    st["history"].pop()
    log_dir = p.task_state_dir / doc.id
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (log_dir / f"{stage}-r{rnd}-{stamp}.spec.md").write_text(spec, encoding="utf-8")
    receipt_path = log_dir / f"{stage}-r{rnd}-{stamp}.receipt.json"
    write_json(receipt_path, receipt)
    ok = code == 0 and bool(receipt.get("ok"))
    entry = {"stage": stage, "round": rnd, "at": now(), "ok": ok,
             "task_id": orca.find_key(receipt, ("taskId",)), "dispatch_id": orca.find_key(receipt, ("dispatchId",)),
             "agent": agent or agents.TEMP, **(extra or {}),
             "launch": ((receipt.get("result") or {}).get("launch") or {}).get("effective"),
             "receipt": str(receipt_path.relative_to(p.root))}
    if note:
        entry["note"] = note
    if fixed is not None:
        entry["fresh"] = fresh
        if overrides or any(doc.front.get(k) for k in ("claude_model", "claude_effort")):
            entry["model_ignored"] = True  # 고정 에이전트의 모델은 띄울 때 정해진다(--terminal 은 --model 을 받지 않는다)
    st["history"].append(entry)
    st.update(stage=stage, round=rnd, owns=owns)
    if ok:
        st.update(status="working", task_id=entry["task_id"], dispatch_id=entry["dispatch_id"])
    else:
        st["status"] = "launch_failed"
    save_state(p, st)
    if ok and fixed is not None:
        agents.record_start(p, fixed, receipt, bool(fresh), doc.id)
    if not ok:
        raise OrchError("worker-start 실패. 같은 명령을 그냥 다시 돌리지 않는다. 영수증의 failedStage·residualResources 를 보고 "
                        f"Orca recovery 참조(orca skills get orchestration --reference references/recovery-and-cleanup.md)를 "
                        f"따른 뒤 --retry 로 다시 한다: {receipt_path}")
    return entry


def start_refusal(st: dict) -> str:
    task_id, status = st["id"], st.get("status")
    if status == "launch_failed":
        return (f"{task_id} 의 직전 {st.get('stage')} 실행이 실패했거나 실행 도중 끊겼다(launch_failed). 워커가 떠 있을 수 있다. "
                f"`orca orchestration worker-list` 로 확인하고, 남은 워커는 Orca recovery 참조({RECOVERY_REF})대로 정리한 뒤 "
                f"`{retry_command(st)}` 로 다시 한다")
    if status == "dropped":
        return f"{task_id} 는 중단(drop)된 태스크다. 다시 하려면 orch task new 로 새 태스크를 만든다"
    return f"{task_id} 는 이미 시작됐다(status={status}). 수정 라운드는 orch fix"


def start(p: Project, cfg: Config, task_id: str, retry: bool = False, overrides: dict | None = None) -> dict:
    require_coordinator(p)
    doc = load_doc(p, cfg, task_id)
    st = load_state(p, task_id)
    if st is not None and launch_in_progress(st):
        raise OrchError(launching_refusal(st), 2)
    fresh = st is None or st.get("status") == "pending"  # pending: plan apply 가 만들고 아직 띄우지 않은 태스크
    if not fresh and not (retry and st.get("status") == "launch_failed" and st.get("stage") == "work"):
        raise OrchError(start_refusal(st), 2)
    if fresh and retry:
        raise OrchError(f"{task_id}: --retry 할 실패 기록이 없다", 2)
    deps = deps_of(doc)
    unmet = unmet_deps(p, deps, doc.role, cfg.user_approval_roles)
    if unmet:
        raise OrchError(f"{task_id}: 선행 태스크가 끝나지(done) 않았다: {format_unmet(unmet)}. "
                        "끝나면 orch start-ready 가 띄운다", 2)
    owns = owns_of(doc, cfg)
    check_lock(p, task_id, owns)
    target = agents.place(p, cfg, doc.role)
    if target == agents.WAIT:
        agent = cfg.agent_for(doc.role)
        raise OrchError(f"{task_id}: {agent.title} 에이전트가 바쁘고 임시 워커가 꺼져 있다([overflow] enabled = false). "
                        "에이전트가 비면 orch start-ready 가 띄운다", 3)
    if fresh:
        # pending 상태의 plan_key 등은 남기고 실행 필드만 새로 쓴다.
        st = {**(st or {}), "id": task_id, "role": doc.role, "title": doc.title, "status": "new", "stage": "work",
              "round": 1, "base": git(p.root, "rev-parse", "HEAD").strip(), "owns": owns, "depends_on": deps,
              "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}}
    return launch(p, cfg, doc, st, "work", 1, owns, None, overrides, agent=None if target == agents.TEMP else target)


def check_supplement(p: Project, task_id: str, supplement: str) -> None:
    """`orch fix --supplement` 로 준 태스크가 끝난(done) director 태스크(디자인 시스템 보강)인지 확인한다."""
    from .plan import DIRECTOR_ROLE  # plan 이 이 모듈을 import 하므로 실행 시점에 읽는다

    st = load_state(p, supplement) if TASK_ID_RE.match(supplement) else None
    if not st:
        raise OrchError(f"{task_id}: --supplement {supplement} 의 상태가 없다. 끝난(done) director 보강 태스크 ID 를 준다", 2)
    if st.get("role") != DIRECTOR_ROLE or st.get("status") != "done":
        raise OrchError(f"{task_id}: --supplement 는 끝난(done) director 보강 태스크만 받는다"
                        f"({supplement}: role={st.get('role')}, status={st.get('status')})", 2)


def fix(p: Project, cfg: Config, task_id: str, note: str | None = None, retry: bool = False,
        overrides: dict | None = None, force: bool = False, supplement: str | None = None,
        extra_round: bool = False, from_queue: bool = False) -> dict:
    """수정 라운드. supplement 는 이 fix 가 반영하는 디자인 시스템 보강(director) 태스크 ID 다. 주면 이력에 supplement 로
    남고, awaiting_user 에서 해도 사용자 요청(user_requested)으로 남기지 않는다.

    고정 에이전트가 한 태스크는 같은 에이전트가 고친다(해제하지 않는다). 그 에이전트가 일하는 중이거나 앞선 수정이
    기다리면 대기열에 넣고 {"status": "queued"} 를 돌려준다. 에이전트가 비면 drain_fix_queues 가 from_queue 로 띄운다.
    extra_round 는 최대 라운드를 넘긴 태스크를 사용자가 한 번 더 고치기로 했을 때만 쓴다."""
    require_coordinator(p)
    doc = load_doc(p, cfg, task_id)
    st = must_state(p, task_id)
    status = st.get("status")
    if launch_in_progress(st):
        raise OrchError(launching_refusal(st), 2)
    if supplement is not None:
        if retry:
            raise OrchError(f"{task_id}: --supplement 는 --retry 와 함께 쓰지 않는다. --retry 는 실패한 fix 를 그대로 다시 띄운다", 2)
        check_supplement(p, task_id, supplement)
    if retry:
        if not (status == "launch_failed" and st.get("stage") == "fix"):
            raise OrchError(f"{task_id}: --retry 는 fix 실행이 실패한 뒤에만 쓴다(status={status})", 2)
        rnd = st["round"]
    else:
        if status not in ("changes", "working", "awaiting_user"):
            raise OrchError(f"{task_id}: fix 는 changes, working(워커 실패 보고), awaiting_user(사용자 수정 요청) "
                            f"상태에서만 한다(status={status})", 2)
        # 최대 라운드는 Codex 리뷰 반복을 끊고 사용자에게 묻기 위한 것이다. awaiting_user 의 fix 는 이미 사용자 요청이다.
        if status != "awaiting_user" and st["round"] >= cfg.max_rounds and not extra_round:
            raise OrchError(f"{task_id}: 최대 {cfg.max_rounds} 라운드에 도달했다. 메인이 사용자에게 묻고, 한 번 더 고치기로 "
                            f"하면 orch fix {task_id} --extra-round 로 한다", 1)
        rnd = st["round"] + 1
        # 리뷰를 거친 changes 의 워커는 이미 settled 다. working 은 워커가 아직 일하는 중일 수 있어 확인한다.
        if status == "working" and not force:
            busy, desc = busy_or_unknown(st)
            if busy:
                raise OrchError(f"{task_id}: 워커가 아직 일하고 있을 수 있다({desc}). fix 하면 이전 워커를 해제하고 같은 경로에 "
                                "새 워커를 띄워 동시에 고친다. worker_done 을 기다리거나 워커가 끝난 뒤 한다. "
                                "그래도 하려면 --force", 2)
    owns = owns_of(doc, cfg)
    check_lock(p, task_id, owns)
    agent = st.get("agent") if st.get("agent") in cfg.agents else None
    if agent and not from_queue and (agents.working(p, agent) or agents.entry(p, agent).get("queue")):
        st["queued_fix"] = {"note": note, "supplement": supplement, "extra_round": extra_round, "force": force,
                            "retry": retry, "at": now()}
        save_state(p, st)
        agents.enqueue(p, agent, task_id)
        return {"id": task_id, "status": "queued", "agent": agent}
    st.pop("queued_fix", None)
    if agent:
        extra: dict = {}  # 고정 에이전트는 해제하지 않는다(해제하면 작업 터미널이 닫힌다)
    else:
        # 임시 워커는 이전 디스패치를 해제하고 새 워커를 띄운다. awaiting_user 는 accept 때 이미 해제했다.
        extra = {} if status == "awaiting_user" and st.get("released") else release_previous(st)
    if not retry and status == "working":
        extra["forced"] = force
    if supplement is not None:
        extra["supplement"] = supplement
    elif not retry and status == "awaiting_user":
        extra["user_requested"] = True
    if extra_round:
        extra["extra_round"] = True
    return launch(p, cfg, doc, st, "fix", rnd, owns, note, overrides, extra, agent=agent)


def drain_fix_queues(p: Project, cfg: Config) -> list[dict]:
    """비어 있는 고정 에이전트마다 대기열 맨 앞의 수정 라운드를 띄운다(start_ready 가 먼저 부른다)."""
    out = []
    for name in cfg.agents:
        e = agents.entry(p, name)
        if e.get("task") or e.get("resetting") or not e.get("queue"):
            continue
        task_id = e["queue"][0]
        agents.dequeue(p, name, task_id)
        queued = (load_state(p, task_id) or {}).get("queued_fix")
        if not isinstance(queued, dict):
            continue
        try:
            entry = fix(p, cfg, task_id, queued.get("note"), bool(queued.get("retry")), None, bool(queued.get("force")),
                        queued.get("supplement"), bool(queued.get("extra_round")), from_queue=True)
        except OrchError as exc:
            if exc.code == 12:
                raise
            out.append({"id": task_id, "status": "error", "error": str(exc)})
            continue
        out.append({"id": task_id, "status": "working", "dispatch_id": entry.get("dispatch_id")})
    return out


def release_previous(st: dict) -> dict:
    """이전 디스패치를 최선 노력으로 해제하고 결과를 이력용 dict 로 돌려준다. 실패는 막지 않고 기록한다(orca 없음 12 만 중단)."""
    if not st.get("dispatch_id"):
        return {}
    try:
        code, data = orca.call(["orchestration", "worker-release", "--dispatch", str(st["dispatch_id"])], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return {"previous_released": False, "previous_release_error": str(exc)}
    released = code == 0 and bool(data.get("ok"))
    return {"previous_released": released} if released else {"previous_released": False, "previous_release_error": data}


def approval_roles(p: Project) -> list[str]:
    """사용자 승인 대상 직무. config.toml 을 아예 읽지 못하면(파일 없음·TOML 오류) accept 는 돌아야 하므로 키가 없을 때와
    같은 기본값([])을 쓴다. 읽히는데 user_approval_roles 가 틀리면(없는 직무, 목록 아님) 승인 게이트를 건너뛰지 않게 exit 2."""
    try:
        cfg = Config.load(p.config_path)
    except OrchError:
        return list(DEFAULT_APPROVAL_ROLES)
    try:
        return cfg.user_approval_roles
    except OrchError as exc:
        raise OrchError(f"{exc}. 사용자 승인 대상을 알 수 없어 accept 하지 않는다. config.toml 을 고친 뒤 다시 accept 한다", 2) from exc


def load_design_reviews(p: Project) -> list[dict]:
    """state/design-review.json 의 디자인 승인 질문 이력 [{"id", "tasks": {T-id: 라운드}, "at"}]."""
    data = read_json(p.design_review_json, {})
    asks = data.get("asks") if isinstance(data, dict) else None
    return [a for a in asks if isinstance(a, dict)] if isinstance(asks, list) else []


def accept(p: Project, task_id: str) -> dict:
    """APPROVE 된 태스크를 끝낸다. 사용자 승인 대상 직무는 먼저 awaiting_user 로 두고(잠금 유지),
    awaiting_user 에서 다시 accept 하면 사용자 승인으로 보고 done 이 된다. 임시 워커만 해제한다."""
    require_coordinator(p)
    st = must_state(p, task_id)
    status = st.get("status")
    if status == "awaiting_user":
        st.update(status="done", done_at=now(), user_approved_at=now())
        st["history"].append({"stage": "user_approval", "round": st.get("round"), "at": now()})
        save_state(p, st)
        return {"id": task_id, "status": "done", "user_approved": True}
    if status != "approved":
        raise OrchError(f"{task_id}: APPROVE 된 태스크(또는 사용자 승인 대기 awaiting_user)만 accept 한다"
                        f"(status={status})", 2)
    gated = st["role"] in approval_roles(p)  # 설정이 틀려 거부하면 워커를 해제하기 전에 멈춘다
    released, data = None, None
    if st.get("agent") in (None, agents.TEMP):
        code, data = orca.call(["orchestration", "worker-release", "--dispatch", str(st.get("dispatch_id"))], check=False)
        released = code == 0 and bool(data.get("ok"))
    if gated:
        st.update(status="awaiting_user", released=released, awaiting_since=now())
    else:
        st.update(status="done", released=released, done_at=now())
    save_state(p, st)
    result = {"id": task_id, "status": st["status"], "released": released}
    if released is False:
        result["release_error"] = data
    return result


def drop(p: Project, task_id: str, reason: str, force: bool = False) -> dict:
    """태스크를 중단한다. dropped 는 ACTIVE 가 아니라 소유 경로 잠금이 풀린다.

    working 태스크는 워커가 확실히 끝났을 때만 중단한다. worker-release 가 도는 워커를 멈춘다는 보장이 없어
    잠금을 풀면 다음 태스크와 같은 경로를 동시에 고칠 수 있다. --force 는 이 확인을 건너뛰고 이력에 남긴다."""
    require_coordinator(p)
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    reason = reason.strip()
    if not reason:
        raise OrchError("--reason 이 비었다", 2)
    pid = review_pid(p, task_id)
    acquire_pid(pid, f"orch review {task_id}")  # 진행 중 리뷰가 결과를 덮어쓰지 않게 리뷰와 직렬화한다
    try:
        st = must_state(p, task_id)
        if st.get("status") in FINISHED:
            raise OrchError(f"{task_id}: 이미 끝난 태스크다(status={st.get('status')})", 2)
        if launch_in_progress(st):
            raise OrchError(f"{task_id}: 워커를 띄우는 launch 가 아직 실행 중이다. 지금 drop 하면 launch 가 끝나며 "
                            "working 으로 덮어쓴다. orch status 로 launch 가 끝난 것을 확인한 뒤 다시 한다", 2)
        if st.get("status") == "working" and not force:
            busy, desc = busy_or_unknown(st)
            if busy:
                raise OrchError(f"{task_id}: 워커가 아직 일하고 있을 수 있다({desc}). drop 하면 소유 경로 잠금이 "
                                "풀려 다음 태스크와 같은 파일을 동시에 고칠 수 있다. 워커를 먼저 멈추거나 worker_done 또는 "
                                f"워커가 끝날(settled·exited 등) 때까지 기다린다. 그래도 중단하려면 --force", 2)
        released, release_error = None, None
        temp = st.get("agent") in (None, agents.TEMP)
        if not temp:
            agents.detach(p, st["agent"], task_id)  # 고정 에이전트는 해제하지 않는다
        elif st.get("status") == "awaiting_user" and st.get("released"):
            released = True  # accept 때 이미 해제했다(fix 와 같다)
        elif st.get("dispatch_id"):
            try:
                code, data = orca.call(["orchestration", "worker-release", "--dispatch", str(st["dispatch_id"])],
                                       check=False)
                released = code == 0 and bool(data.get("ok"))
                release_error = None if released else data
            except OrchError as exc:
                if exc.code == 12:
                    raise
                released, release_error = False, str(exc)
        st.update(status="dropped", released=released, dropped_at=now(), drop_reason=reason)
        st["history"].append({"stage": "drop", "round": st.get("round"), "at": now(), "reason": reason,
                              "released": released, "forced": force})
        save_state(p, st)
    finally:
        release_pid(pid)
    result = {"id": task_id, "status": "dropped", "released": released, "forced": force}
    if release_error is not None:
        result["release_error"] = release_error
    return result


def recover_unknown_start(p: Project, cfg: Config, task_id: str) -> dict:
    """working 태스크의 디스패치가 start_unknown(입력이 들어갔는지 모름)이면 디스패치를 정리하고 새 작업자로 다시 띄운다.

    늦게 입력이 들어가 같은 체크아웃에서 두 작업자가 일하지 않도록 그 터미널을 닫는다(worker-release). 고정 에이전트면
    작업 터미널을 잊고 새로 띄운다. 같은 태스크에서 UNKNOWN_START_LIMIT 번째면 다시 띄우지 않는다."""
    require_coordinator(p)
    doc = load_doc(p, cfg, task_id)
    st = must_state(p, task_id)
    dispatch = st.get("dispatch_id")
    info = orca.worker_info(dispatch) if dispatch else None
    if st.get("status") != "working" or not info or info.get("state") != "start_unknown":
        return {"id": task_id, "status": "not_unknown"}
    count = int(st.get("unknown_starts") or 0) + 1
    st["unknown_starts"] = count
    for verb in ("worker-abandon", "worker-release"):
        try:
            orca.call(["orchestration", verb, "--dispatch", str(dispatch)], check=False)
        except OrchError as exc:
            if exc.code == 12:
                raise
    agent = st.get("agent") if st.get("agent") in cfg.agents else None
    if agent:
        agents.forget(p, agent)
    st["history"].append({"stage": "recover", "round": st.get("round"), "at": now(), "dispatch_id": dispatch,
                          "reason": "start_unknown", "count": count})
    save_state(p, st)
    if count >= UNKNOWN_START_LIMIT:
        return {"id": task_id, "status": "escalate", "count": count}
    stage, rnd = st.get("stage") or "work", int(st.get("round") or 1)
    note = next((h.get("note") for h in reversed(st["history"])
                 if h.get("stage") == stage and h.get("round") == rnd and h.get("note")), None)
    entry = launch(p, cfg, doc, st, stage, rnd, owns_of(doc, cfg), note, None, {"recovered": "start_unknown"},
                   agent=agent)
    return {"id": task_id, "status": "relaunched", "dispatch_id": entry.get("dispatch_id"), "count": count}


def next_action(p: Project, cfg: Config | None, st: dict) -> str | None:
    """재시작한 코디네이터가 따를 다음 행동. 끝난 태스크는 None. cfg 가 없으면(설정 깨짐) 최대 라운드 검사를 뺀다."""
    task_id, status = st.get("id"), st.get("status")
    max_rounds = cfg.max_rounds if cfg is not None else None
    at_max = max_rounds is not None and int(st.get("round") or 1) >= max_rounds
    ask_then_drop = (f"최대 {max_rounds} 라운드라 fix 할 수 없다. orch notify --type question 으로 메인에 묻고, "
                     f"중단하기로 하면 orch drop {task_id} --reason \"<이유>\"")
    if status == "pending":
        dropped = [dep for dep in st.get("depends_on") or [] if (load_state(p, dep) or {}).get("status") == "dropped"]
        if dropped:
            return (f"선행 태스크 {', '.join(dropped)} 가 중단(dropped)돼 시작할 수 없다. orch notify --type question 으로 "
                    f"메인에 묻고, 진행하지 않기로 하면 orch drop {task_id} --reason \"<이유>\"")
        return "orch start-ready"
    if status == "awaiting_user":
        asks = load_design_reviews(p)
        last = asks[-1] if asks else {}
        asked = last.get("tasks") if isinstance(last.get("tasks"), dict) else {}
        if asked.get(task_id) == st.get("round"):
            return (f"사용자 승인을 기다린다(디자인 승인 질문 {last.get('id')}). 답이 오면 승인은 orch accept {task_id}, "
                    f"수정 요청은 orch fix {task_id} --note \"<사용자 요청>\"")
        return ("사용자 승인 대기. orch design-review 로 일괄 승인 질문을 올린다(승인 대상 태스크가 아직 리뷰 중이면 "
                "exit 2 로 무엇을 기다리는지 알려 준다)")
    if status == "working":
        try:
            busy, desc, info = worker_check(st)
        except OrchError:
            busy, desc, info = None, "orca 확인 불가", None
        if busy is False and (info or {}).get("stage") == "settled" and info.get("state") == "succeeded":
            return f"orch review {task_id} (Bash `run_in_background: true` 로 실행). 워커가 worker_done 없이 정상 종료했다({desc})"
        if busy is False:
            return f"워커가 worker_done 없이 끝났다({desc}). {ask_then_drop}" if at_max \
                else f'orch fix {task_id} --note "워커 종료"'
        if busy:
            return "워커의 worker_done 을 기다린다"
        return f"워커의 worker_done 을 기다린다(워커 생존 확인 불가: {desc})"
    if status == "reviewing":
        if pid_alive(review_pid(p, task_id)):
            return "리뷰가 진행 중이다. 끝나면 출력의 verdict 대로 accept 또는 fix 한다"
        return f"orch review {task_id} (Bash `run_in_background: true` 로 실행)"
    if status == "approved":
        return f"orch accept {task_id}"
    if status == "changes":
        return ask_then_drop if at_max else f"orch fix {task_id}"
    if status == "launch_failed":
        if launch_in_progress(st):
            return LAUNCHING_NEXT
        return (f"실행이 실패했거나 실행 도중 끊겼다. `orca orchestration worker-list` 로 이 태스크 워커가 떠 있는지 확인하고, "
                f"남은 워커는 Orca recovery 참조({RECOVERY_REF})대로 정리한다. 그다음 {retry_command(st)}. "
                f"복구할 수 없으면 orch drop {task_id} --reason \"<이유>\"")
    if status in FINISHED:
        return None
    return f"알 수 없는 상태({status}). .orchestration/state/tasks/{task_id}.json 을 확인한다"


def which(p: Project, ref: str) -> dict:
    for st in all_states(p):
        refs = {st.get("task_id"), st.get("dispatch_id")}
        for entry in st.get("history", []):
            refs.update((entry.get("task_id"), entry.get("dispatch_id")))
        if ref in refs:
            return {k: st.get(k) for k in ("id", "role", "title", "status", "round")}
    raise OrchError(f"{ref} 에 해당하는 태스크가 없다", 1)
