"""리뷰 파일의 `## 후속`·`## 기획 누락`·`## 회귀` 항목을 모은다(읽기 전용).
코디네이터가 이것으로 회귀 수정·묶음 정리 태스크와 사용자 질문을 만든다."""
from __future__ import annotations

import re
from pathlib import Path

from .paths import OrchError, Project
from .tasks import TASK_ID_RE, all_states, load_state, must_state, parse_task, task_path
from .testgate import DEVELOPER_ROLE, TESTER_ROLE

SECTIONS = (("regressions", "회귀"), ("followups", "후속"), ("planning_gaps", "기획 누락"))
# 없음·해당 없음으로 시작하고 뒤가 끝·공백·괄호·구두점인 항목은 빈 항목이다(예: `해당 없음(묶음 리뷰 아님)`).
EMPTY_RE = re.compile(r"^\(?\*{0,2}(?:해당 ?없음|없음)\*{0,2}\)?(?:$|[\s(（).,;:!?·—–-])")
ITEM_ID_RE = re.compile(r"^(r\d+-F\d+)\b")


def is_empty(item: str) -> bool:
    """`rN-Fk` id 가 붙은 항목은 빈 항목이 아니다. id 가 없을 때만 없음·해당 없음·해당없음·**없음** 을 빈 항목으로 본다."""
    return not ITEM_ID_RE.match(item) and bool(EMPTY_RE.match(item))
SOURCE_RE = re.compile(r"^출처: (T-\d{3,}|rest|final) followups\s*$")


def section_items(text: str, title: str) -> list[str]:
    """`## {title}` 절의 `- ` 항목. 들여 쓴 다음 줄은 앞 항목에 잇는다. 없음·해당 없음(뒤에 사유가 붙어도)은 뺀다."""
    match = re.search(rf"^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |^VERDICT:|\Z)", text, re.M | re.S)
    if not match:
        return []
    items: list[str] = []
    for line in match.group(1).splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1] += " " + line.strip()
    return [item for item in items if item and not is_empty(item)]


def latest_review(p: Project, task_id: str) -> tuple[int, Path] | None:
    """판정이 남은 마지막 라운드의 리뷰 파일."""
    st = load_state(p, task_id) or {}
    rounds = sorted((int(k) for k in (st.get("verdicts") or {}) if str(k).isdigit()), reverse=True)
    for rnd in rounds:
        path = p.root / f"docs/reviews/{task_id}/r{rnd}.md"
        if path.is_file():
            return rnd, path
    return None


def from_followups(p: Project, task_id: str) -> bool:
    """후속 처리로 만든 태스크인가(spec `## Change` 첫 줄이 `출처: … followups`). spec 을 읽지 못하면 아니라고 본다
    (spec 하나가 깨져도 orch followups 전체가 멈추지 않게)."""
    path = task_path(p, task_id)
    if not path.is_file():
        return False
    try:
        doc = parse_task(path)
    except (OrchError, OSError, UnicodeDecodeError):
        return False
    lines = [line.strip() for line in doc.sections.get("Change", "").splitlines() if line.strip()]
    return bool(lines) and bool(SOURCE_RE.match(lines[0]))


def collect(p: Project, task_ids: list[str]) -> dict:
    out: dict[str, list] = {key: [] for key, _ in SECTIONS}
    for task_id in task_ids:
        found = latest_review(p, task_id)
        if not found:
            continue
        rnd, path = found
        text = path.read_text(encoding="utf-8")
        derived = from_followups(p, task_id)
        for key, title in SECTIONS:
            if key == "followups" and derived:  # 정리 태스크가 다시 정리 태스크를 낳지 않게 한다
                continue
            for item in section_items(text, title):
                match = ITEM_ID_RE.match(item)
                out[key].append({"task": task_id, "round": rnd, "id": match.group(1) if match else None,
                                 "text": item})
    return out


def group_members(p: Project, task_id: str) -> list[str]:
    """tester 태스크면 그 묶음(developer 선행 태스크와 tester 태스크), 아니면 그 태스크만."""
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = must_state(p, task_id)
    if st.get("role") != TESTER_ROLE:
        return [task_id]
    deps = [d for d in st.get("depends_on", []) if (load_state(p, d) or {}).get("role") == DEVELOPER_ROLE]
    return [*deps, task_id]


def rest_members(p: Project) -> list[str]:
    """어떤 tester 태스크의 depends_on 에도 없는 done developer 태스크."""
    states = all_states(p)
    grouped = {dep for st in states if st.get("role") == TESTER_ROLE for dep in st.get("depends_on", [])}
    return [st["id"] for st in states
            if st.get("role") == DEVELOPER_ROLE and st.get("status") == "done" and st["id"] not in grouped]


def followups(p: Project, task_id: str | None = None, rest: bool = False) -> dict:
    ids = rest_members(p) if rest else group_members(p, task_id)
    return {"tasks": ids, **collect(p, ids)}
