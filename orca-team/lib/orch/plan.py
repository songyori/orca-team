"""태스크 그래프. docs/planning/tasks.toml 을 검증해 태스크 spec 과 pending 상태로 만들고(plan apply),
시작할 수 있는 태스크를 동시 상한까지 띄우며(start-ready), 디자인을 모아 사용자 승인을 받는다(design-review)."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass

from . import agents, comm, testgate
from .ask import build_render
from .config import Config
from .globs import any_overlap
from .paths import OrchError, Project
from .tasks import (ACTIVE, FINISHED, all_states, check_lock, deps_of, drain_fix_queues, format_unmet,
                    load_design_reviews, load_doc, load_state, max_task_number, owns_of, parse_text, save_state, start,
                    task_path, unmet_deps, validate)
from .util import git, git_commit_paths, now, read_json, require_coordinator, write_json

PLAN_FILE = "docs/planning/tasks.toml"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TEXT_FIELDS = ("target", "change", "constraints")
REQUIRED = ("key", "role", "title", "owns", "target", "change", "constraints", "acceptance")
LINK_RE = re.compile(r"https://claude\.ai/[\w./-]*?artifact/[\w-]+", re.ASCII)
# design-review 가 기다리는 상태: 사용자 답 없이 awaiting_user 로 갈 진행 중 상태(잠금을 잡는 상태에서 awaiting_user 를 뺀 것).
# pending 은 곧 시작할 태스크만 기다린다(will_start).
REVIEW_WAIT_STATUSES = tuple(s for s in ACTIVE if s != "awaiting_user")


@dataclass
class PlanTask:
    key: str
    role: str
    title: str
    owns: list[str]
    depends_on: list[str]
    target: str
    change: str
    constraints: str
    acceptance: list[str]

    def digest(self) -> str:
        """적용한 key 가 바뀌었는지 가르는 지문(--append)."""
        raw = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _strings(value) -> list[str] | None:
    """문자열 목록이면 앞뒤 공백을 뺀 목록, 아니면 None."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return [v.strip() for v in value]


def parse_plan(data: dict, cfg: Config, rel: str = PLAN_FILE) -> list[PlanTask]:
    """tasks.toml 을 읽은 dict 의 필드를 검사한다. 어긴 곳을 모두 모아 exit 2 로 알린다. rel 은 오류 문구의 파일 이름이다."""
    items = data.get("task")
    if not isinstance(items, list) or not items:
        raise OrchError(f"{rel}: [[task]] 가 없다", 2)
    problems: list[str] = []
    tasks: list[PlanTask] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"task[{i}]: 표가 아니다")
            continue
        key = item.get("key")
        where = f"task[{i}]({key})" if isinstance(key, str) else f"task[{i}]"
        missing = [name for name in REQUIRED if name not in item]
        if missing:
            problems.append(f"{where}: 필수 필드 없음 {', '.join(missing)}")
            continue
        before = len(problems)
        if not isinstance(key, str) or not KEY_RE.match(key):
            problems.append(f"{where}: key 는 영문 소문자·숫자·하이픈")
        elif key in seen:
            problems.append(f"{where}: key 중복")
        else:
            seen.add(key)
        role = item["role"]
        if not isinstance(role, str) or role not in cfg.roles:
            problems.append(f"{where}: 알 수 없는 role {role!r}")
        title = item["title"].strip() if isinstance(item["title"], str) else ""
        if not title or "\n" in title:
            problems.append(f"{where}: title 은 비지 않은 한 줄")
        owns = _strings(item["owns"])
        if not owns or any(not o or "," in o or "\n" in o for o in owns):
            problems.append(f"{where}: owns 는 비지 않은 glob 목록(쉼표 금지)")
        deps = _strings(item.get("depends_on", []))
        if deps is None:
            problems.append(f"{where}: depends_on 은 key 목록")
        texts = {}
        for name in TEXT_FIELDS:
            value = item[name].strip() if isinstance(item[name], str) else ""
            if not value:
                problems.append(f"{where}: {name} 비었음")
            elif any(line.startswith("## ") for line in value.splitlines()):
                problems.append(f"{where}: {name} 에 `## ` 로 시작하는 줄을 넣지 않는다")
            texts[name] = value
        acceptance = _strings(item["acceptance"])
        if not acceptance or any(not a or "\n" in a for a in acceptance):
            problems.append(f"{where}: acceptance 는 비지 않은 한 줄 문자열 목록")
        if len(problems) == before:
            tasks.append(PlanTask(key, role, title, owns, deps, texts["target"], texts["change"],
                                  texts["constraints"], acceptance))
    if problems:
        raise OrchError(f"{rel} 검증 실패: " + "; ".join(problems), 2)
    return tasks


def find_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    """의존 순환이 있으면 그 경로(시작 key 로 끝난다), 없으면 None."""
    state: dict[str, int] = {}  # 1 방문 중, 2 끝남
    path: list[str] = []

    def visit(key: str) -> list[str] | None:
        state[key] = 1
        path.append(key)
        for dep in deps[key]:
            if state.get(dep) == 1:
                return path[path.index(dep):] + [dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        state[key] = 2
        path.pop()
        return None

    for key in deps:
        if key not in state:
            found = visit(key)
            if found:
                return found
    return None


def ancestors(deps: dict[str, list[str]]) -> dict[str, set[str]]:
    """key 마다 직간접 선행 key 집합. 순환이 없을 때만 부른다."""
    memo: dict[str, set[str]] = {}

    def up(key: str) -> set[str]:
        if key not in memo:
            found: set[str] = set()
            for dep in deps[key]:
                found |= {dep} | up(dep)
            memo[key] = found
        return memo[key]

    return {key: up(key) for key in deps}


def graph_problems(tasks: list[PlanTask], skip_pair=None) -> list[str]:
    """없는 선행 key, 의존 순환, 의존으로 이어지지 않아 동시에 돌 수 있는 두 태스크의 owns 겹침.

    skip_pair(a, b) 가 True 인 쌍은 겹침 검사에서 뺀다(--append 의 기존 태스크)."""
    keys = {t.key for t in tasks}
    problems = [f"{t.key}: depends_on 에 없는 key {', '.join(d for d in t.depends_on if d not in keys)}"
                for t in tasks if any(d not in keys for d in t.depends_on)]
    if problems:
        return problems
    deps = {t.key: t.depends_on for t in tasks}
    cycle = find_cycle(deps)
    if cycle:
        return [f"의존 순환: {' -> '.join(cycle)}"]
    above = ancestors(deps)
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            if a.key in above[b.key] or b.key in above[a.key]:
                continue
            if skip_pair is not None and skip_pair(a, b):
                continue
            hits = any_overlap(a.owns, b.owns)
            if hits:
                problems.append(f"{a.key}~{b.key}: 의존으로 이어지지 않았는데 owns 가 겹친다("
                                + ", ".join(f"{x}~{y}" for x, y in hits) + ")")
    return problems


DIRECTOR_ROLE = "director"
DESIGNER_ROLE = "designer"


def asset_only(t: PlanTask, cfg: Config) -> bool:
    """owns 가 모두 `assets/` 아래인(화면 없이 래스터 에셋만 만드는) 태스크. owns 가 없으면 config 의 기본 owns 로 본다.
    둘 다 비면 에셋 전용이 아니다(닫힌 쪽으로 실패해 director 규칙에 남긴다)."""
    owns = t.owns or cfg.role(t.role).get("owns") or []
    return bool(owns) and all(isinstance(o, str) and o.startswith("assets/") for o in owns)


def director_problems(tasks: list[PlanTask], cfg: Config, new_keys: set[str] | None = None) -> list[str]:
    """config 에 director 직무가 있고 designer 태스크가 있으면 director 태스크가 정확히 하나이고, designer 태스크가 모두
    그것에 직간접으로 의존해야 한다. new_keys 가 있으면(--append) 그 안의 designer 만 본다(적용한 태스크는 바꿀 수 없다).
    에셋만 만드는 designer 태스크(asset_only)는 보지 않는다. graph_problems 가 빈 목록(없는 key·순환 없음)일 때만 부른다."""
    if DIRECTOR_ROLE not in cfg.roles:
        return []
    designers = [t for t in tasks if t.role == DESIGNER_ROLE and (new_keys is None or t.key in new_keys)
                 and not asset_only(t, cfg)]
    if not designers:
        return []
    directors = [t.key for t in tasks if t.role == DIRECTOR_ROLE]
    if len(directors) != 1:
        return [f"designer 태스크가 있으면 director 태스크가 정확히 1개여야 한다(지금: {', '.join(directors) or '없음'}). "
                "디자인 방향(레퍼런스·톤 시안·디자인 시스템)은 director 태스크 하나가 화면 디자인보다 먼저 정한다"]
    director = directors[0]
    above = ancestors({t.key: t.depends_on for t in tasks})
    return [f"{t.key}: designer 태스크는 director 태스크({director})에 직접 또는 간접으로 의존해야 한다. "
            f'depends_on 에 "{director}" 를 넣는다' for t in designers if director not in above[t.key]]


def docs_only(t: PlanTask) -> bool:
    """owns 가 모두 README.md 이거나 docs/ 아래인 태스크(정책·사용자 문서). 묶음에 넣지 않아도 된다."""
    return all(o == "README.md" or o.startswith("docs/") for o in t.owns)


def group_problems(tasks: list[PlanTask], new_keys: set[str] | None = None) -> list[str]:
    """[test] 가 있을 때의 묶음 규칙. 묶음은 tester 태스크 하나와 그 depends_on 의 developer 태스크들이다.
    new_keys 가 있으면(--append) 그 안의 태스크만 본다. graph_problems 가 빈 목록일 때만 부른다."""
    by_key = {t.key: t for t in tasks}
    testers = [t for t in tasks if t.role == testgate.TESTER_ROLE]

    def fresh(t: PlanTask) -> bool:
        return new_keys is None or t.key in new_keys

    problems = []
    for t in testers:
        if not fresh(t):
            continue
        want = [f"tests/{t.key}/**", f"docs/qa/{t.key}.md"]
        if sorted(t.owns) != sorted(want):
            problems.append(f'{t.key}: tester 태스크 owns 는 정확히 ["{want[0]}", "{want[1]}"] 다')
        others = [d for d in t.depends_on if by_key[d].role != testgate.DEVELOPER_ROLE]
        if others:
            problems.append(f"{t.key}: tester 태스크의 depends_on 에는 developer 태스크만 둔다({', '.join(others)})")
    for t in tasks:
        if t.role != testgate.DEVELOPER_ROLE or not fresh(t):
            continue
        on_tester = [d for d in t.depends_on if by_key[d].role == testgate.TESTER_ROLE]
        if on_tester:
            problems.append(f"{t.key}: developer 태스크는 tester 태스크에 의존하지 않는다({', '.join(on_tester)}). "
                            "다음 묶음 구현이 앞 묶음 테스트를 기다리지 않게 한다")
        if docs_only(t):
            continue
        holders = [s.key for s in testers if t.key in s.depends_on]
        if len(holders) != 1:
            problems.append(f"{t.key}: 문서 전용이 아닌 developer 태스크는 정확히 한 tester 태스크(묶음)의 depends_on 에 "
                            f"든다(지금: {', '.join(holders) or '없음'})")
    return problems


def read_plan_data(p: Project, rel: str = PLAN_FILE) -> dict:
    path = p.root / rel
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OrchError(f"{rel} 이 없다. 기획 태스크가 만들어 커밋해야 한다", 2) from exc
    except (OSError, ValueError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
        raise OrchError(f"{rel} 읽기 실패: {exc}", 2) from exc


def read_plan(p: Project, cfg: Config, rel: str = PLAN_FILE) -> list[PlanTask]:
    return parse_plan(read_plan_data(p, rel), cfg, rel)


def load_plan_state(p: Project) -> dict:
    """state/plan.json: {"tasks": {key: T-id}, "digests": {key: 지문}}. 없거나 깨졌으면 빈 계획."""
    data = read_json(p.plan_json, {})
    data = data if isinstance(data, dict) else {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
    digests = data.get("digests") if isinstance(data.get("digests"), dict) else {}
    return {"tasks": tasks, "digests": digests}


def check(p: Project, cfg: Config, append: bool, rel: str = PLAN_FILE) -> tuple[list[PlanTask], list[PlanTask]]:
    """(tasks.toml 의 모든 태스크, 새로 만들 태스크). 어긴 곳이 있으면 모두 모아 exit 2."""
    state = load_plan_state(p)
    applied, digests = state["tasks"], state["digests"]
    if applied and not append:
        raise OrchError("계획을 이미 적용했다(state/plan.json). 새 태스크만 더하려면 orch plan apply --append", 2)
    if append and not applied:
        raise OrchError("아직 적용한 계획이 없다. 처음에는 --append 없이 orch plan apply", 2)
    data = read_plan_data(p, rel)
    tasks = parse_plan(data, cfg, rel)
    keys = {t.key for t in tasks}
    problems = []
    removed = [key for key in applied if key not in keys]
    if removed:
        problems.append(f"적용한 key 가 빠졌다: {', '.join(removed)}")
    changed = [t.key for t in tasks if t.key in applied and digests.get(t.key) != t.digest()]
    if changed:
        problems.append(f"적용한 key 를 바꿨다: {', '.join(changed)}. 기존 태스크는 그대로 두고 새 key 만 더한다")

    if "test" in data:
        problems += testgate.config_problems(data["test"])

    def settled(a: PlanTask, b: PlanTask) -> bool:
        """--append: 둘 다 기존 태스크이거나, 기존 쪽이 이미 끝났으면(done·dropped) 겹쳐도 된다."""
        old = [t for t in (a, b) if t.key in applied]
        if len(old) == 2:
            return True
        return any((load_state(p, applied[t.key]) or {}).get("status") in FINISHED for t in old)

    graph = graph_problems(tasks, settled)
    problems += graph
    if not graph:
        new_keys = {t.key for t in tasks if t.key not in applied}
        problems += director_problems(tasks, cfg, new_keys)
        if "test" in data:
            problems += group_problems(tasks, new_keys)
    if problems:
        raise OrchError(f"{rel} 검증 실패: " + "; ".join(problems), 2)
    return tasks, [t for t in tasks if t.key not in applied]


def parallel_groups(tasks: list[PlanTask]) -> list[list[str]]:
    """선행 깊이별 key 묶음. 같은 묶음은 서로 기다리지 않고 병렬로 갈 수 있다(검증을 통과한 비순환 그래프 전제)."""
    deps = {t.key: t.depends_on for t in tasks}
    depth: dict[str, int] = {}

    def level(key: str) -> int:
        if key not in depth:
            depth[key] = 1 + max((level(d) for d in deps[key]), default=-1)
        return depth[key]

    groups: list[list[str]] = []
    for t in tasks:
        n = level(t.key)
        while len(groups) <= n:
            groups.append([])
        groups[n].append(t.key)
    return groups


def check_only(p: Project, cfg: Config, rel: str | None = None) -> dict:
    """읽기 전용 검사. plan apply 와 같은 검증을 하고 아무것도 쓰지 않는다. 이미 적용한 계획이 있으면 --append 규칙으로 본다."""
    tasks, new = check(p, cfg, bool(load_plan_state(p)["tasks"]), rel or PLAN_FILE)
    roles: dict[str, int] = {}
    for t in tasks:
        roles[t.role] = roles.get(t.role, 0) + 1
    return {"ok": True, "file": rel or PLAN_FILE, "tasks": len(tasks), "new": len(new), "roles": roles,
            "groups": parallel_groups(tasks), "test": "test" in read_plan_data(p, rel or PLAN_FILE)}


def spec_text(t: PlanTask, task_id: str, dep_ids: list[str]) -> str:
    """orch task new 와 같은 형식의 태스크 spec. front matter 에 owns·depends_on·plan_key 를 넣는다."""
    lines = ["---", f"id: {task_id}", f"role: {t.role}", f"title: {t.title}", f"owns: {', '.join(t.owns)}",
             f"depends_on: {', '.join(dep_ids)}".rstrip(), f"plan_key: {t.key}", "---",
             "## Target", t.target, "", "## Change", t.change, "", "## Constraints", t.constraints, "",
             "## Acceptance", *(f"- {a}" for a in t.acceptance)]
    return "\n".join(lines) + "\n"


def refuse_lost_plan_state(p: Project) -> None:
    """state/plan.json 이 없거나 깨졌는데 plan apply 로 만든(plan_key 가 있는) 태스크 상태가 있으면 exit 2.
    그대로 적용하면 같은 key 의 태스크가 새 번호로 중복된다."""
    if load_plan_state(p)["tasks"]:
        return
    made = [st["id"] for st in all_states(p) if st.get("plan_key")]
    if made:
        raise OrchError(f"state/plan.json 이 없거나 깨졌는데 plan apply 로 만든 태스크가 있다({', '.join(made)}). "
                        "다시 적용하면 같은 태스크가 새 번호로 중복되므로 plan apply 를 하지 않는다. "
                        "orch notify --type question --json - 으로 메인에 알려 plan.json 을 복구하게 한다", 2)


def apply(p: Project, cfg: Config, append: bool = False) -> dict:
    """tasks.toml 의 새 태스크를 spec 과 pending 상태로 만든다. 검증에 하나라도 실패하면 아무것도 만들지 않는다.
    spec 은 경로 지정 커밋 하나(`[plan] apply: N tasks`)로 묶는다. 잠금은 잡지 않는다(pending 은 ACTIVE 가 아니다)."""
    require_coordinator(p)
    refuse_lost_plan_state(p)
    _, new = check(p, cfg, append)
    if not new:
        raise OrchError(f"{PLAN_FILE} 에 더할 새 key 가 없다", 2)
    applied = load_plan_state(p)
    first = max_task_number(p) + 1
    ids = {**applied["tasks"], **{t.key: f"T-{first + i:03d}" for i, t in enumerate(new)}}
    specs = []
    for t in new:
        task_id, dep_ids = ids[t.key], [ids[d] for d in t.depends_on]
        text = spec_text(t, task_id, dep_ids)
        validate(parse_text(text, f"{PLAN_FILE}({t.key})"), cfg, task_id)
        specs.append((t, task_id, dep_ids, text))
    p.tasks_dir.mkdir(parents=True, exist_ok=True)
    rels = [str(task_path(p, task_id).relative_to(p.root)) for _, task_id, _, _ in specs]
    try:
        for _, task_id, _, text in specs:
            task_path(p, task_id).write_text(text, encoding="utf-8")
        git_commit_paths(p.root, rels, f"[plan] apply: {len(specs)} tasks")
    except BaseException:
        # 커밋하지 못한 spec 을 남기면 다음 apply 가 번호를 건너뛴다. 인덱스와 파일에서 지운다.
        git(p.root, "rm", "-q", "--cached", "--ignore-unmatch", "--", *rels, check=False)
        for _, task_id, _, _ in specs:
            task_path(p, task_id).unlink(missing_ok=True)
        raise
    write_json(p.plan_json, {"tasks": ids, "digests": {**applied["digests"], **{t.key: t.digest() for t in new}}})
    for t, task_id, dep_ids, _ in specs:
        save_state(p, {"id": task_id, "role": t.role, "title": t.title, "status": "pending", "stage": "work",
                       "round": 1, "base": None, "owns": t.owns, "depends_on": dep_ids, "plan_key": t.key,
                       "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}})
    return {"applied": len(specs), "commit": git(p.root, "rev-parse", "HEAD").strip(),
            "tasks": [{"key": t.key, "id": task_id, "role": t.role, "title": t.title, "depends_on": dep_ids}
                      for t, task_id, dep_ids, _ in specs]}


def plan_order(p: Project) -> list[str]:
    """시작 후보 순서: tasks.toml 순서(plan.json 에 적용 순서대로 남는다), 그 밖의 pending 태스크는 ID 순."""
    ordered = list(load_plan_state(p)["tasks"].values())
    extra = sorted(st["id"] for st in all_states(p) if st.get("status") == "pending" and st["id"] not in ordered)
    return ordered + extra


def start_ready(p: Project, cfg: Config) -> dict:
    """pending 태스크 중 선행 태스크가 모두 충족되고(deps. done, 승인 대상 직무끼리는 awaiting_user 도), 잠금이 겹치지 않고
    (lock), working 수가 max_parallel 보다 적은(limit) 것을 tasks.toml 순서로 띄운다. 한 태스크의 실행 실패는 나머지를 막지 않는다.
    waiting reason: spec, deps, lock, agent(고정 에이전트 대기), limit."""
    require_coordinator(p)
    fixes = drain_fix_queues(p, cfg)
    limit = cfg.max_parallel
    approval = cfg.user_approval_roles
    running = sum(1 for st in all_states(p) if st.get("status") == "working")
    started, waiting = [], []
    for task_id in plan_order(p):
        if (load_state(p, task_id) or {}).get("status") != "pending":
            continue
        try:
            doc = load_doc(p, cfg, task_id)
            owns = owns_of(doc, cfg)
        except OrchError as exc:
            waiting.append({"id": task_id, "reason": "spec", "detail": str(exc)})
            continue
        unmet = unmet_deps(p, deps_of(doc), doc.role, approval)
        if unmet:
            waiting.append({"id": task_id, "reason": "deps", "detail": format_unmet(unmet)})
            continue
        try:
            check_lock(p, task_id, owns)
        except OrchError as exc:
            waiting.append({"id": task_id, "reason": "lock", "detail": str(exc)})
            continue
        if agents.place(p, cfg, doc.role) == agents.WAIT:
            waiting.append({"id": task_id, "reason": "agent", "detail": cfg.agent_for(doc.role).title})
            continue
        if running >= limit:
            waiting.append({"id": task_id, "reason": "limit"})
            continue
        try:
            entry = start(p, cfg, task_id)
        except OrchError as exc:
            if exc.code == 12:
                raise
            if exc.code == 3:
                waiting.append({"id": task_id, "reason": "agent", "detail": str(exc)})
                continue
            started.append({"id": task_id, "status": "launch_failed", "error": str(exc)})
            continue
        started.append({"id": task_id, "status": "working", "dispatch_id": entry.get("dispatch_id")})
        running += 1
    return {"started": started, "waiting": waiting, "running": running, "max_parallel": limit, "fixes": fixes}


def design_system_url(p: Project) -> str | None:
    """`docs/design/direction.md` 의 `design_system_url:` 줄에 적힌 프로젝트 디자인 시스템 아티팩트 주소. 없으면 None."""
    try:
        text = (p.root / "docs" / "design" / "direction.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        if line.startswith("design_system_url:"):
            m = LINK_RE.search(line)
            return m.group(0) if m else None
    return None


def design_link(p: Project, task_id: str) -> str | None:
    """`docs/tasks/T-xxx/report.md` 의 마지막 claude.ai 아티팩트 주소. 리포트는 라운드마다 절을 더하므로 마지막이 최신이다.
    designer 는 화면 주소 뒤에 프로젝트 디자인 시스템 주소도 적으므로 `design_system_url` 과 같은 주소는 건너뛴다."""
    try:
        text = (p.root / "docs" / "tasks" / task_id / "report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    system = design_system_url(p)
    found = [u for u in LINK_RE.findall(text) if u != system]
    return found[-1] if found else None


def will_start(p: Project, st: dict, approval, states: list[dict]) -> bool:
    """pending 태스크가 사용자 답 없이 시작될 수 있는가. 선행 태스크가 충족됐고(unmet_deps), awaiting_user 태스크와
    소유 경로가 겹치지 않아야 한다. 겹치면 그 태스크가 사용자 승인으로 done 이 된 뒤에야 시작하므로 기다리면 교착이다."""
    if unmet_deps(p, st.get("depends_on") or [], st.get("role"), approval):
        return False
    held = [o for o in states if o.get("status") == "awaiting_user" and o.get("id") != st.get("id")]
    return not any(any_overlap(st.get("owns") or [], o.get("owns") or []) for o in held)


def design_review(p: Project, cfg: Config) -> dict:
    """승인 대상 직무 태스크가 모두 Codex 리뷰를 지나 awaiting_user 가 됐으면 메인에 승인 질문 하나를 올린다.

    진행 중(REVIEW_WAIT_STATUSES)이거나 곧 시작할 pending(will_start) 승인 대상 태스크가 있으면 기다린다. 선행 태스크가
    충족되지 않은 pending 태스크(예: 아직 끝나지 않은 개발 태스크를 기다리는 디자인)는 기다리지 않는다."""
    require_coordinator(p)
    roles = cfg.user_approval_roles
    everything = all_states(p)
    states = [st for st in everything if st.get("role") in roles]
    busy = [f"{st['id']}({st.get('status')})" for st in states
            if st.get("status") in REVIEW_WAIT_STATUSES
            or (st.get("status") == "pending" and will_start(p, st, roles, everything))]
    if busy:
        raise OrchError("승인 대상 태스크가 아직 끝나지 않았다: " + ", ".join(busy)
                        + ". 모두 Codex 리뷰를 거쳐 awaiting_user 가 되면 다시 한다(pending 은 orch start-ready 가 띄운다)", 2)
    ready = [st for st in states if st.get("status") == "awaiting_user"]
    if not ready:
        raise OrchError("사용자 승인을 기다리는(awaiting_user) 태스크가 없다"
                        f"(승인 대상 직무: {', '.join(roles) or '없음'})", 2)
    rounds = {st["id"]: st.get("round") for st in ready}
    asks = load_design_reviews(p)
    if asks and asks[-1].get("tasks") == rounds:
        last = asks[-1].get("id")
        raise OrchError(f"같은 디자인을 이미 물었다({last}). 메인의 답(`[main] reply {last}`)을 기다린다", 3)
    links, lines = {}, []
    for st in ready:
        links[st["id"]] = design_link(p, st["id"])
        missing = f"(링크 없음: docs/tasks/{st['id']}/report.md)"
        lines.append(f"- {st['id']} {st.get('title')}: {links[st['id']] or missing}")
    question = f"디자인 {len(ready)}개를 확인해 주세요"
    # 화면별 링크 목록은 context 로 싣는다. 메인이 질문 전에 평문으로 그대로 보여 준다.
    render = build_render({
        "header": "디자인", "question": question, "context": "\n".join(lines),
        "example": "위 링크를 화면마다 열어 본다. 고칠 화면은 Other 에 화면과 내용을 적는다",
        "options": [{"label": "전부 승인", "description": "모든 화면을 승인하고 다음 작업을 이어 간다"},
                    {"label": "수정 요청(화면과 내용을 적어 주세요)", "description": "고칠 화면과 내용은 Other 로 적는다"}],
        "recommended": 0, "reason": "모두 Codex 리뷰를 통과했다"})
    entry = comm.post(p, "approval", question, {"tasks": list(rounds), "render": render})
    asks.append({"id": entry["id"], "tasks": rounds, "at": now()})
    write_json(p.design_review_json, {"asks": asks})
    return {"id": entry["id"], "tasks": list(rounds), "links": links}
