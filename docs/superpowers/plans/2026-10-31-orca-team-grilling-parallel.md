# orca-team 그릴링 기획·병렬 실행 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기획 워커가 메인을 거쳐 사용자를 그릴링 인터뷰(`orch ask-user`/`orch answer`)해 기획·기술 설계·태스크 그래프를 확정하고, orch 가 그 그래프(`docs/planning/tasks.toml`)대로 태스크를 `max_parallel`까지 병렬로 띄우며(`orch plan apply`, `orch start-ready`), 디자인은 모아서 한 번에 사용자 승인(`orch design-review`)을 받게 한다.

**Architecture:** 새 모듈 `lib/orch/ask.py`(질문 스키마·render·답 파일)와 `lib/orch/plan.py`(tasks.toml 검증·적용, start-ready, design-review)를 더하고, `tasks.py`에 `pending`·`awaiting_user` 상태와 `depends_on`, accept 승인 게이트를 넣는다. 기획 워커 → 메인 질문은 outbox(`type: "ask"`)로, 메인 → 기획 워커 답은 `state/answers/<out-id>.json` 파일로 흐르므로 코디네이터와 Run 을 거치지 않는다. 템플릿(planner·coordinator·CLAUDE.section·worker-spec·config)이 새 흐름을 지시한다.

**Tech Stack:** Python 3.11+ 표준 라이브러리(`tomllib`, `argparse`, `unittest`, `hashlib`), Orca CLI 1.4.x, 내장 Codex 래퍼, Claude Code 플러그인(templates, skills).

**설계 문서:** `docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md`(요구사항), `docs/superpowers/specs/2026-10-30-orca-team-design.md`(기반 설계 §4–§8, §13)

## Global Constraints

- Python 3.11 이상, 표준 라이브러리만 쓴다. 외부 패키지를 설치하지 않는다. f-string 안에 바깥과 같은 따옴표를 겹쳐 쓰지 않는다(3.11 문법).
- 모든 Orca 호출은 `lib/orch/orca.py`의 `call()`을 거치고 `--json`을 붙인다.
- 워커 배치는 항상 `orca orchestration worker-start --worktree current --agent claude --model <M> --effort <E>`다. 수정 라운드(fix)는 이전 디스패치를 해제하고 새 워커를 띄운다.
- Codex 호출은 플러그인에 들어 있는 `orca-team/scripts/codex-run.sh`(내장 래퍼)로만 한다.
- 종료 코드: 0 성공, 1 실패, 2 인자·상태 오류, 3 중복, 4 ask-user 답 대기 중(시간 초과, 결과 JSON 과 함께), 12 의존성 없음.
- 코디네이터 전용 명령(`bind`, `wait`, `task new`, `start`, `fix`, `review`, `accept`, `drop`, `plan apply`, `start-ready`, `design-review`)은 `util.require_coordinator(p)`로 보호한다. `ask-user`(기획 워커)와 `answer`(메인)는 코디네이터 확인을 하지 않는다.
- 메인은 어떤 Run 에도 묶이지 않는다. 메인이 부르는 orch 명령은 `orca orchestration send --to run:<id>`(`comm.tell`) 외의 Orca 호출을 하지 않는다.
- config.toml 이 필요한 명령만 `cli.CONFIG_COMMANDS`에 넣는다. 소통·조회·정리 명령(`tell`, `watch`, `notify`, `wait`, `which`, `accept`, `drop`, `ask-user`, `answer`)은 설정이 깨져도 돈다.
- 문서·주석·사용자 메시지는 한국어로 쓴다. 식별자·명령·경로는 원문 그대로 둔다.
- 커밋은 경로를 지정해서만 한다(`git add -- <경로> && git commit -m "..." -- <경로>`). orch 가 만드는 커밋은 `util.git_commit_paths`를 쓴다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다.
- 테스트(저장소 루트에서): 전체 `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`, 일부 `cd orca-team/tests && python3 -m unittest <모듈>[.<클래스>[.<메서드>]] -v`. 테스트는 가짜 orca(`tests/fakes/orca`, 응답은 `ProjectCase.respond`)와 가짜 Codex 래퍼만 쓴다.

## 설계 대비 결정

설계가 열어 둔 부분은 기존 코드와 가장 잘 맞는 쪽으로 정했다. Task 7 에서 기반 설계 §13 에 요약해 남긴다.

1. **ask-user 입출력.** `--json -`(표준 입력)도 받는다. 기획 지침은 heredoc 을 써서 질문 파일을 저장소에 남기지 않는다. "한 문장"은 줄바꿈 없음으로 검사한다. 허용 조건(working 상태 planner)은 `--wait`·`--pending`에도 적용한다. "읽음" 기록은 `state/answers/<out-id>.read` 표시 파일이다. `--pending`은 돌려준 답을 읽음으로 기록하고, 아직 답이 없는 질문 id 를 `waiting`에 함께 준다(§9 복구용). 대기 초는 테스트용 환경변수 `ORCA_TEAM_ASK_WAIT`로 줄일 수 있다(기본 540). `render`는 선택지가 있을 때 `multiSelect: false`를 싣고, 추천 사유 끝 마침표는 한 번만 붙인다.
2. **질문 `context` 필드(확정 질문 요약).** 300자 `example`로는 계획 전체를 승인받기에 부족하다. 그래서 질문 스키마에 선택 필드 `context`(마크다운, 4000자 이하, 그 밖의 제약 없음)를 더하고 `render.context`로 싣는다. 메인은 `render.context`가 있으면 그것을 먼저 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부르고, 자기 설명은 덧붙이지 않는다. 기획 워커의 확정 질문은 요약(기획 핵심, 자동 결정 목록, 태스크 그래프 표)을 `context`에 담고 `example`은 짧게 둔다. `design-review`도 화면별 링크 목록을 `example` 대신 `context`에 담는다. 기획 워커는 확정 질문 전에 `docs/planning/` 네 파일을 써 두고 확정 뒤 커밋한다.
3. **tasks.toml 필드와 plan.json.** `depends_on`만 생략할 수 있다(빈 목록). front matter 가 한 줄·쉼표 구분이라 title 은 한 줄, owns 항목에 쉼표 금지, 본문 필드에 `## `로 시작하는 줄 금지. `state/plan.json`은 `{"tasks": {key: T-id}, "digests": {key: sha1}}`이고 digest 로 `--append`의 기존 key 변경을 가린다. `--append`는 기존 key 변경·삭제와 "새 key 없음"을 거부하고, owns 겹침은 새 태스크가 낀 쌍만 보며 이미 done·dropped 인 기존 태스크와의 겹침은 허용한다. 커밋이 실패하면 쓴 spec 을 인덱스와 파일에서 지운다(번호 재사용).
4. **tasks.toml 검증을 리뷰 사전 검사에도 둔다.** accept 뒤 기획 태스크는 `done`이라 `orch fix`로 되돌릴 수 없어 §9 의 "plan apply 실패 → fix" 경로가 성립하지 않는다. 그래서 커밋이 tasks.toml 을 바꾼 태스크는 `orch review`가 plan apply 와 같은 규칙(`plan.check`)으로 먼저 검사해 Codex 없이 REQUEST_CHANGES 로 돌린다. accept 뒤 apply 가 실패하면 새 기획 태스크로 고친다.
5. **start-ready 세부.** 검사 순서는 deps → lock → limit. spec 을 읽지 못한 태스크는 reason `spec`으로 waiting 에 넣는다. `started` 항목마다 `status`(`working`|`launch_failed`)를 싣고, 실패가 하나라도 있으면 exit 1. 단건 `orch start`는 선행 태스크만 검사하고 `max_parallel`은 보지 않는다(코디네이터 수동 개입용). 선행 태스크가 dropped 인 pending 태스크의 `next`는 메인에 묻고 drop 하라는 안내다(그래프가 조용히 멈추지 않게).
6. **awaiting_user 의 fix·accept.** `awaiting_user`에서 하는 fix 는 최대 라운드 검사를 하지 않는다(라운드 번호는 올린다). 최대 라운드는 사용자에게 묻기 위한 장치인데 이 fix 는 이미 사용자 요청이기 때문이다. accept 때 해제한 워커는 다시 해제하지 않는다. config.toml 이 깨져 승인 대상 직무를 읽지 못하면 accept 는 기본값 `["designer"]`로 판정한다(설정이 깨져도 accept 가 도는 기존 규칙).
7. **design-review 세부.** 마지막 질문과 같은 태스크·라운드를 다시 물으면 exit 3(중복). 링크는 report.md 의 마지막 claude.ai 아티팩트 주소이고 없으면 `(링크 없음: docs/tasks/T-xxx/report.md)`. 화면별 링크 목록은 `render.context`(`- T-xxx 제목: 링크` 줄)에 싣는다. orch 가 직접 만드는 질문이라 스키마 검증을 거치지 않는다(화면 수만큼 줄이 늘어난다). `state/design-review.json`은 질문 이력 `{"asks": [{"id", "tasks": {T-id: round}, "at"}]}`다.
8. **worker spec 의 선행 태스크 줄.** 워커 spec 의 태스크 본문에는 front matter 가 빠지므로 `선행 태스크: T-aaa, T-bbb.` 줄을 넣어 `depends_on`을 보여 준다(설계 §8 의 worker-spec 한 줄). `compose_spec`이 채우므로 Task 1 에서 함께 바꾼다. 같은 곳의 규칙 5 에 "기획 워커만 `orch ask-user`로 사용자를 인터뷰한다"를 더한다.

## 파일 구조

```
orca-team/
  lib/orch/ask.py          (신규) ask-user·answer, 질문 스키마 검증, render, 답 파일·읽음 표시
  lib/orch/plan.py         (신규) tasks.toml 검증, plan apply, start-ready, design-review
  lib/orch/tasks.py        pending·awaiting_user, depends_on, start 의 선행 검사, accept 승인 게이트, fix 확장,
                           next_action 확장, parse_text·max_task_number·load_design_reviews
  lib/orch/comm.py         post()(ask·approval 타입, 잠금 안 검사), notify 는 post 위의 얇은 층
  lib/orch/config.py       max_parallel, user_approval_roles
  lib/orch/paths.py        Project.answers_dir·plan_json·design_review_json, 종료 코드 4 설명, VERSION 0.3.0
  lib/orch/review.py       tasks.toml 사전 검사(plan_problem)
  lib/orch/cli.py          ask-user, answer, plan apply, start-ready, design-review
  templates/config.toml    max_parallel = 4, user_approval_roles = ["designer"]
  templates/worker-spec.md 선행 태스크 줄, planner 예외
  templates/roles/planner.md  4.4 대로 다시 씀
  templates/coordinator.md    7 절 규칙
  templates/CLAUDE.section.md 4.3 표시 규칙, orch answer
  tests/test_ask.py        (신규)
  tests/test_plan.py       (신규) plan apply, 리뷰 사전 검사, start-ready, 승인 게이트, design-review
  tests/test_tasks.py test_config.py test_comm.py test_templates.py test_skills.py test_cli.py (수정)
README.md, docs/superpowers/specs/2026-10-30-orca-team-design.md(§13), orca-team/.claude-plugin/plugin.json, .claude-plugin/marketplace.json
```

훅(`hooks/orca_team_hook.py`)은 바꾸지 않는다. 기획 워커의 AskUserQuestion 은 지금처럼 워커 규칙으로 막히고, `orch ask-user`·`orch answer`는 `orca orchestration` 문자열을 담지 않아 메인 가드에 걸리지 않는다.

## 테스트 수(누적)

| 시점 | 테스트 수 |
|---|---|
| 시작(HEAD 855eef8) | 271 |
| Task 1 뒤 | 284 |
| Task 2 뒤 | 306 |
| Task 3 뒤 | 321 |
| Task 4 뒤 | 330 |
| Task 5 뒤 | 343 |
| Task 6 뒤 | 347 |
| Task 7 뒤 | 348 |
| Task 8 뒤 | 348(수동 확인, 코드 변경 없음) |

---

### Task 1: 설정(max_parallel·user_approval_roles)과 pending 상태·depends_on

`plan apply`(Task 3)가 만들 `pending` 상태와 front matter `depends_on`을 tasks.py 가 이해하게 한다. `pending`은 잠금을 잡지 않고, `orch start`는 선행 태스크가 모두 `done`일 때만 띄운다. 설정 두 개와 경로 세 개를 미리 둔다.

**Files:**
- Modify: `orca-team/lib/orch/config.py`(상수 2개, `Config.max_parallel`, `Config.user_approval_roles`)
- Modify: `orca-team/lib/orch/paths.py:53-74`(`Project.__init__`)
- Modify: `orca-team/lib/orch/cli.py:76-85`(`soft_config`)
- Modify: `orca-team/lib/orch/tasks.py`(모듈 docstring, `deps_of`·`unmet_deps`·`format_unmet` 신규, `validate`, `compose_spec`, `start`, `next_action`)
- Modify: `orca-team/templates/config.toml`, `orca-team/templates/worker-spec.md`
- Test: `orca-team/tests/test_config.py`, `orca-team/tests/test_templates.py`, `orca-team/tests/test_tasks.py`

**Interfaces:**
- Consumes: `tasks.load_state(p, task_id) -> dict | None`, `tasks.TaskDoc.front: dict`, `tasks.TASK_ID_RE`, `tasks.launch(...)`, `util.git`, `util.render`.
- Produces:
  - `config.DEFAULT_MAX_PARALLEL = 4`, `config.DEFAULT_APPROVAL_ROLES = ("designer",)`
  - `Config.max_parallel -> int`(1 이상, 아니면 `OrchError` 2), `Config.user_approval_roles -> list[str]`(아니면 `OrchError` 2)
  - `Project.answers_dir`(`state/answers`), `Project.plan_json`(`state/plan.json`), `Project.design_review_json`(`state/design-review.json`)
  - `tasks.deps_of(doc: TaskDoc) -> list[str]`(front matter `depends_on: T-001, T-002`)
  - `tasks.unmet_deps(p, deps: list[str]) -> list[tuple[str, str]]`(done 이 아닌 선행 태스크와 상태, 상태 파일이 없으면 `"없음"`)
  - `tasks.format_unmet(unmet) -> str`(`"T-001(working), T-002(없음)"`)
  - `tasks.start(p, cfg, task_id, retry=False, overrides=None) -> dict`: 상태가 없거나 `pending`이면 새로 띄운다. 선행 미완료면 `OrchError` 2. 새 상태에 `depends_on: list[str]`을 남기고 pending 상태의 다른 필드(`plan_key` 등)는 유지한다.
  - 태스크 상태 dict 의 pending 형식(Task 3 이 그대로 쓴다): `{"id", "role", "title", "status": "pending", "stage": "work", "round": 1, "base": None, "owns": [...], "depends_on": [T-id...], "plan_key": key, "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}}`
  - `tasks.next_action`: `pending` → `"orch start-ready"`, 선행 태스크가 dropped 면 메인에 묻고 drop 하라는 안내.
  - 워커 spec 템플릿 자리표시 `{depends_on}`(compose_spec 이 `"T-001, T-002"` 또는 `"없음"`을 넣는다).

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_config.py`의 `ConfigTest` 끝(`test_non_utf8_config_is_orch_error` 뒤)에 추가한다.

```python
    def test_max_parallel_default_and_value(self):
        self.assertEqual(self.cfg.max_parallel, 4)
        self.assertEqual(Config({**DATA, "max_parallel": 2}).max_parallel, 2)

    def test_malformed_max_parallel_is_orch_error(self):
        for bad in ("abc", 0, True, [1]):
            with self.subTest(bad=bad):
                with self.assertRaises(OrchError) as cm:
                    Config({**DATA, "max_parallel": bad}).max_parallel
                self.assertEqual(cm.exception.code, 2)

    def test_user_approval_roles(self):
        self.assertEqual(self.cfg.user_approval_roles, ["designer"])
        self.assertEqual(Config({**DATA, "user_approval_roles": []}).user_approval_roles, [])
        self.assertEqual(Config({**DATA, "user_approval_roles": ["designer", "tester"]}).user_approval_roles,
                         ["designer", "tester"])
        for bad in ("designer", [1], [""]):
            with self.subTest(bad=bad):
                with self.assertRaises(OrchError) as cm:
                    Config({**DATA, "user_approval_roles": bad}).user_approval_roles
                self.assertEqual(cm.exception.code, 2)
```

`orca-team/tests/test_templates.py`의 `TemplateTest`에서 `test_model_table_matches_spec` 바로 뒤에 추가한다.

```python
    def test_parallel_and_approval_defaults(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["max_parallel"], cfg["user_approval_roles"]), (4, ["designer"]))
```

`orca-team/tests/test_tasks.py`의 `NextActionTest` 클래스 뒤, `if __name__ == "__main__":` 앞에 추가한다.

```python
class DependsPendingTest(TaskCase):
    """plan apply 가 만드는 pending 태스크와 front matter depends_on."""

    def write_pending(self, task_id, role="developer", title="로그인", depends_on=(), owns=("src/**",)):
        state = {"id": task_id, "role": role, "title": title, "status": "pending", "stage": "work", "round": 1,
                 "base": None, "owns": list(owns), "depends_on": list(depends_on), "plan_key": task_id.lower(),
                 "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}}
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")

    def next_of(self, task_id):
        return {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}[task_id]

    def test_deps_of_reads_front_matter_and_validate_rejects_bad_ids(self):
        self.write_spec("T-002", extra="depends_on: T-001, T-003\n")
        doc = tasks.parse_task(tasks.task_path(Project(self.root), "T-002"))
        self.assertEqual(tasks.deps_of(doc), ["T-001", "T-003"])
        self.write_spec("T-004", extra="depends_on: login, T-004\n")
        proc = self.orch("start", "T-004", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("depends_on=login, T-004", proc.stderr)

    def test_start_refuses_until_dependency_done(self):
        self.write_spec("T-001")
        self.write_spec("T-002", role="tester", extra="depends_on: T-001\n")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(없음)", proc.stderr)
        self.orch("start", "T-001")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(working)", proc.stderr)
        self.assertEqual(len(self.calls_for("orchestration worker-start")), 1)
        self.set_state("T-001", status="done")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_start_launches_pending_task_and_keeps_plan_fields(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.assertEqual(self.orch("start", "T-001", "--retry", check=False).returncode, 2)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
        self.orch("start", "T-001")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["plan_key"], st["depends_on"]), ("working", 1, "t-001", []))
        self.assertEqual(st["base"], self.git("rev-parse", "HEAD").strip())

    def test_pending_task_does_not_hold_lock(self):
        self.write_spec("T-001")
        self.write_pending("T-001", owns=("src/**",))
        self.write_spec("T-002")
        self.orch("start", "T-002")
        self.assertEqual(self.state("T-002")["status"], "working")

    def test_worker_spec_names_dependencies_and_contracts(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.set_state("T-001", status="done")
        self.write_spec("T-002", role="tester", extra="depends_on: T-001\n")
        self.orch("start", "T-002")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("선행 태스크: T-001.", spec)
        self.assertIn("docs/planning/contracts.md", spec)
        self.assertIn("orch ask-user", spec)
        self.write_spec("T-003", role="planner")
        self.orch("start", "T-003")
        self.assertIn("선행 태스크: 없음.", self.arg(self.last_start(), "--spec"))

    def test_drop_pending_task_skips_release(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        out = json.loads(self.orch("drop", "T-001", "--reason", "범위 제외").stdout)
        self.assertEqual((out["status"], out["released"]), ("dropped", None))
        self.assertEqual(self.calls_for("orchestration worker-release"), [])

    def test_pending_next_is_start_ready(self):
        self.write_spec("T-001")
        self.write_pending("T-001")
        self.assertEqual(self.next_of("T-001"), "orch start-ready")

    def test_pending_with_dropped_dependency_asks_main_then_drop(self):
        self.write_pending("T-001")
        self.set_state("T-001", status="dropped")
        self.write_pending("T-002", role="tester", depends_on=("T-001",), owns=("tests/**",))
        nxt = self.next_of("T-002")
        self.assertIn("T-001", nxt)
        self.assertIn("orch notify --type question", nxt)
        self.assertIn("orch drop T-002", nxt)

    def test_status_survives_malformed_max_parallel(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_parallel = 4", 'max_parallel = "many"'),
                       encoding="utf-8")
        proc = self.orch("status", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("max_parallel", json.loads(proc.stdout)["config_error"])
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_config test_templates.TemplateTest.test_parallel_and_approval_defaults test_tasks.DependsPendingTest -v 2>&1 | tail -5`
Expected: `FAILED (failures=…, errors=…)`. 원인: `AttributeError: 'Config' object has no attribute 'max_parallel'`, `KeyError: 'max_parallel'`, `AttributeError: module 'orch.tasks' has no attribute 'deps_of'`, start 가 pending 태스크를 "이미 시작됐다"로 거부.

- [ ] **Step 3: config.py 구현**

`orca-team/lib/orch/config.py`의 모듈 docstring 을 바꾸고, `from .paths import OrchError` 바로 아래에 상수를 둔다.

```python
"""config.toml 해석: 직무, 소유 경로, 모델 우선순위(overrides > 직무 > defaults), 병렬 상한, 사용자 승인 직무."""
```

```python
DEFAULT_MAX_PARALLEL = 4
DEFAULT_APPROVAL_ROLES = ("designer",)
```

`Config` 클래스 끝(`max_rounds` 뒤)에 추가한다.

```python
    @property
    def max_parallel(self) -> int:
        """orch start-ready 가 동시에 띄워 두는 working 태스크 수(최상위 max_parallel). 없으면 4."""
        value = self.data.get("max_parallel", DEFAULT_MAX_PARALLEL)
        try:
            number = int(value)
        except (ValueError, TypeError) as exc:
            raise OrchError(f"config.toml max_parallel 이 정수가 아니다: {exc}", 2) from exc
        if isinstance(value, bool) or number < 1:
            raise OrchError(f"config.toml max_parallel 은 1 이상의 정수다(값: {value!r})", 2)
        return number

    @property
    def user_approval_roles(self) -> list[str]:
        """Codex APPROVE 뒤 사용자 승인을 한 번 더 받는 직무(최상위 user_approval_roles). 없으면 ["designer"]."""
        value = self.data.get("user_approval_roles", list(DEFAULT_APPROVAL_ROLES))
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise OrchError(f"config.toml user_approval_roles 는 직무 이름 목록이다(값: {value!r})", 2)
        return [v.strip() for v in value]
```

- [ ] **Step 4: paths.py, cli.soft_config, config.toml 템플릿**

`orca-team/lib/orch/paths.py`의 `Project.__init__` 전체:

```python
    def __init__(self, root: Path):
        self.root = root
        self.orch = root / ".orchestration"
        self.config_path = self.orch / "config.toml"
        self.tasks_dir = self.orch / "tasks"
        self.state = self.orch / "state"
        self.task_state_dir = self.state / "tasks"
        self.sessions = self.state / "sessions"
        self.run_json = self.state / "run.json"
        self.outbox = self.state / "outbox.jsonl"
        self.outbox_cursor = self.state / "outbox.cursor"
        self.watch_pid = self.state / "watch.pid"
        self.wait_pid = self.state / "wait.pid"
        self.main_msg_ids = self.state / "main-msg-ids.json"
        self.setup_mode = self.state / "setup-mode"
        self.answers_dir = self.state / "answers"  # orch answer 가 쓰는 ask 답 파일과 읽음 표시
        self.plan_json = self.state / "plan.json"  # plan apply 의 key → T-id 와 지문
        self.design_review_json = self.state / "design-review.json"  # design-review 질문 이력
```

`orca-team/lib/orch/cli.py`의 `soft_config` 전체:

```python
def soft_config(p):
    """(설정, 오류 문구). status 는 설정이 깨져도 복구 안내를 보여 준다."""
    from .config import Config

    try:
        cfg = Config.load(p.config_path)
        # 늦게 평가되는 값도 여기서 검증해 status 본문이 죽지 않게 한다
        cfg.max_rounds
        cfg.max_parallel
        cfg.user_approval_roles
        return cfg, None
    except OrchError as exc:
        return None, str(exc)
```

`orca-team/templates/config.toml`의 첫 줄부터 `[coordinator]` 직전까지를 다음으로 바꾼다(나머지는 그대로). TOML 최상위 키는 첫 표(`[coordinator]`)보다 앞에 있어야 한다.

```toml
# orca-team 설정. orch 가 디스패치·리뷰 때 읽는다.
# 모델 우선순위: orch start/fix --model·--effort > 태스크 front matter(claude_model 등) > [roles.<직무>] > [defaults]
# Claude 모델: fable | opus | sonnet | haiku (또는 전체 모델 ID). effort: low | medium | high | xhigh | max
# Codex 모델: gpt-6-astra | gpt-6.1-sol | gpt-6-luna. effort: low | medium | high | xhigh
# owns: 직무가 수정·커밋할 수 있는 경로(glob). 진행 중 태스크끼리 겹치면 orch start 가 거부한다.
# max_parallel: orch start-ready 가 동시에 띄워 두는 working 태스크 수 상한.
# user_approval_roles: Codex APPROVE 뒤 orch accept 해도 done 이 아니라 awaiting_user(사용자 일괄 승인 대기)가 되는 직무.

max_parallel = 4
user_approval_roles = ["designer"]

```

- [ ] **Step 5: tasks.py 구현**

모듈 docstring:

```python
"""태스크 spec, 상태(pending 포함), 선행 태스크(depends_on), 디스패치(start/fix/accept), 소유 경로 잠금."""
```

`validate` 바로 앞에 새 함수 셋을 둔다.

```python
def deps_of(doc: TaskDoc) -> list[str]:
    """front matter `depends_on: T-001, T-002` 의 선행 태스크 ID."""
    return [x.strip() for x in doc.front.get("depends_on", "").split(",") if x.strip()]


def unmet_deps(p: Project, deps: list[str]) -> list[tuple[str, str]]:
    """끝나지(done) 않은 선행 태스크와 그 상태. 상태 파일이 없으면 "없음"."""
    unmet = []
    for dep in deps:
        status = (load_state(p, dep) or {}).get("status") or "없음"
        if status != "done":
            unmet.append((dep, status))
    return unmet


def format_unmet(unmet: list[tuple[str, str]]) -> str:
    return ", ".join(f"{dep}({status})" for dep, status in unmet)
```

`validate` 전체:

```python
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
```

`compose_spec` 전체:

```python
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
        "owns": ", ".join(owns), "fix_block": fix_block})
```

`start` 전체:

```python
def start(p: Project, cfg: Config, task_id: str, retry: bool = False, overrides: dict | None = None) -> dict:
    require_coordinator(p)
    doc = load_doc(p, cfg, task_id)
    st = load_state(p, task_id)
    fresh = st is None or st.get("status") == "pending"  # pending: plan apply 가 만들고 아직 띄우지 않은 태스크
    if not fresh and not (retry and st.get("status") == "launch_failed" and st.get("stage") == "work"):
        raise OrchError(start_refusal(st), 2)
    if fresh and retry:
        raise OrchError(f"{task_id}: --retry 할 실패 기록이 없다", 2)
    deps = deps_of(doc)
    unmet = unmet_deps(p, deps)
    if unmet:
        raise OrchError(f"{task_id}: 선행 태스크가 끝나지(done) 않았다: {format_unmet(unmet)}. "
                        "끝나면 orch start-ready 가 띄운다", 2)
    owns = owns_of(doc, cfg)
    check_lock(p, task_id, owns)
    if fresh:
        # pending 상태의 plan_key 등은 남기고 실행 필드만 새로 쓴다.
        st = {**(st or {}), "id": task_id, "role": doc.role, "title": doc.title, "status": "new", "stage": "work",
              "round": 1, "base": git(p.root, "rev-parse", "HEAD").strip(), "owns": owns, "depends_on": deps,
              "task_id": None, "dispatch_id": None, "history": [], "verdicts": {}}
    return launch(p, cfg, doc, st, "work", 1, owns, None, overrides)
```

`next_action` 전체(`pending` 분기를 맨 앞에 더한다. 나머지는 그대로):

```python
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
        return (f"실행이 실패했거나 실행 도중 끊겼다. `orca orchestration worker-list` 로 이 태스크 워커가 떠 있는지 확인하고, "
                f"남은 워커는 Orca recovery 참조({RECOVERY_REF})대로 정리한다. 그다음 {retry_command(st)}. "
                f"복구할 수 없으면 orch drop {task_id} --reason \"<이유>\"")
    if status in FINISHED:
        return None
    return f"알 수 없는 상태({status}). .orchestration/state/tasks/{task_id}.json 을 확인한다"
```

- [ ] **Step 6: worker-spec.md**

`orca-team/templates/worker-spec.md` 전체:

```markdown
너는 orca-team 의 {role_title}({role}) 워커다. 아래 태스크 하나만 수행한다.

## 직무 정의
{role_doc}

## 태스크 {task_id} · 라운드 {round}
{task_body}

선행 태스크: {depends_on}. 시작 전에 `docs/planning/contracts.md`(있으면)와 선행 태스크 리포트 `docs/tasks/<선행 태스크 ID>/report.md` 를 먼저 읽는다.

## 소유 경로
수정·커밋해도 되는 경로: {owns}
리포트 경로 `docs/tasks/{task_id}/report.md` 는 항상 허용된다.
소유 경로 밖을 고쳐야 하면 멈추고 프리앰블의 ask 명령으로 코디네이터에게 묻는다.

{fix_block}## 작업 규칙
1. 같은 체크아웃에서 다른 워커가 동시에 일한다. 남의 변경을 되돌리거나 `git stash`·`git reset`·`git checkout -- <파일>` 하지 않는다.
2. 커밋은 경로를 지정해서 한다: `git add -- <소유 경로의 파일들> docs/tasks/{task_id}/report.md && git commit -m "[{task_id}] <요약>" -- <같은 경로들>`. 인덱스를 다른 워커와 같이 쓰므로 `--` 뒤 경로를 빼면 남이 스테이징한 파일까지 커밋된다. `git add -A`·`git add .`·경로 없는 `git commit` 은 쓰지 않는다.
3. 커밋 제목은 `[{task_id}] ` 로 시작한다. 예: `[{task_id}] feat: 로그인 폼 검증`.
4. 리포트 `docs/tasks/{task_id}/report.md` 에 라운드마다 `## r{round}` 절을 추가한다: 바꾼 파일, 이유, 검증 명령과 출력 원문, 남은 위험, 가정.
5. 사용자에게 직접 묻지 않는다(AskUserQuestion 금지). 결정이 필요하면 프리앰블의 ask 명령을 쓴다. 기획(planner) 워커만 직무 정의대로 `orch ask-user` 로 사용자를 인터뷰한다.
6. 끝나면 리포트까지 커밋한 뒤 worker_done 을 한 번 보낸다: `--outcome succeeded` 또는 `--outcome failed`, `--report-path docs/tasks/{task_id}/report.md`. 보낸 뒤에는 새 작업을 시작하지 않고 대기한다.
```

- [ ] **Step 7: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_config test_templates test_tasks -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 284 tests in …` 다음 줄 `OK`

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/lib/orch/config.py orca-team/lib/orch/paths.py orca-team/lib/orch/cli.py orca-team/lib/orch/tasks.py orca-team/templates/config.toml orca-team/templates/worker-spec.md orca-team/tests/test_config.py orca-team/tests/test_templates.py orca-team/tests/test_tasks.py && git commit -m "$(cat <<'EOF'
feat(orca-team): pending 상태·depends_on 과 max_parallel·user_approval_roles 설정

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/lib/orch/config.py orca-team/lib/orch/paths.py orca-team/lib/orch/cli.py orca-team/lib/orch/tasks.py orca-team/templates/config.toml orca-team/templates/worker-spec.md orca-team/tests/test_config.py orca-team/tests/test_templates.py orca-team/tests/test_tasks.py
```

---

### Task 2: 그릴링 질문 경로(ask-user, answer)

기획 워커가 `orch ask-user`로 질문을 outbox 에 올리고 답 파일을 기다린다. 메인은 `orch answer`로 답한다. `ask`는 답 파일로, `question`·`approval`은 지금의 `orch tell --reply`처럼 코디네이터 Run 으로 간다.

**Files:**
- Create: `orca-team/lib/orch/ask.py`
- Modify: `orca-team/lib/orch/comm.py`(모듈 docstring, `TYPES` 옆 `POST_TYPES`, `post` 신규, `notify`)
- Modify: `orca-team/lib/orch/paths.py`(`OrchError` docstring)
- Modify: `orca-team/lib/orch/cli.py`(`build_parser`, `run`)
- Create: `orca-team/tests/test_ask.py`
- Modify: `orca-team/tests/test_comm.py`

**Interfaces:**
- Consumes: `Project.answers_dir`(Task 1), `tasks.load_state`, `tasks.TASK_ID_RE`, `comm.read_outbox`, `comm.tell(p, body, reply_to) -> dict`, `util.now`, `util.read_json`.
- Produces:
  - `comm.POST_TYPES = ("progress", "question", "done", "ask", "approval")`
  - `comm.post(p, kind: str, body: str, extra: dict | None = None, check: Callable[[list[dict]], None] | None = None) -> dict` — outbox 잠금 안에서 `check(기존 줄)`을 부른 뒤 `{"id", "type", "body", **extra, "at"}`를 쓴다. Task 5 의 design-review 가 `post(p, "approval", ...)`로 쓴다.
  - `comm.notify(p, kind, body) -> dict`(타입은 여전히 `progress|question|done`만)
  - `ask.WAITING_EXIT = 4`, `ask.WAIT_SECONDS = 540.0`, `ask.POLL_SECONDS = 2.0`, `ask.DEFAULT_HEADER = "기획"`
  - `ask.validate_question(data) -> dict`(정리된 `{"question", "example", "header"[, "context"][, "options", "recommended", "reason"]}`, 어기면 `OrchError` 2). `context`는 선택, 마크다운 문자열 4000자 이하(`ask.MAX_CONTEXT`), 앞뒤 공백을 빼고 비면 뺀다.
  - `ask.build_render(q: dict) -> dict`(`{"header", "question"[, "context"][, "options", "multiSelect": False]}`). Task 5 가 검증 없이 직접 부른다(키: `header`, `question`, `example`, `context`, `options`, `recommended`, `reason`).
  - `ask.write_answer(p, out_id, text) -> Path`(있으면 `OrchError` 3), `ask.wait_answer(p, out_id, timeout=None, poll=2.0, clock=time.monotonic, sleep=time.sleep) -> dict`
  - `ask.ask_user(p, task_id, source=None, wait_id=None, pending=False) -> dict`, `ask.answer(p, out_id, body) -> dict`
  - 명령: `orch ask-user --task T-xxx (--json <파일|-> | --wait out-NNNN | --pending)`, `orch answer out-NNNN "<답>"`
  - outbox 의 ask 줄: `{"id", "type": "ask", "body": <질문>, "task": "T-xxx", "render": {...}, "at"}`

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_ask.py`(새 파일):

```python
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import ORCH, ProjectCase
from orch import ask, comm
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
WORKER = {"ORCA_TERMINAL_HANDLE": "term_w1", "ORCA_TEAM_ASK_WAIT": "0.2"}
GOOD = {"question": "로그인 방식은?", "example": "이메일이면 가입 화면이 필요하다",
        "options": [{"label": "이메일", "description": "가입 화면 필요"},
                    {"label": "소셜", "description": "외부 연동 필요"}],
        "recommended": 1, "reason": "가입 화면이 줄어든다."}


class AskSchemaTest(unittest.TestCase):
    def problems(self, data):
        with self.assertRaises(OrchError) as cm:
            ask.validate_question(data)
        self.assertEqual(cm.exception.code, 2)
        return str(cm.exception)

    def test_minimal_question_gets_default_header(self):
        q = ask.validate_question({"question": " 언제 쓰나요? ", "example": "출근길 5분"})
        self.assertEqual(q, {"question": "언제 쓰나요?", "example": "출근길 5분", "header": "기획"})

    def test_missing_and_multiline_fields_are_listed(self):
        msg = self.problems({"question": "첫 줄\n둘째 줄"})
        self.assertIn("question: 한 문장", msg)
        self.assertIn("example: 필수", msg)
        self.assertIn("질문은 JSON 객체", self.problems(["x"]))

    def test_option_count_must_be_2_to_4(self):
        for options in ([GOOD["options"][0]], GOOD["options"] * 3, "a"):
            with self.subTest(options=options):
                self.assertIn("options: 없거나 2~4개", self.problems({**GOOD, "options": options}))

    def test_recommended_and_reason_required_with_options(self):
        msg = self.problems({**GOOD, "recommended": 2, "reason": " "})
        self.assertIn("recommended", msg)
        self.assertIn("reason: 필수", msg)
        self.assertIn("recommended", self.problems({**GOOD, "recommended": True}))
        self.assertIn("recommended", self.problems({k: v for k, v in GOOD.items() if k != "recommended"}))

    def test_length_limits(self):
        msg = self.problems({**GOOD, "question": "가" * 301, "header": "열세글자를넘는꼬리표입니다",
                             "options": [{"label": "가" * 31, "description": "d"}, GOOD["options"][1]]})
        for text in ("question: 300자 이하", "header: 12자 이하", "options[0].label: 30자 이하"):
            self.assertIn(text, msg)

    def test_context_is_optional_markdown_up_to_4000(self):
        q = ask.validate_question({**GOOD, "context": "\n## 요약\n" + "가" * 3990 + "\n"})
        self.assertEqual(q["context"], "## 요약\n" + "가" * 3990)
        self.assertNotIn("context", ask.validate_question({**GOOD, "context": "  "}))
        self.assertIn("context: 4000자 이하", self.problems({**GOOD, "context": "가" * 4001}))
        self.assertIn("context: 마크다운 문자열", self.problems({**GOOD, "context": ["x"]}))


class RenderTest(unittest.TestCase):
    def test_recommended_option_moves_first_with_tag_and_reason(self):
        render = ask.build_render(ask.validate_question({**GOOD, "header": "로그인"}))
        self.assertEqual(render, {
            "header": "로그인", "question": "로그인 방식은?\n예: 이메일이면 가입 화면이 필요하다",
            "options": [{"label": "소셜 (추천)", "description": "추천 사유: 가입 화면이 줄어든다. 외부 연동 필요"},
                        {"label": "이메일", "description": "가입 화면 필요"}],
            "multiSelect": False})

    def test_without_options_render_has_no_options(self):
        render = ask.build_render(ask.validate_question({"question": "목표 사용자는?", "example": "1인 가구 직장인"}))
        self.assertEqual(render, {"header": "기획", "question": "목표 사용자는?\n예: 1인 가구 직장인"})

    def test_context_is_carried_as_render_context(self):
        context = "## 기획 핵심\n- 할 일 웹앱\n\n| key | 직무 |\n|---|---|\n| todo-ui | developer |"
        render = ask.build_render(ask.validate_question({**GOOD, "context": context}))
        self.assertEqual(render["context"], context)
        self.assertEqual(render["question"], "로그인 방식은?\n예: 이메일이면 가입 화면이 필요하다")


class AskCase(ProjectCase):
    """scaffold 한 프로젝트에 working 상태 planner 태스크 T-001 을 둔다."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.set_task("T-001")

    def set_task(self, task_id, role="planner", status="working"):
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": task_id, "role": role, "title": "기획", "status": status, "round": 1,
                                    "owns": ["docs/planning/**"], "history": []}), encoding="utf-8")

    def question_file(self, data=None):
        path = self.tmp / "q.json"
        path.write_text(json.dumps(GOOD if data is None else data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def ask(self, *args, task="T-001", check=False):
        return self.orch("ask-user", "--task", task, *args, check=check, env=WORKER)

    def outbox(self):
        path = self.root / ".orchestration/state/outbox.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class WaitClockTest(AskCase):
    """대기 시계는 모의 객체로 돌린다. 기본 540초, 2초 간격."""

    def test_times_out_after_540_seconds_polling_every_2(self):
        clock = {"t": 1000.0}
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            clock["t"] += seconds

        with mock.patch.dict(os.environ):
            os.environ.pop("ORCA_TEAM_ASK_WAIT", None)
            out = ask.wait_answer(Project(self.root), "out-0001", clock=lambda: clock["t"], sleep=sleep)
        self.assertEqual(out, {"id": "out-0001", "status": "waiting"})
        self.assertEqual((sum(slept), max(slept), len(slept)), (540.0, 2.0, 270))
        self.assertFalse((self.root / ".orchestration/state/answers/out-0001.read").exists())

    def test_returns_answer_that_arrives_mid_wait_and_marks_read(self):
        p = Project(self.root)
        clock = {"t": 0.0}

        def sleep(seconds):
            clock["t"] += seconds
            if clock["t"] == 6.0:
                ask.write_answer(p, "out-0001", "B안")

        out = ask.wait_answer(p, "out-0001", timeout=540, clock=lambda: clock["t"], sleep=sleep)
        self.assertEqual(out, {"id": "out-0001", "answer": "B안"})
        self.assertEqual(clock["t"], 6.0)
        self.assertTrue((self.root / ".orchestration/state/answers/out-0001.read").exists())


class AskUserTest(AskCase):
    def test_only_working_planner_may_ask(self):
        self.set_task("T-002", role="developer")
        self.set_task("T-003", status="done")
        for task_id in ("T-002", "T-003", "T-009"):
            self.assertEqual(self.ask("--json", self.question_file(), task=task_id).returncode, 2, task_id)
        self.assertEqual(self.outbox(), [])

    def test_schema_error_lists_fields_and_posts_nothing(self):
        proc = self.ask("--json", self.question_file({"question": "x", "options": [{"label": "a"}]}))
        self.assertEqual(proc.returncode, 2)
        for name in ("example", "options"):
            self.assertIn(name, proc.stderr)
        self.assertEqual(self.outbox(), [])

    def test_times_out_with_exit_4_then_wait_returns_answer(self):
        proc = self.ask("--json", self.question_file())
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"id": "out-0001", "status": "waiting"})
        entry = self.outbox()[0]
        self.assertEqual((entry["type"], entry["task"], entry["body"]), ("ask", "T-001", "로그인 방식은?"))
        self.assertEqual(entry["render"]["options"][0]["label"], "소셜 (추천)")
        self.orch("answer", "out-0001", "소셜", env=MAIN)
        out = json.loads(self.ask("--wait", "out-0001", check=True).stdout)
        self.assertEqual(out, {"id": "out-0001", "answer": "소셜"})
        self.assertTrue((self.root / ".orchestration/state/answers/out-0001.read").exists())

    def test_second_question_while_waiting_is_exit_3(self):
        self.assertEqual(self.ask("--json", self.question_file()).returncode, 4)
        proc = self.ask("--json", self.question_file())
        self.assertEqual(proc.returncode, 3)
        self.assertIn("--wait out-0001", proc.stderr)
        self.assertEqual(len(self.outbox()), 1)
        self.orch("answer", "out-0001", "이메일", env=MAIN)
        self.assertEqual(self.ask("--json", self.question_file()).returncode, 4)  # 답이 오면 다음 질문을 올린다
        self.assertEqual([e["id"] for e in self.outbox()], ["out-0001", "out-0002"])

    def test_wait_rejects_foreign_or_non_ask_id(self):
        self.orch("notify", "--type", "progress", "진행")  # out-0001
        self.set_task("T-002")
        self.ask("--json", self.question_file(), task="T-002")  # out-0002 는 T-002 의 질문
        for out_id in ("out-0001", "out-0002", "out-0099"):
            self.assertEqual(self.ask("--wait", out_id).returncode, 2, out_id)

    def test_pending_returns_unread_answers_once(self):
        self.ask("--json", self.question_file())  # out-0001
        self.orch("answer", "out-0001", "소셜", env=MAIN)
        self.ask("--json", self.question_file())  # out-0002, 답 없음
        out = json.loads(self.ask("--pending", check=True).stdout)
        self.assertEqual(out["pending"], [{"id": "out-0001", "question": "로그인 방식은?", "answer": "소셜"}])
        self.assertEqual(out["waiting"], "out-0002")
        again = json.loads(self.ask("--pending", check=True).stdout)
        self.assertEqual((again["pending"], again["waiting"]), ([], "out-0002"))

    def test_question_from_stdin(self):
        proc = subprocess.run([sys.executable, str(ORCH), "ask-user", "--task", "T-001", "--json", "-"],
                              cwd=self.root, env={**self.env, **WORKER}, input=json.dumps(GOOD, ensure_ascii=False),
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(self.outbox()[0]["render"]["header"], "기획")


class AnswerTest(AskCase):
    def test_answer_to_ask_writes_file_once(self):
        self.ask("--json", self.question_file())
        out = json.loads(self.orch("answer", "out-0001", "소셜", env=MAIN).stdout)
        self.assertEqual((out["type"], out["task"]), ("ask", "T-001"))
        saved = json.loads((self.root / ".orchestration/state/answers/out-0001.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["id"], saved["answer"]), ("out-0001", "소셜"))
        self.assertEqual(self.orch("answer", "out-0001", "이메일", check=False, env=MAIN).returncode, 3)
        self.assertEqual(self.calls_for("orchestration send"), [])

    def test_answer_to_question_and_approval_goes_to_coordinator_run(self):
        self.orch("notify", "--type", "question", "A안과 B안 중 무엇으로 할까요?")  # out-0001
        comm.post(Project(self.root), "approval", "디자인 1개를 확인해 주세요", {"tasks": ["T-002"]})  # out-0002
        for out_id, kind in (("out-0001", "question"), ("out-0002", "approval")):
            out = json.loads(self.orch("answer", out_id, "전부 승인", env=MAIN).stdout)
            self.assertEqual(out["type"], kind)
            call = self.calls_for("orchestration send")[-1]
            self.assertEqual(call[call.index("--subject") + 1], f"[main] reply {out_id}")
            self.assertIn("--body=전부 승인", call)
        self.assertFalse((self.root / ".orchestration/state/answers").exists())

    def test_answer_rejects_unknown_progress_and_empty(self):
        self.orch("notify", "--type", "progress", "진행")  # out-0001
        for args in (("out-0099", "x"), ("out-0001", "x"), ("out-0001", "  ")):
            self.assertEqual(self.orch("answer", *args, check=False, env=MAIN).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
```

`orca-team/tests/test_comm.py`의 `CommTest` 끝(`test_watch_sigterm_cleans_pid` 뒤)에 추가한다.

```python
    def test_post_carries_extra_fields_but_notify_rejects_ask_types(self):
        from orch import comm
        from orch.paths import OrchError, Project
        for kind in ("ask", "approval"):
            self.assertEqual(self.orch("notify", "--type", kind, "x", check=False).returncode, 2)
        seen = []
        entry = comm.post(Project(self.root), "ask", " 질문 ", {"task": "T-001", "render": {"header": "기획"}},
                          check=lambda entries: seen.append(len(entries)))
        self.assertEqual((entry["id"], entry["type"], entry["body"], entry["task"]), ("out-0001", "ask", "질문", "T-001"))
        self.assertEqual(seen, [0])
        self.assertEqual(self.outbox()[0]["render"], {"header": "기획"})
        with self.assertRaises(OrchError):
            comm.post(Project(self.root), "nope", "x")
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_ask test_comm -v 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'ask' from 'orch'`(test_ask 로드 실패)와 `AttributeError: module 'orch.comm' has no attribute 'post'`.

- [ ] **Step 3: comm.py 와 paths.py**

`orca-team/lib/orch/comm.py`의 모듈 docstring 을 다음으로 바꾼다.

```python
"""메인 ⇄ 코디네이터·기획 워커 소통.

코디네이터 → 메인: outbox.jsonl(파일). 메인은 Run 에 묶이지 않으므로 Orca 알림이 메인 입력란에 들어오지 않는다.
메인 → 코디네이터: 코디네이터 Run 메일함(orca orchestration send --to run:<id>).
기획 워커 → 메인: outbox 의 ask 줄(orch ask-user). 메인 → 기획 워커: state/answers/ 답 파일(orch answer). ask.py 참고.
"""
```

`TYPES = ("progress", "question", "done")` 줄을 다음 두 줄로 바꾼다.

```python
TYPES = ("progress", "question", "done")  # orch notify 가 받는 타입
POST_TYPES = (*TYPES, "ask", "approval")  # outbox 에 쓰는 모든 타입. ask·approval 은 메인이 그대로 쓰는 render 를 싣는다
```

`notify` 함수를 지우고 그 자리에 `post`와 새 `notify`를 둔다.

```python
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
```

`orca-team/lib/orch/paths.py`의 `OrchError` 클래스 전체:

```python
class OrchError(Exception):
    """사용자에게 보여줄 오류. code 는 종료 코드다(1 실패, 2 인자·상태, 3 중복, 12 의존성 없음).

    종료 코드 4(ask-user 답 대기 중)는 오류가 아니라 cli 가 결과 JSON 과 함께 돌려준다."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code
```

- [ ] **Step 4: ask.py**

`orca-team/lib/orch/ask.py`(새 파일):

```python
"""그릴링 인터뷰: 기획 워커 → 메인 직통 질문(ask-user)과 메인의 답(answer).

질문은 outbox 에 `type: "ask"` 줄로 쓰고(메인의 orch watch 가 그대로 전한다), 답은 `state/answers/<out-id>.json` 으로 받는다.
기획 워커는 답 파일을 2초 간격으로 확인하고, 540초가 지나면 exit 4 로 끝난다(Bash 포그라운드 상한이 10분이다).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from . import comm
from .paths import OrchError, Project
from .tasks import TASK_ID_RE, load_state
from .util import now, read_json

ASK_ROLE = "planner"
WAITING_EXIT = 4
POLL_SECONDS = 2.0
WAIT_SECONDS = 540.0
DEFAULT_HEADER = "기획"
MAX_TEXT = 300
MAX_LABEL = 30
MAX_HEADER = 12
MAX_CONTEXT = 4000
REPLY_TYPES = ("question", "approval")


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


def validate_question(data) -> dict:
    """질문 JSON 을 검사해 정리한 dict 를 돌려준다. 어긴 필드를 모두 모아 exit 2 로 알린다."""
    if not isinstance(data, dict):
        raise OrchError("질문은 JSON 객체여야 한다", 2)
    problems: list[str] = []
    header = data.get("header")
    q = {"question": _text(data.get("question"), "question", MAX_TEXT, problems),
         "example": _text(data.get("example"), "example", MAX_TEXT, problems),
         "header": DEFAULT_HEADER if header is None else _text(header, "header", MAX_HEADER, problems)}
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


def require_planner(p: Project, task_id: str) -> dict:
    """ask-user 는 working 상태의 planner 태스크만 쓴다."""
    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = load_state(p, task_id) or {}
    if st.get("role") != ASK_ROLE or st.get("status") != "working":
        raise OrchError(f"{task_id}: ask-user 는 working 상태의 {ASK_ROLE} 태스크만 쓴다"
                        f"(role={st.get('role')}, status={st.get('status')})", 2)
    return st


def answer_path(p: Project, out_id: str) -> Path:
    return p.answers_dir / f"{out_id}.json"


def read_marker(p: Project, out_id: str) -> Path:
    """기획 워커가 답을 받아 갔다는 표시."""
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
    require_planner(p, task_id)
    if pending:
        return pending_answers(p, task_id)
    if wait_id is not None:
        entry = find_entry(p, wait_id)
        if entry is None or entry.get("type") != "ask" or entry.get("task") != task_id:
            raise OrchError(f"{wait_id} 는 {task_id} 의 ask 질문이 아니다", 2)
        return wait_answer(p, wait_id)
    if source is None:
        raise OrchError("--json, --wait, --pending 중 하나가 필요하다", 2)
    q = validate_question(load_question(source))

    def one_at_a_time(entries: list[dict]) -> None:
        waiting = [e["id"] for e in asks_of(entries, task_id) if load_answer(p, e["id"]) is None]
        if waiting:
            raise OrchError(f"{task_id}: 답을 기다리는 질문({waiting[-1]})이 있다. 질문은 한 번에 하나다. "
                            f"orch ask-user --task {task_id} --wait {waiting[-1]} 로 그 답부터 기다린다", 3)

    entry = comm.post(p, "ask", q["question"], {"task": task_id, "render": build_render(q)}, check=one_at_a_time)
    return wait_answer(p, entry["id"])


def answer(p: Project, out_id: str, body: str) -> dict:
    """메인의 답. ask 는 답 파일로(기획 워커가 받아 간다), question·approval 은 코디네이터 Run 으로 보낸다."""
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
    if kind in REPLY_TYPES:
        return {"id": out_id, "type": kind, **comm.tell(p, body, out_id)}
    raise OrchError(f"{out_id} 는 {kind} 메시지다. ask·question·approval 에만 답한다", 2)
```

- [ ] **Step 5: cli.py**

`orca-team/lib/orch/cli.py`의 `build_parser` 전체:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orch", description="orca-team 오케스트레이션 CLI")
    parser.add_argument("--version", action="version", version=f"orca-team {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="설치 점검")
    s = sub.add_parser("scaffold", help="프로젝트에 orca-team 파일 생성")
    s.add_argument("--description-file", required=True)
    sub.add_parser("setup-done", help="세팅 모드 해제")
    s = sub.add_parser("up", help="코디네이터 확인·기동(메인)")
    s.add_argument("--wait-run", type=int, default=0, metavar="SECONDS")
    sub.add_parser("bind", help="Run 생성·재연결(코디네이터)")
    sub.add_parser("status", help="상태")
    s = sub.add_parser("tell", help="메인 → 코디네이터")
    s.add_argument("body")
    s.add_argument("--reply", metavar="OUT_ID")
    s = sub.add_parser("watch", help="코디네이터 → 메인 메시지 대기(메인, 백그라운드)")
    s.add_argument("--timeout", type=float, metavar="SECONDS")
    s = sub.add_parser("notify", help="코디네이터 → 메인")
    s.add_argument("--type", required=True, dest="kind")
    s.add_argument("body")
    s = sub.add_parser("wait", help="코디네이터 메일함 대기(코디네이터, 백그라운드)")
    s.add_argument("--ack")
    s.add_argument("--timeout-ms", type=int, default=3600000)
    s = sub.add_parser("ask-user", help="기획 워커 → 메인 직통 질문과 답 대기(기획 워커)")
    s.add_argument("--task", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", metavar="FILE", help="질문 JSON 파일. - 면 표준 입력")
    g.add_argument("--wait", metavar="OUT_ID", help="이미 올린 질문의 답을 이어서 기다린다")
    g.add_argument("--pending", action="store_true", help="답은 왔지만 아직 읽지 않은 질문")
    s = sub.add_parser("answer", help="ask·question·approval 에 답한다(메인)")
    s.add_argument("id")
    s.add_argument("body")
    s = sub.add_parser("task", help="태스크 spec")
    task_sub = s.add_subparsers(dest="task_cmd", required=True)
    n = task_sub.add_parser("new")
    n.add_argument("role")
    n.add_argument("title")
    for name in ("start", "fix"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--retry", action="store_true")
        s.add_argument("--model")
        s.add_argument("--effort")
        if name == "fix":
            s.add_argument("--note")
            s.add_argument("--force", action="store_true", help="워커가 일하는 중이어도 fix 한다")
    for name in ("review", "accept"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("drop", help="태스크 중단·잠금 해제(코디네이터)")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true", help="워커가 살아 있어도 중단한다")
    sub.add_parser("which").add_argument("ref")
    return parser
```

`run` 전체:

```python
def run(args) -> int:
    if args.cmd == "doctor":
        from . import setup

        checks = setup.doctor(Path.cwd())
        for c in checks:
            mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
            print(f"{mark} {c.name}: {c.detail}")
        return 0 if all(c.ok or not c.required for c in checks) else 1
    if args.cmd == "scaffold":
        from . import setup

        root = (git_toplevel(Path.cwd()) or Path.cwd()).resolve()
        emit(setup.scaffold(root, Path(args.description_file).read_text(encoding="utf-8")))
        return 0
    p, cfg = project(args.cmd in CONFIG_COMMANDS)
    if args.cmd in ("setup-done", "up", "bind", "status"):
        from . import setup

        handlers = {"setup-done": lambda: setup.setup_done(p), "up": lambda: setup.up(p, cfg, args.wait_run),
                    "bind": lambda: setup.bind(p), "status": lambda: setup.status(p, *soft_config(p))}
        emit(handlers[args.cmd]())
    elif args.cmd in ("tell", "watch", "notify", "wait"):
        from . import comm
        from .util import require_coordinator

        if args.cmd == "tell":
            emit(comm.tell(p, args.body, args.reply))
        elif args.cmd == "watch":
            def deliver(lines):
                emit(lines)
                sys.stdout.flush()

            if not comm.watch(p, timeout=args.timeout, deliver=deliver):
                emit([])
        elif args.cmd == "notify":
            emit(comm.notify(p, args.kind, args.body))
        else:
            require_coordinator(p)
            emit(comm.wait(p, args.ack, args.timeout_ms))
    elif args.cmd in ("ask-user", "answer"):
        from . import ask

        if args.cmd == "answer":
            emit(ask.answer(p, args.id, args.body))
        else:
            out = ask.ask_user(p, args.task, source=args.json, wait_id=args.wait, pending=args.pending)
            emit(out)
            if out.get("status") == "waiting":
                return ask.WAITING_EXIT
    elif args.cmd == "review":
        from . import review

        emit(review.review(p, cfg, args.id))
    else:
        from . import tasks

        if args.cmd == "task":
            emit(tasks.new_task(p, cfg, args.role, args.title))
        elif args.cmd == "start":
            emit(tasks.start(p, cfg, args.id, args.retry, overrides(args)))
        elif args.cmd == "fix":
            emit(tasks.fix(p, cfg, args.id, args.note, args.retry, overrides(args), args.force))
        elif args.cmd == "accept":
            emit(tasks.accept(p, args.id))
        elif args.cmd == "drop":
            emit(tasks.drop(p, args.id, args.reason, args.force))
        else:
            emit(tasks.which(p, args.ref))
    return 0
```

- [ ] **Step 6: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_ask test_comm -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 306 tests in …` 다음 줄 `OK`

- [ ] **Step 7: 커밋**

```bash
git add -- orca-team/lib/orch/ask.py orca-team/lib/orch/comm.py orca-team/lib/orch/paths.py orca-team/lib/orch/cli.py orca-team/tests/test_ask.py orca-team/tests/test_comm.py && git commit -m "$(cat <<'EOF'
feat(orca-team): 기획 워커 → 메인 직통 질문(orch ask-user)과 orch answer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/lib/orch/ask.py orca-team/lib/orch/comm.py orca-team/lib/orch/paths.py orca-team/lib/orch/cli.py orca-team/tests/test_ask.py orca-team/tests/test_comm.py
```

---

### Task 3: tasks.toml 검증과 plan apply

`docs/planning/tasks.toml`을 검증해 태스크 spec(`.orchestration/tasks/T-xxx.md`)과 `pending` 상태를 만든다. 같은 검증을 리뷰 사전 검사에도 걸어, 잘못된 tasks.toml 은 기획 태스크가 accept 되기 전에 REQUEST_CHANGES 로 돌아가게 한다(설계 대비 결정 4).

**Files:**
- Create: `orca-team/lib/orch/plan.py`
- Modify: `orca-team/lib/orch/tasks.py`(`parse_task` → `parse_text` 분리, `max_task_number` 신규, `new_task`)
- Modify: `orca-team/lib/orch/review.py`(import, `plan_problem` 신규, `run_review`)
- Modify: `orca-team/lib/orch/cli.py`(`GRAPH_COMMANDS`·`CONFIG_COMMANDS`, `build_parser`, `add_graph_commands`·`run_graph` 신규, `run`)
- Create: `orca-team/tests/test_plan.py`

**Interfaces:**
- Consumes: Task 1 의 `Project.plan_json`, pending 상태 형식, `tasks.validate`, `tasks.save_state`, `tasks.task_path`, `tasks.load_state`, `tasks.FINISHED`, `globs.any_overlap`, `util.git_commit_paths(root, paths, message)`, `util.git`, `util.require_coordinator`, `util.write_json`, `util.read_json`.
- Produces:
  - `tasks.parse_text(text: str, where: str) -> TaskDoc`, `tasks.max_task_number(p) -> int`
  - `plan.PLAN_FILE = "docs/planning/tasks.toml"`, `plan.REQUIRED`(필수 필드 8개), `plan.PlanTask`(dataclass: `key, role, title, owns, depends_on, target, change, constraints, acceptance`, `.digest() -> str`)
  - `plan.parse_plan(data: dict, cfg) -> list[PlanTask]`(필드 검증, 어기면 `OrchError` 2), `plan.graph_problems(tasks, skip_pair=None) -> list[str]`(없는 선행 key, 순환, 병렬 태스크 owns 겹침)
  - `plan.load_plan_state(p) -> {"tasks": {key: T-id}, "digests": {key: sha1}}`
  - `plan.check(p, cfg, append: bool) -> tuple[list[PlanTask], list[PlanTask]]`(전체, 새 태스크)
  - `plan.apply(p, cfg, append=False) -> {"applied": n, "commit": sha, "tasks": [{"key", "id", "role", "title", "depends_on"}]}`
  - `review.plan_problem(p, cfg, files) -> str | None`
  - cli: `GRAPH_COMMANDS`(이 태스크에서는 `{"plan"}`), `add_graph_commands(sub)`, `run_graph(p, cfg, args) -> int`. Task 4·5 는 이 셋만 고친다.
  - 명령: `orch plan apply [--append]`(코디네이터, config 필요)

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_plan.py`(새 파일):

```python
import json
import os
import subprocess
import unittest
from unittest import mock

from test_tasks import TaskCase
from orch import plan
from orch.config import Config
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
BASIC = [("login-design", "designer", ["docs/design/login/**"], []),
         ("auth-api", "developer", ["src/api/**"], []),
         ("login-ui", "developer", ["src/ui/**"], ["login-design", "auth-api"])]
BAD_FIELDS = """
[[task]]
key = "Bad_Key"
role = "chef"
title = "x"
owns = []
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "dup"
role = "developer"
title = "중복 1"
owns = ["src/a/**"]
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "dup"
role = "developer"
title = "중복 2"
owns = ["src/b/**"]
target = "t"
change = "c"
constraints = "k"
acceptance = ["a"]

[[task]]
key = "no-accept"
role = "developer"
title = "수용 기준 없음"
owns = ["src/c/**"]
target = "t"
change = "c"
constraints = "k"
"""


def quoted(items) -> str:
    return ", ".join(f'"{x}"' for x in items)


def toml_task(key, role, owns, depends_on=(), title=None) -> str:
    title = title or f"{key} 작업"
    return (f'[[task]]\nkey = "{key}"\nrole = "{role}"\ntitle = "{title}"\n'
            f"owns = [{quoted(owns)}]\ndepends_on = [{quoted(depends_on)}]\n"
            f'target = "{key} 대상"\nchange = "{key} 결과"\nconstraints = "contracts.md 1절을 따른다"\n'
            f'acceptance = ["{key} 확인 명령 통과"]\n')


def write_plan(root, rows) -> None:
    """rows 는 toml_task 인자 튜플 목록이거나 TOML 원문."""
    path = root / plan.PLAN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = rows if isinstance(rows, str) else "\n".join(toml_task(*row) for row in rows)
    path.write_text(text, encoding="utf-8")


class PlanApplyTest(TaskCase):
    def apply(self, *extra, check=True, env=None):
        return self.orch("plan", "apply", *extra, check=check, env=env)

    def spec_files(self):
        return sorted(f.name for f in (self.root / ".orchestration/tasks").glob("T-*.md"))

    def plan_state(self):
        return json.loads((self.root / ".orchestration/state/plan.json").read_text(encoding="utf-8"))

    def assert_nothing_created(self):
        self.assertEqual(self.spec_files(), [])
        self.assertFalse((self.root / ".orchestration/state/plan.json").exists())
        self.assertEqual(list((self.root / ".orchestration/state/tasks").glob("T-*.json")), [])

    def test_apply_writes_specs_commit_plan_and_pending_states(self):
        write_plan(self.root, BASIC)
        out = json.loads(self.apply().stdout)
        self.assertEqual([(t["key"], t["id"]) for t in out["tasks"]],
                         [("login-design", "T-001"), ("auth-api", "T-002"), ("login-ui", "T-003")])
        spec = (self.root / ".orchestration/tasks/T-003.md").read_text(encoding="utf-8")
        for text in ("role: developer", "owns: src/ui/**", "depends_on: T-001, T-002", "plan_key: login-ui",
                     "## Acceptance\n- login-ui 확인 명령 통과"):
            self.assertIn(text, spec)
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[plan] apply: 3 tasks")
        self.assertEqual(sorted(self.git("show", "--name-only", "--format=", "HEAD").split()),
                         [f".orchestration/tasks/T-00{n}.md" for n in (1, 2, 3)])
        self.assertEqual(self.plan_state()["tasks"], {"login-design": "T-001", "auth-api": "T-002", "login-ui": "T-003"})
        st = self.state("T-003")
        self.assertEqual((st["status"], st["depends_on"], st["plan_key"]), ("pending", ["T-001", "T-002"], "login-ui"))
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
        nexts = {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}
        self.assertEqual(set(nexts.values()), {"orch start-ready"})

    def test_numbers_continue_after_existing_tasks(self):
        self.orch("task", "new", "planner", "기획")
        write_plan(self.root, BASIC)
        out = json.loads(self.apply().stdout)
        self.assertEqual([t["id"] for t in out["tasks"]], ["T-002", "T-003", "T-004"])
        self.assertIn("depends_on: T-002, T-003",
                      (self.root / ".orchestration/tasks/T-004.md").read_text(encoding="utf-8"))

    def test_rejects_cycle_and_creates_nothing(self):
        write_plan(self.root, [("a", "developer", ["src/a/**"], ["b"]), ("b", "developer", ["src/b/**"], ["a"])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("의존 순환: a -> b -> a", proc.stderr)
        self.assert_nothing_created()

    def test_rejects_missing_dependency(self):
        write_plan(self.root, [("a", "developer", ["src/a/**"], ["ghost"])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("depends_on 에 없는 key ghost", proc.stderr)
        self.assert_nothing_created()

    def test_rejects_owns_overlap_between_parallel_tasks(self):
        write_plan(self.root, [("a", "developer", ["src/**"], []), ("b", "developer", ["src/ui/**"], [])])
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("a~b", proc.stderr)
        self.assertIn("src/**~src/ui/**", proc.stderr)
        self.assert_nothing_created()

    def test_allows_owns_overlap_between_dependent_tasks(self):
        write_plan(self.root, [("a", "developer", ["src/**"], []), ("b", "developer", ["src/ui/**"], ["a"]),
                               ("c", "developer", ["src/ui/x/**"], ["b"])])
        self.assertEqual(json.loads(self.apply().stdout)["applied"], 3)

    def test_rejects_bad_fields_missing_file_and_broken_toml(self):
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("tasks.toml 이 없다", proc.stderr)
        write_plan(self.root, "[[task]\n")
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("읽기 실패", proc.stderr)
        write_plan(self.root, BAD_FIELDS)
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        for text in ("key 는 영문 소문자", "알 수 없는 role 'chef'", "owns 는", "task[2](dup): key 중복",
                     "task[3](no-accept): 필수 필드 없음 acceptance"):
            self.assertIn(text, proc.stderr)
        self.assert_nothing_created()

    def test_second_apply_needs_append(self):
        write_plan(self.root, BASIC)
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("처음에는 --append 없이", proc.stderr)
        self.apply()
        proc = self.apply(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("orch plan apply --append", proc.stderr)

    def test_append_adds_only_new_keys(self):
        write_plan(self.root, BASIC[:2])
        self.apply()
        write_plan(self.root, BASIC)
        out = json.loads(self.apply("--append").stdout)
        self.assertEqual([(t["key"], t["id"], t["depends_on"]) for t in out["tasks"]],
                         [("login-ui", "T-003", ["T-001", "T-002"])])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[plan] apply: 1 tasks")
        self.assertEqual(list(self.plan_state()["tasks"]), ["login-design", "auth-api", "login-ui"])

    def test_append_rejects_changed_removed_or_no_new_keys(self):
        write_plan(self.root, BASIC[:2])
        self.apply()
        write_plan(self.root, [("login-design", "designer", ["docs/design/login/**"], [], "바뀐 제목"), BASIC[1], BASIC[2]])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("적용한 key 를 바꿨다: login-design", proc.stderr)
        write_plan(self.root, [BASIC[1]])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("적용한 key 가 빠졌다: login-design", proc.stderr)
        write_plan(self.root, BASIC[:2])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("더할 새 key 가 없다", proc.stderr)
        self.assertFalse((self.root / ".orchestration/tasks/T-003.md").exists())

    def test_append_allows_overlap_only_with_finished_tasks(self):
        write_plan(self.root, [("old-ui", "developer", ["src/ui/**"], [])])
        self.apply()
        write_plan(self.root, [("old-ui", "developer", ["src/ui/**"], []), ("new-ui", "developer", ["src/ui/login/**"], [])])
        proc = self.apply("--append", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("old-ui~new-ui", proc.stderr)
        self.set_state("T-001", status="done")
        self.assertEqual(json.loads(self.apply("--append").stdout)["tasks"][0]["id"], "T-002")

    def test_commit_failure_removes_written_specs(self):
        write_plan(self.root, BASIC)
        p = Project(self.root)

        def add_then_fail(root, paths, message):
            subprocess.run(["git", "-C", str(root), "add", "--", *paths], check=True, env=self.env)
            raise OrchError("커밋 실패 모사")

        with mock.patch.dict(os.environ, self.env), mock.patch.object(plan, "git_commit_paths", add_then_fail):
            with self.assertRaises(OrchError):
                plan.apply(p, Config.load(p.config_path))
        self.assert_nothing_created()
        self.assertEqual(self.git("ls-files", "--", ".orchestration/tasks"), "")
        self.apply()
        self.assertEqual(self.spec_files(), ["T-001.md", "T-002.md", "T-003.md"])

    def test_coordinator_only(self):
        write_plan(self.root, BASIC)
        self.assertEqual(self.apply(check=False, env=MAIN).returncode, 2)
        self.assert_nothing_created()


class PlanReviewTest(TaskCase):
    """기획 태스크가 커밋한 tasks.toml 은 리뷰 사전 검사에서 plan apply 와 같은 규칙으로 검증한다."""

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.write_spec("T-001", role="planner", title="기획")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec: 기획")
        self.orch("start", "T-001")

    def commit_plan(self, rows):
        write_plan(self.root, rows)
        self.git("add", "--", plan.PLAN_FILE)
        self.git("commit", "-q", "-m", "[T-001] docs: 태스크 그래프")

    def test_invalid_tasks_toml_is_auto_request_changes(self):
        self.commit_plan([("a", "developer", ["src/a/**"], ["b"]), ("b", "developer", ["src/b/**"], ["a"])])
        out = json.loads(self.orch("review", "T-001").stdout)
        self.assertEqual((out["verdict"], out["source"]), ("REQUEST_CHANGES", "orch"))
        review = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        self.assertIn("tasks.toml", review)
        self.assertIn("의존 순환", review)
        self.assertFalse((self.fake_dir / "codex-run.log").exists())

    def test_valid_tasks_toml_goes_to_codex(self):
        self.commit_plan(BASIC)
        out = json.loads(self.orch("review", "T-001").stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan -v 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'plan' from 'orch'`

- [ ] **Step 3: tasks.py — parse_text, max_task_number**

`parse_task`를 다음 두 함수로 바꾼다.

```python
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
```

`new_task` 바로 앞에 `max_task_number`를 두고, `new_task` 전체를 바꾼다.

```python
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
```

- [ ] **Step 4: plan.py — 검증과 apply**

`orca-team/lib/orch/plan.py`(새 파일):

```python
"""태스크 그래프. docs/planning/tasks.toml 을 검증해 태스크 spec 과 pending 상태로 만든다(plan apply)."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass

from .config import Config
from .globs import any_overlap
from .paths import OrchError, Project
from .tasks import FINISHED, load_state, max_task_number, parse_text, save_state, task_path, validate
from .util import git, git_commit_paths, read_json, require_coordinator, write_json

PLAN_FILE = "docs/planning/tasks.toml"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TEXT_FIELDS = ("target", "change", "constraints")
REQUIRED = ("key", "role", "title", "owns", "target", "change", "constraints", "acceptance")


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


def parse_plan(data: dict, cfg: Config) -> list[PlanTask]:
    """tasks.toml 을 읽은 dict 의 필드를 검사한다. 어긴 곳을 모두 모아 exit 2 로 알린다."""
    items = data.get("task")
    if not isinstance(items, list) or not items:
        raise OrchError(f"{PLAN_FILE}: [[task]] 가 없다", 2)
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
        raise OrchError(f"{PLAN_FILE} 검증 실패: " + "; ".join(problems), 2)
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


def read_plan(p: Project, cfg: Config) -> list[PlanTask]:
    path = p.root / PLAN_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OrchError(f"{PLAN_FILE} 이 없다. 기획 태스크가 만들어 커밋해야 한다", 2) from exc
    except (OSError, ValueError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
        raise OrchError(f"{PLAN_FILE} 읽기 실패: {exc}", 2) from exc
    return parse_plan(data, cfg)


def load_plan_state(p: Project) -> dict:
    """state/plan.json: {"tasks": {key: T-id}, "digests": {key: 지문}}. 없거나 깨졌으면 빈 계획."""
    data = read_json(p.plan_json, {})
    data = data if isinstance(data, dict) else {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), dict) else {}
    digests = data.get("digests") if isinstance(data.get("digests"), dict) else {}
    return {"tasks": tasks, "digests": digests}


def check(p: Project, cfg: Config, append: bool) -> tuple[list[PlanTask], list[PlanTask]]:
    """(tasks.toml 의 모든 태스크, 새로 만들 태스크). 어긴 곳이 있으면 모두 모아 exit 2."""
    state = load_plan_state(p)
    applied, digests = state["tasks"], state["digests"]
    if applied and not append:
        raise OrchError("계획을 이미 적용했다(state/plan.json). 새 태스크만 더하려면 orch plan apply --append", 2)
    if append and not applied:
        raise OrchError("아직 적용한 계획이 없다. 처음에는 --append 없이 orch plan apply", 2)
    tasks = read_plan(p, cfg)
    keys = {t.key for t in tasks}
    problems = []
    removed = [key for key in applied if key not in keys]
    if removed:
        problems.append(f"적용한 key 가 빠졌다: {', '.join(removed)}")
    changed = [t.key for t in tasks if t.key in applied and digests.get(t.key) != t.digest()]
    if changed:
        problems.append(f"적용한 key 를 바꿨다: {', '.join(changed)}. 기존 태스크는 그대로 두고 새 key 만 더한다")

    def settled(a: PlanTask, b: PlanTask) -> bool:
        """--append: 둘 다 기존 태스크이거나, 기존 쪽이 이미 끝났으면(done·dropped) 겹쳐도 된다."""
        old = [t for t in (a, b) if t.key in applied]
        if len(old) == 2:
            return True
        return any((load_state(p, applied[t.key]) or {}).get("status") in FINISHED for t in old)

    problems += graph_problems(tasks, settled)
    if problems:
        raise OrchError(f"{PLAN_FILE} 검증 실패: " + "; ".join(problems), 2)
    return tasks, [t for t in tasks if t.key not in applied]


def spec_text(t: PlanTask, task_id: str, dep_ids: list[str]) -> str:
    """orch task new 와 같은 형식의 태스크 spec. front matter 에 owns·depends_on·plan_key 를 넣는다."""
    lines = ["---", f"id: {task_id}", f"role: {t.role}", f"title: {t.title}", f"owns: {', '.join(t.owns)}",
             f"depends_on: {', '.join(dep_ids)}".rstrip(), f"plan_key: {t.key}", "---",
             "## Target", t.target, "", "## Change", t.change, "", "## Constraints", t.constraints, "",
             "## Acceptance", *(f"- {a}" for a in t.acceptance)]
    return "\n".join(lines) + "\n"


def apply(p: Project, cfg: Config, append: bool = False) -> dict:
    """tasks.toml 의 새 태스크를 spec 과 pending 상태로 만든다. 검증에 하나라도 실패하면 아무것도 만들지 않는다.
    spec 은 경로 지정 커밋 하나(`[plan] apply: N tasks`)로 묶는다. 잠금은 잡지 않는다(pending 은 ACTIVE 가 아니다)."""
    require_coordinator(p)
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
```

- [ ] **Step 5: review.py — tasks.toml 사전 검사**

`orca-team/lib/orch/review.py`의 import 에서 `from . import orca`를 `from . import orca, plan`으로 바꾼다. `auto_review` 바로 뒤에 추가한다.

```python
def plan_problem(p: Project, cfg: Config, files: list[str]) -> str | None:
    """이 태스크 커밋이 tasks.toml 을 바꿨으면 plan apply 와 같은 규칙으로 검증하고, 어긴 곳이 있으면 그 설명을 돌려준다.

    accept 뒤(done)에는 fix 로 기획 태스크를 되돌릴 수 없으므로 리뷰에서 먼저 잡는다."""
    if plan.PLAN_FILE not in files:
        return None
    try:
        plan.check(p, cfg, append=bool(plan.load_plan_state(p)["tasks"]))
    except OrchError as exc:
        return str(exc)
    return None
```

`run_review` 전체:

```python
def run_review(p: Project, cfg: Config, doc: TaskDoc) -> dict:
    task_id = doc.id
    st = must_state(p, task_id)
    if st.get("status") not in ("working", "reviewing"):
        raise OrchError(f"{task_id}: 리뷰는 워커가 끝난 working 상태(또는 리뷰가 끊긴 reviewing)에서만 한다"
                        f"(status={st.get('status')})", 2)
    rnd, owns = st["round"], owns_of(doc, cfg)
    review_rel = f"docs/reviews/{task_id}/r{rnd}.md"
    if st.get("dispatch_id"):
        try:  # 보존 요청은 최선 노력이다. orca 가 없을 때(12)만 멈춘다.
            orca.call(["orchestration", "worker-retain", "--dispatch", st["dispatch_id"]], check=False)
        except OrchError as exc:
            if exc.code == 12:
                raise
    st["status"] = "reviewing"
    save_state(p, st)
    untracked: list[str] = []
    try:
        commits = task_commits(p.root, task_id, st["base"])
        files = changed_files(p.root, [sha for sha, _ in commits])
        bad = violations(files, owns, task_id)
        if not commits:
            reason = f"`[{task_id}]` 로 시작하는 작업 커밋이 base({st['base'][:10]}) 이후에 없다."
            verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
        elif bad:
            reason = (f"소유 경로 밖 파일을 커밋했다(허용: {', '.join(owns)}, docs/tasks/{task_id}/**):\n"
                      + "\n".join(f"- {f}" for f in bad))
            verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
        else:
            dirty = uncommitted_owned(p.root, owns, task_id)
            if dirty:
                raise OrchError(f"{task_id}: 소유 경로에 커밋하지 않은 변경이 있다: {', '.join(dirty)}. 리뷰는 커밋만 본다. "
                                f"워커가 먼저 커밋해야 한다. `orch fix {task_id} --note \"커밋 안 된 변경을 커밋\"` 으로 "
                                "되돌려 보낸다", 2)
            problem = plan_problem(p, cfg, files)
            if problem:
                reason = f"`{plan.PLAN_FILE}` 가 orch plan apply 검증을 통과하지 못한다:\n{problem}"
                verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
            else:
                prompt = compose_prompt(p, cfg, doc, rnd, review_rel, commits, files)
                (verdict, untracked), source = codex_review(p, cfg, doc, rnd, review_rel, prompt), "codex"
        message = f"[{task_id}] review r{rnd}: {verdict}"
        if untracked:
            message += ("\n\n리뷰 중 새로 생긴 추적 안 된 파일(판정에는 영향 없음):\n"
                        + "\n".join(f"- {f}" for f in untracked))
        git_commit_paths(p.root, [review_rel], message)
    except BaseException as exc:
        st["status"] = "working"
        save_state(p, st)
        if isinstance(exc, (OSError, UnicodeDecodeError)):
            raise OrchError(f"리뷰 중 입출력 오류: {exc}") from exc
        raise
    st.setdefault("verdicts", {})[str(rnd)] = verdict
    st["status"] = "approved" if verdict == "APPROVE" else "changes"
    entry = {"stage": "review", "round": rnd, "at": now(), "verdict": verdict, "source": source}
    result = {"id": task_id, "round": rnd, "verdict": verdict, "source": source, "review": review_rel}
    if untracked:
        entry["untracked"] = result["untracked"] = untracked
    st["history"].append(entry)
    save_state(p, st)
    return result
```

- [ ] **Step 6: cli.py — plan apply**

`CONFIG_COMMANDS = {"up", "task", "start", "fix", "review"}` 줄을 다음으로 바꾼다.

```python
GRAPH_COMMANDS = {"plan"}  # 태스크 그래프 명령(코디네이터). add_graph_commands·run_graph 가 처리한다
CONFIG_COMMANDS = {"up", "task", "start", "fix", "review", *GRAPH_COMMANDS}
```

`build_parser`의 끝 `sub.add_parser("which").add_argument("ref")` 다음, `return parser` 앞에 `add_graph_commands(sub)` 한 줄을 넣는다. 결과 `build_parser` 전체:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orch", description="orca-team 오케스트레이션 CLI")
    parser.add_argument("--version", action="version", version=f"orca-team {VERSION}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="설치 점검")
    s = sub.add_parser("scaffold", help="프로젝트에 orca-team 파일 생성")
    s.add_argument("--description-file", required=True)
    sub.add_parser("setup-done", help="세팅 모드 해제")
    s = sub.add_parser("up", help="코디네이터 확인·기동(메인)")
    s.add_argument("--wait-run", type=int, default=0, metavar="SECONDS")
    sub.add_parser("bind", help="Run 생성·재연결(코디네이터)")
    sub.add_parser("status", help="상태")
    s = sub.add_parser("tell", help="메인 → 코디네이터")
    s.add_argument("body")
    s.add_argument("--reply", metavar="OUT_ID")
    s = sub.add_parser("watch", help="코디네이터 → 메인 메시지 대기(메인, 백그라운드)")
    s.add_argument("--timeout", type=float, metavar="SECONDS")
    s = sub.add_parser("notify", help="코디네이터 → 메인")
    s.add_argument("--type", required=True, dest="kind")
    s.add_argument("body")
    s = sub.add_parser("wait", help="코디네이터 메일함 대기(코디네이터, 백그라운드)")
    s.add_argument("--ack")
    s.add_argument("--timeout-ms", type=int, default=3600000)
    s = sub.add_parser("ask-user", help="기획 워커 → 메인 직통 질문과 답 대기(기획 워커)")
    s.add_argument("--task", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", metavar="FILE", help="질문 JSON 파일. - 면 표준 입력")
    g.add_argument("--wait", metavar="OUT_ID", help="이미 올린 질문의 답을 이어서 기다린다")
    g.add_argument("--pending", action="store_true", help="답은 왔지만 아직 읽지 않은 질문")
    s = sub.add_parser("answer", help="ask·question·approval 에 답한다(메인)")
    s.add_argument("id")
    s.add_argument("body")
    s = sub.add_parser("task", help="태스크 spec")
    task_sub = s.add_subparsers(dest="task_cmd", required=True)
    n = task_sub.add_parser("new")
    n.add_argument("role")
    n.add_argument("title")
    for name in ("start", "fix"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--retry", action="store_true")
        s.add_argument("--model")
        s.add_argument("--effort")
        if name == "fix":
            s.add_argument("--note")
            s.add_argument("--force", action="store_true", help="워커가 일하는 중이어도 fix 한다")
    for name in ("review", "accept"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("drop", help="태스크 중단·잠금 해제(코디네이터)")
    s.add_argument("id")
    s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true", help="워커가 살아 있어도 중단한다")
    sub.add_parser("which").add_argument("ref")
    add_graph_commands(sub)
    return parser


def add_graph_commands(sub) -> None:
    """태스크 그래프 명령(코디네이터)."""
    s = sub.add_parser("plan", help="태스크 그래프(docs/planning/tasks.toml) 적용(코디네이터)")
    plan_sub = s.add_subparsers(dest="plan_cmd", required=True)
    a = plan_sub.add_parser("apply", help="검증 후 태스크 spec·pending 상태 생성")
    a.add_argument("--append", action="store_true", help="이미 적용한 계획에 새 key 만 더한다")
```

`overrides` 함수 뒤에 추가한다.

```python
def run_graph(p, cfg, args) -> int:
    from . import plan

    emit(plan.apply(p, cfg, args.append))
    return 0
```

`run`에서 `p, cfg = project(args.cmd in CONFIG_COMMANDS)` 바로 다음 줄에 두 줄을 넣는다. 결과 `run`의 앞부분:

```python
    p, cfg = project(args.cmd in CONFIG_COMMANDS)
    if args.cmd in GRAPH_COMMANDS:
        return run_graph(p, cfg, args)
    if args.cmd in ("setup-done", "up", "bind", "status"):
```

`run` 전체(확인용):

```python
def run(args) -> int:
    if args.cmd == "doctor":
        from . import setup

        checks = setup.doctor(Path.cwd())
        for c in checks:
            mark = "ok  " if c.ok else ("FAIL" if c.required else "warn")
            print(f"{mark} {c.name}: {c.detail}")
        return 0 if all(c.ok or not c.required for c in checks) else 1
    if args.cmd == "scaffold":
        from . import setup

        root = (git_toplevel(Path.cwd()) or Path.cwd()).resolve()
        emit(setup.scaffold(root, Path(args.description_file).read_text(encoding="utf-8")))
        return 0
    p, cfg = project(args.cmd in CONFIG_COMMANDS)
    if args.cmd in GRAPH_COMMANDS:
        return run_graph(p, cfg, args)
    if args.cmd in ("setup-done", "up", "bind", "status"):
        from . import setup

        handlers = {"setup-done": lambda: setup.setup_done(p), "up": lambda: setup.up(p, cfg, args.wait_run),
                    "bind": lambda: setup.bind(p), "status": lambda: setup.status(p, *soft_config(p))}
        emit(handlers[args.cmd]())
    elif args.cmd in ("tell", "watch", "notify", "wait"):
        from . import comm
        from .util import require_coordinator

        if args.cmd == "tell":
            emit(comm.tell(p, args.body, args.reply))
        elif args.cmd == "watch":
            def deliver(lines):
                emit(lines)
                sys.stdout.flush()

            if not comm.watch(p, timeout=args.timeout, deliver=deliver):
                emit([])
        elif args.cmd == "notify":
            emit(comm.notify(p, args.kind, args.body))
        else:
            require_coordinator(p)
            emit(comm.wait(p, args.ack, args.timeout_ms))
    elif args.cmd in ("ask-user", "answer"):
        from . import ask

        if args.cmd == "answer":
            emit(ask.answer(p, args.id, args.body))
        else:
            out = ask.ask_user(p, args.task, source=args.json, wait_id=args.wait, pending=args.pending)
            emit(out)
            if out.get("status") == "waiting":
                return ask.WAITING_EXIT
    elif args.cmd == "review":
        from . import review

        emit(review.review(p, cfg, args.id))
    else:
        from . import tasks

        if args.cmd == "task":
            emit(tasks.new_task(p, cfg, args.role, args.title))
        elif args.cmd == "start":
            emit(tasks.start(p, cfg, args.id, args.retry, overrides(args)))
        elif args.cmd == "fix":
            emit(tasks.fix(p, cfg, args.id, args.note, args.retry, overrides(args), args.force))
        elif args.cmd == "accept":
            emit(tasks.accept(p, args.id))
        elif args.cmd == "drop":
            emit(tasks.drop(p, args.id, args.reason, args.force))
        else:
            emit(tasks.which(p, args.ref))
    return 0
```

- [ ] **Step 7: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan test_review test_tasks -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 321 tests in …` 다음 줄 `OK`

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/lib/orch/plan.py orca-team/lib/orch/tasks.py orca-team/lib/orch/review.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py && git commit -m "$(cat <<'EOF'
feat(orca-team): tasks.toml 검증과 orch plan apply, 리뷰 사전 검사

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/lib/orch/plan.py orca-team/lib/orch/tasks.py orca-team/lib/orch/review.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py
```

---

### Task 4: start-ready 와 max_parallel

`pending` 태스크 중 선행 태스크가 모두 `done`이고, 잠금이 겹치지 않고, `working` 수가 `max_parallel`보다 적은 것을 tasks.toml 순서로 띄운다. 한 태스크의 실행 실패는 나머지를 막지 않는다.

**Files:**
- Modify: `orca-team/lib/orch/plan.py`(`.tasks` import, `plan_order`·`start_ready` 신규)
- Modify: `orca-team/lib/orch/cli.py`(`GRAPH_COMMANDS`, `add_graph_commands`, `run_graph`)
- Modify: `orca-team/tests/test_plan.py`(import, `StartReadyTest` 추가)

**Interfaces:**
- Consumes: Task 1 의 `tasks.deps_of`, `tasks.unmet_deps`, `tasks.format_unmet`, `tasks.start`, `Config.max_parallel`; Task 3 의 `plan.load_plan_state`, `cli.add_graph_commands`·`run_graph`·`GRAPH_COMMANDS`; 기존 `tasks.all_states`, `tasks.check_lock`, `tasks.load_doc`, `tasks.owns_of`.
- Produces:
  - `plan.plan_order(p) -> list[str]`(plan.json 의 T-id 순서, 그 밖의 pending 은 ID 순)
  - `plan.start_ready(p, cfg) -> {"started": [{"id", "status": "working", "dispatch_id"} | {"id", "status": "launch_failed", "error"}], "waiting": [{"id", "reason": "deps"|"lock"|"limit"|"spec"[, "detail"]}], "running": int, "max_parallel": int}`
  - 명령: `orch start-ready`(코디네이터, config 필요). `started`에 `launch_failed`가 있으면 exit 1.

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_plan.py`의 import 부분을 다음으로 바꾼다.

```python
import json
import os
import subprocess
import unittest
from unittest import mock

from test_tasks import STARTED, TaskCase
from orch import orca, plan
from orch.config import Config
from orch.paths import OrchError, Project
```

`PlanReviewTest` 뒤, `if __name__ == "__main__":` 앞에 추가한다.

```python
class StartReadyTest(TaskCase):
    """T-001 login-design, T-002 auth-api 는 독립, T-003 login-ui 는 둘에, T-004 login-test 는 T-003 에 의존한다."""

    def setUp(self):
        super().setUp()
        write_plan(self.root, [*BASIC, ("login-test", "tester", ["tests/login/**"], ["login-ui"])])
        self.orch("plan", "apply")

    def ready(self, check=True, env=None):
        return self.orch("start-ready", check=check, env=env)

    def set_max_parallel(self, n):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_parallel = 4", f"max_parallel = {n}"),
                       encoding="utf-8")

    def test_starts_ready_tasks_in_plan_order(self):
        out = json.loads(self.ready().stdout)
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]], [("T-001", "working"), ("T-002", "working")])
        self.assertEqual([(w["id"], w["reason"]) for w in out["waiting"]], [("T-003", "deps"), ("T-004", "deps")])
        self.assertIn("T-001(working)", out["waiting"][0]["detail"])
        self.assertEqual((out["running"], out["max_parallel"]), (2, 4))
        titles = [self.arg(c, "--task-title") for c in self.calls_for("orchestration worker-start")]
        self.assertEqual([t.split()[0] for t in titles], ["T-001", "T-002"])

    def test_limit_caps_working_tasks(self):
        self.set_max_parallel(1)
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        self.assertEqual(out["waiting"][0], {"id": "T-002", "reason": "limit"})
        self.assertEqual((out["running"], out["max_parallel"]), (1, 1))
        self.assertEqual(json.loads(self.ready().stdout)["started"], [])

    def test_lock_waits_for_overlapping_active_task(self):
        self.write_spec("T-009", title="긴급", extra="owns: src/api/auth/**\n")
        self.orch("start", "T-009")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        lock = next(w for w in out["waiting"] if w["id"] == "T-002")
        self.assertEqual(lock["reason"], "lock")
        self.assertIn("T-009", lock["detail"])

    def test_dependents_start_after_dependencies_done(self):
        self.ready()
        self.set_state("T-001", status="done")
        self.set_state("T-002", status="done")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-003"])
        self.assertEqual(out["waiting"], [{"id": "T-004", "reason": "deps", "detail": "T-003(working)"}])

    def test_reviewing_task_does_not_count_toward_limit(self):
        self.set_max_parallel(1)
        self.ready()
        self.set_state("T-001", status="reviewing")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-002"])
        self.assertEqual(out["running"], 1)

    def test_broken_spec_waits_with_reason_spec(self):
        self.write_spec("T-002", role="chef")
        out = json.loads(self.ready().stdout)
        self.assertEqual([s["id"] for s in out["started"]], ["T-001"])
        broken = next(w for w in out["waiting"] if w["id"] == "T-002")
        self.assertEqual(broken["reason"], "spec")
        self.assertIn("role=chef", broken["detail"])

    def test_launch_failure_is_reported_and_others_still_start(self):
        p = Project(self.root)

        def call(cmd, check=True):
            if cmd[:2] == ["orchestration", "worker-start"] and cmd[cmd.index("--task-title") + 1].startswith("T-001"):
                return 1, {"ok": False, "error": {"code": "x"}}
            return 0, STARTED

        with mock.patch.dict(os.environ, self.env), mock.patch.object(orca, "call", call):
            out = plan.start_ready(p, Config.load(p.config_path))
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]],
                         [("T-001", "launch_failed"), ("T-002", "working")])
        self.assertIn("--retry", out["started"][0]["error"])
        self.assertEqual(self.state("T-001")["status"], "launch_failed")
        self.assertEqual(out["running"], 1)

    def test_cli_exits_1_when_a_launch_fails(self):
        self.respond("orchestration worker-start", {"ok": False, "error": {"code": "x"}}, code=1)
        proc = self.ready(check=False)
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertEqual([(s["id"], s["status"]) for s in out["started"]],
                         [("T-001", "launch_failed"), ("T-002", "launch_failed")])

    def test_coordinator_only(self):
        self.assertEqual(self.ready(check=False, env=MAIN).returncode, 2)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan.StartReadyTest -v 2>&1 | tail -5`
Expected: `FAILED (failures=…, errors=…)`, 9개 모두 실패. 원인: `orch start-ready` 가 argparse 의 `invalid choice` 로 exit 2(헬퍼의 `self.fail`), `AttributeError: module 'orch.plan' has no attribute 'start_ready'`.

- [ ] **Step 3: plan.py — start_ready**

`orca-team/lib/orch/plan.py`의 `.tasks` import 줄을 다음으로 바꾼다.

```python
from .tasks import (FINISHED, all_states, check_lock, deps_of, format_unmet, load_doc, load_state, max_task_number,
                    owns_of, parse_text, save_state, start, task_path, unmet_deps, validate)
```

모듈 docstring 을 바꾼다.

```python
"""태스크 그래프. docs/planning/tasks.toml 을 검증해 태스크 spec 과 pending 상태로 만들고(plan apply),
시작할 수 있는 태스크를 동시 상한까지 띄운다(start-ready)."""
```

파일 끝에 추가한다.

```python
def plan_order(p: Project) -> list[str]:
    """시작 후보 순서: tasks.toml 순서(plan.json 에 적용 순서대로 남는다), 그 밖의 pending 태스크는 ID 순."""
    ordered = list(load_plan_state(p)["tasks"].values())
    extra = sorted(st["id"] for st in all_states(p) if st.get("status") == "pending" and st["id"] not in ordered)
    return ordered + extra


def start_ready(p: Project, cfg: Config) -> dict:
    """pending 태스크 중 선행 태스크가 모두 done 이고(deps), 잠금이 겹치지 않고(lock), working 수가 max_parallel 보다
    적은(limit) 것을 tasks.toml 순서로 띄운다. 한 태스크의 실행 실패는 나머지를 막지 않는다."""
    require_coordinator(p)
    limit = cfg.max_parallel
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
        unmet = unmet_deps(p, deps_of(doc))
        if unmet:
            waiting.append({"id": task_id, "reason": "deps", "detail": format_unmet(unmet)})
            continue
        try:
            check_lock(p, task_id, owns)
        except OrchError as exc:
            waiting.append({"id": task_id, "reason": "lock", "detail": str(exc)})
            continue
        if running >= limit:
            waiting.append({"id": task_id, "reason": "limit"})
            continue
        try:
            entry = start(p, cfg, task_id)
        except OrchError as exc:
            if exc.code == 12:
                raise
            started.append({"id": task_id, "status": "launch_failed", "error": str(exc)})
            continue
        started.append({"id": task_id, "status": "working", "dispatch_id": entry.get("dispatch_id")})
        running += 1
    return {"started": started, "waiting": waiting, "running": running, "max_parallel": limit}
```

- [ ] **Step 4: cli.py**

`GRAPH_COMMANDS` 줄:

```python
GRAPH_COMMANDS = {"plan", "start-ready"}  # 태스크 그래프 명령(코디네이터). add_graph_commands·run_graph 가 처리한다
```

`add_graph_commands` 전체:

```python
def add_graph_commands(sub) -> None:
    """태스크 그래프 명령(코디네이터)."""
    s = sub.add_parser("plan", help="태스크 그래프(docs/planning/tasks.toml) 적용(코디네이터)")
    plan_sub = s.add_subparsers(dest="plan_cmd", required=True)
    a = plan_sub.add_parser("apply", help="검증 후 태스크 spec·pending 상태 생성")
    a.add_argument("--append", action="store_true", help="이미 적용한 계획에 새 key 만 더한다")
    sub.add_parser("start-ready", help="시작할 수 있는 pending 태스크를 max_parallel 까지 띄운다(코디네이터)")
```

`run_graph` 전체:

```python
def run_graph(p, cfg, args) -> int:
    from . import plan

    if args.cmd == "plan":
        emit(plan.apply(p, cfg, args.append))
        return 0
    out = plan.start_ready(p, cfg)
    emit(out)
    return 1 if any(s["status"] == "launch_failed" for s in out["started"]) else 0
```

- [ ] **Step 5: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 330 tests in …` 다음 줄 `OK`

- [ ] **Step 6: 커밋**

```bash
git add -- orca-team/lib/orch/plan.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py && git commit -m "$(cat <<'EOF'
feat(orca-team): orch start-ready 로 시작 가능한 태스크를 max_parallel 까지 띄운다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/lib/orch/plan.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py
```

---

### Task 5: 디자인 일괄 승인 게이트(accept → awaiting_user, fix, design-review)

`user_approval_roles` 직무는 Codex APPROVE 뒤 `orch accept`해도 `awaiting_user`(잠금 유지, 워커 해제)가 된다. 모두 모이면 `orch design-review`가 링크를 모은 승인 질문(`type: "approval"`) 하나를 메인에 올린다. `awaiting_user`에서 다시 accept 하면 사용자 승인으로 `done`, fix 하면 새 라운드다.

**Files:**
- Modify: `orca-team/lib/orch/tasks.py`(docstring, config import, `ACTIVE`, `load_design_reviews`·`approval_roles` 신규, `accept`, `fix`, `next_action`)
- Modify: `orca-team/lib/orch/plan.py`(import, `LINK_RE`·`REVIEW_WAIT_STATUSES`·`design_link`·`design_review` 신규)
- Modify: `orca-team/lib/orch/cli.py`(`GRAPH_COMMANDS`, `add_graph_commands`, `run_graph`)
- Modify: `orca-team/tests/test_plan.py`(`ApprovalTest`, `DesignReviewTest` 추가)

**Interfaces:**
- Consumes: Task 1 의 `Config.user_approval_roles`, `config.DEFAULT_APPROVAL_ROLES`, `Project.design_review_json`; Task 2 의 `comm.post(p, "approval", body, extra)`, `ask.build_render(q)`; Task 3·4 의 `cli.add_graph_commands`·`run_graph`·`GRAPH_COMMANDS`; 기존 `tasks.release_previous`, `tasks.launch`, `tasks.role_title`, `comm.notify`.
- Produces:
  - `tasks.ACTIVE`에 `"awaiting_user"` 추가(잠금 유지, 리뷰 제외 경로에도 들어간다)
  - `tasks.load_design_reviews(p) -> list[dict]`(`[{"id", "tasks": {T-id: round}, "at"}]`)
  - `tasks.approval_roles(p) -> list[str]`(config 가 깨지면 `["designer"]`)
  - `tasks.accept(p, task_id) -> dict`: `approved` + 승인 직무 → `{"id", "status": "awaiting_user", "released", "notified"[, "release_error"]}`; `approved` + 그 밖 → `status: "done"`(기존과 같다); `awaiting_user` → `{"id", "status": "done", "user_approved": True, "notified"}`(해제 호출 없음)
  - `tasks.fix`: `awaiting_user` 허용, 최대 라운드 검사 없음, accept 때 해제했으면 다시 해제하지 않음, 이력에 `user_requested: True`
  - `tasks.next_action`: `awaiting_user` → 질문 전이면 `orch design-review`, 질문 뒤면 그 out-id 와 accept/fix 안내
  - `plan.design_link(p, task_id) -> str | None`, `plan.design_review(p, cfg) -> {"id": out-id, "tasks": [T-id...], "links": {T-id: url | None}}`
  - outbox 의 approval 줄: `{"id", "type": "approval", "body": "디자인 N개를 확인해 주세요", "tasks": [...], "render": {...}, "at"}`. 화면별 링크 목록은 `render.context`(`- T-xxx 제목: 링크` 줄)에 있다.
  - 명령: `orch design-review`(코디네이터, config 필요). 조건 불충족 exit 2, 같은 화면·라운드 재질문 exit 3.

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_plan.py`의 `StartReadyTest` 뒤, `if __name__ == "__main__":` 앞에 추가한다.

```python
def outbox(root, kind=None):
    path = root / ".orchestration/state/outbox.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    return [e for e in entries if kind is None or e["type"] == kind]


class ApprovalTest(TaskCase):
    """designer 는 기본 승인 대상 직무다. Codex APPROVE 뒤 accept 하면 awaiting_user 가 된다."""

    def setUp(self):
        super().setUp()
        self.write_spec("T-001", role="designer", title="로그인 화면")
        self.orch("start", "T-001")
        self.set_state("T-001", status="approved")

    def next_of(self, task_id):
        return {t["id"]: t["next"] for t in json.loads(self.orch("status").stdout)["tasks"]}[task_id]

    def test_accept_designer_waits_for_user_and_keeps_lock(self):
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["released"]), ("awaiting_user", True))
        self.assertEqual(self.state("T-001")["status"], "awaiting_user")
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)
        self.assertIn("사용자 승인 대기", outbox(self.root)[-1]["body"])
        self.write_spec("T-002", role="designer", title="회원가입")
        proc = self.orch("start", "T-002", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("T-001(awaiting_user)", proc.stderr)

    def test_accept_from_awaiting_user_is_user_approval(self):
        self.orch("accept", "T-001")
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["user_approved"]), ("done", True))
        st = self.state("T-001")
        self.assertEqual(st["status"], "done")
        self.assertIn("user_approved_at", st)
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)
        self.assertIn("사용자 승인", outbox(self.root)[-1]["body"])
        self.write_spec("T-002", role="designer", title="회원가입")
        self.orch("start", "T-002")

    def test_empty_approval_roles_accepts_straight_to_done(self):
        cfg = self.root / ".orchestration/config.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace('user_approval_roles = ["designer"]',
                                                               "user_approval_roles = []"), encoding="utf-8")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "done")

    def test_broken_config_still_gates_designer(self):
        (self.root / ".orchestration/config.toml").write_text("roles = [깨진\n", encoding="utf-8")
        self.assertEqual(json.loads(self.orch("accept", "T-001").stdout)["status"], "awaiting_user")

    def test_fix_from_awaiting_user_starts_new_round_past_max(self):
        self.orch("accept", "T-001")
        self.set_state("T-001", round=3)
        self.orch("fix", "T-001", "--note", "버튼을 키워 주세요")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"], st["stage"]), ("working", 4, "fix"))
        self.assertTrue(st["history"][-1]["user_requested"])
        self.assertIn("버튼을 키워 주세요", self.arg(self.last_start(), "--spec"))
        self.assertEqual(len(self.calls_for("orchestration worker-release")), 1)  # accept 때 한 번뿐

    def test_next_action_for_awaiting_user(self):
        self.orch("accept", "T-001")  # out-0001 progress
        self.assertIn("orch design-review", self.next_of("T-001"))
        self.orch("design-review")  # out-0002 approval
        nxt = self.next_of("T-001")
        for text in ("out-0002", "orch accept T-001", "orch fix T-001 --note"):
            self.assertIn(text, nxt)


class DesignReviewTest(TaskCase):
    def awaiting(self, task_id, title, owns):
        self.write_spec(task_id, role="designer", title=title, extra=f"owns: {owns}\n")
        self.orch("start", task_id)
        self.set_state(task_id, status="approved")
        self.orch("accept", task_id)

    def test_refuses_while_design_tasks_unfinished(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.write_spec("T-002", role="designer", title="회원가입 화면", extra="owns: docs/design/signup/**\n")
        self.orch("start", "T-002")
        self.write_spec("T-003", title="API")  # developer 는 승인 대상이 아니다
        self.orch("start", "T-003")
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-002(working)", proc.stderr)
        self.assertNotIn("T-003", proc.stderr)
        self.assertEqual(outbox(self.root, "approval"), [])

    def test_refuses_without_awaiting_user(self):
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("awaiting_user", proc.stderr)
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.orch("accept", "T-001")  # 사용자 승인으로 done
        self.assertEqual(self.orch("design-review", check=False).returncode, 2)

    def test_posts_one_approval_with_design_links(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.awaiting("T-002", "회원가입 화면", "docs/design/signup/**")
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("## r1\n- https://claude.ai/artifact/OLD\n\n## r2\n- 최신(https://claude.ai/code/artifact/1a2b-3c).\n",
                          encoding="utf-8")
        out = json.loads(self.orch("design-review").stdout)
        self.assertEqual(out["tasks"], ["T-001", "T-002"])
        self.assertEqual(out["links"], {"T-001": "https://claude.ai/code/artifact/1a2b-3c", "T-002": None})
        entry = outbox(self.root, "approval")[-1]
        self.assertEqual((entry["id"], entry["tasks"], entry["body"]), (out["id"], ["T-001", "T-002"], "디자인 2개를 확인해 주세요"))
        render = entry["render"]
        self.assertEqual(render["header"], "디자인")
        self.assertEqual(render["question"], "디자인 2개를 확인해 주세요\n"
                                             "예: 위 링크를 화면마다 열어 본다. 고칠 화면은 Other 에 화면과 내용을 적는다")
        self.assertEqual(render["context"], "- T-001 로그인 화면: https://claude.ai/code/artifact/1a2b-3c\n"
                                            "- T-002 회원가입 화면: (링크 없음: docs/tasks/T-002/report.md)")
        self.assertEqual([o["label"] for o in render["options"]], ["전부 승인 (추천)", "수정 요청(화면과 내용을 적어 주세요)"])
        self.assertTrue(render["options"][0]["description"].startswith("추천 사유: "))
        saved = json.loads((self.root / ".orchestration/state/design-review.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["asks"][-1]["tasks"], {"T-001": 1, "T-002": 1})
        self.orch("answer", out["id"], "전부 승인 (추천)", env=MAIN)
        call = self.calls_for("orchestration send")[-1]
        self.assertEqual(call[call.index("--subject") + 1], f"[main] reply {out['id']}")

    def test_same_designs_twice_is_exit_3(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        first = json.loads(self.orch("design-review").stdout)["id"]
        proc = self.orch("design-review", check=False)
        self.assertEqual(proc.returncode, 3)
        self.assertIn(first, proc.stderr)
        self.assertEqual(len(outbox(self.root, "approval")), 1)

    def test_after_fix_asks_only_revised_designs(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.awaiting("T-002", "회원가입 화면", "docs/design/signup/**")
        first = json.loads(self.orch("design-review").stdout)["id"]
        self.orch("accept", "T-001")  # 사용자 승인
        self.orch("fix", "T-002", "--note", "여백을 줄여 주세요")
        self.set_state("T-002", status="approved")
        self.orch("accept", "T-002")  # r2 로 다시 awaiting_user
        out = json.loads(self.orch("design-review").stdout)
        self.assertNotEqual(out["id"], first)
        self.assertEqual(out["tasks"], ["T-002"])
        self.assertTrue(outbox(self.root, "approval")[-1]["render"]["question"].startswith("디자인 1개를 확인해 주세요\n"))

    def test_coordinator_only(self):
        self.awaiting("T-001", "로그인 화면", "docs/design/login/**")
        self.assertEqual(self.orch("design-review", check=False, env=MAIN).returncode, 2)
        self.assertEqual(outbox(self.root, "approval"), [])

    def test_design_link_takes_last_claude_artifact_url(self):
        p = Project(self.root)
        self.assertIsNone(plan.design_link(p, "T-001"))
        report = self.root / "docs/tasks/T-001/report.md"
        report.parent.mkdir(parents=True)
        report.write_text("r1 https://claude.ai/artifact/AAA\nr2 (https://claude.ai/code/artifact/0b1c-2d3e).\n"
                          "참고 https://example.com/artifact/zzz\n", encoding="utf-8")
        self.assertEqual(plan.design_link(p, "T-001"), "https://claude.ai/code/artifact/0b1c-2d3e")
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan.ApprovalTest test_plan.DesignReviewTest -v 2>&1 | tail -5`
Expected: `FAILED (failures=…, errors=…)`. 원인: accept 결과가 `done`, `orch design-review` 가 `invalid choice`(exit 2), `AttributeError: module 'orch.plan' has no attribute 'design_link'`.

- [ ] **Step 3: tasks.py — ACTIVE, accept, fix, next_action**

모듈 docstring:

```python
"""태스크 spec, 상태(pending 포함), 선행 태스크(depends_on), 디스패치(start/fix/accept), 소유 경로 잠금,
사용자 승인 게이트(awaiting_user)."""
```

`from .config import Config`를 다음으로 바꾼다.

```python
from .config import DEFAULT_APPROVAL_ROLES, Config
```

`ACTIVE` 줄을 다음으로 바꾼다.

```python
# 소유 경로 잠금을 잡는 상태. awaiting_user 는 사용자 수정 요청으로 같은 경로를 다시 고칠 수 있어 잠금을 유지한다.
# pending(plan apply 가 만든, 아직 띄우지 않은 태스크)은 잡지 않는다.
ACTIVE = ("working", "reviewing", "changes", "approved", "launch_failed", "awaiting_user")
```

`role_title` 바로 뒤에 두 함수를 둔다.

```python
def approval_roles(p: Project) -> list[str]:
    """사용자 승인 대상 직무. config.toml 이 깨져도 accept 는 돌아야 하므로 읽지 못하면 기본값(designer)을 쓴다."""
    try:
        return Config.load(p.config_path).user_approval_roles
    except OrchError:
        return list(DEFAULT_APPROVAL_ROLES)


def load_design_reviews(p: Project) -> list[dict]:
    """state/design-review.json 의 디자인 승인 질문 이력 [{"id", "tasks": {T-id: 라운드}, "at"}]."""
    data = read_json(p.design_review_json, {})
    asks = data.get("asks") if isinstance(data, dict) else None
    return [a for a in asks if isinstance(a, dict)] if isinstance(asks, list) else []
```

`accept` 전체:

```python
def accept(p: Project, task_id: str) -> dict:
    """APPROVE 된 태스크를 끝낸다. 사용자 승인 대상 직무는 먼저 awaiting_user 로 두고(워커 해제, 잠금 유지),
    awaiting_user 에서 다시 accept 하면 사용자 승인으로 보고 done 이 된다."""
    require_coordinator(p)
    st = must_state(p, task_id)
    status = st.get("status")
    if status == "awaiting_user":
        st.update(status="done", done_at=now(), user_approved_at=now())
        st["history"].append({"stage": "user_approval", "round": st.get("round"), "at": now()})
        save_state(p, st)
        msg = notify(p, "progress", f"{task_id} [{role_title(p, st['role'])}] {st['title']} — 사용자 승인 (r{st['round']})")
        return {"id": task_id, "status": "done", "user_approved": True, "notified": msg["id"]}
    if status != "approved":
        raise OrchError(f"{task_id}: APPROVE 된 태스크(또는 사용자 승인 대기 awaiting_user)만 accept 한다"
                        f"(status={status})", 2)
    code, data = orca.call(["orchestration", "worker-release", "--dispatch", str(st.get("dispatch_id"))], check=False)
    released = code == 0 and bool(data.get("ok"))
    if st["role"] in approval_roles(p):
        st.update(status="awaiting_user", released=released, awaiting_since=now())
        label = "Codex APPROVE, 사용자 승인 대기"
    else:
        st.update(status="done", released=released, done_at=now())
        label = "APPROVE"
    save_state(p, st)
    msg = notify(p, "progress", f"{task_id} [{role_title(p, st['role'])}] {st['title']} — {label} (r{st['round']})")
    result = {"id": task_id, "status": st["status"], "released": released, "notified": msg["id"]}
    if not released:
        result["release_error"] = data
    return result
```

`fix` 전체:

```python
def fix(p: Project, cfg: Config, task_id: str, note: str | None = None, retry: bool = False,
        overrides: dict | None = None, force: bool = False) -> dict:
    require_coordinator(p)
    doc = load_doc(p, cfg, task_id)
    st = must_state(p, task_id)
    status = st.get("status")
    if retry:
        if not (status == "launch_failed" and st.get("stage") == "fix"):
            raise OrchError(f"{task_id}: --retry 는 fix 실행이 실패한 뒤에만 쓴다(status={status})", 2)
        rnd = st["round"]
    else:
        if status not in ("changes", "working", "awaiting_user"):
            raise OrchError(f"{task_id}: fix 는 changes, working(워커 실패 보고), awaiting_user(사용자 수정 요청) "
                            f"상태에서만 한다(status={status})", 2)
        # 최대 라운드는 Codex 리뷰 반복을 끊고 사용자에게 묻기 위한 것이다. awaiting_user 의 fix 는 이미 사용자 요청이다.
        if status != "awaiting_user" and st["round"] >= cfg.max_rounds:
            raise OrchError(f"{task_id}: 최대 {cfg.max_rounds} 라운드에 도달했다. "
                            "orch notify --type question 으로 메인에 판단을 요청한다", 1)
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
    # 터미널 재사용은 Orca 가 agent_unconfigured 로 거부하므로 하지 않는다. 이전 디스패치를 해제하고 새 워커를 띄운다.
    # awaiting_user 는 accept 때 이미 해제했다. 그때 해제에 실패했을 때만 다시 시도한다.
    extra = {} if status == "awaiting_user" and st.get("released") else release_previous(st)
    if not retry and status == "working":
        extra["forced"] = force
    if not retry and status == "awaiting_user":
        extra["user_requested"] = True
    return launch(p, cfg, doc, st, "fix", rnd, owns, note, overrides, extra)
```

`next_action` 전체(Task 1 판에 `awaiting_user` 분기를 더한다):

```python
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
        return (f"실행이 실패했거나 실행 도중 끊겼다. `orca orchestration worker-list` 로 이 태스크 워커가 떠 있는지 확인하고, "
                f"남은 워커는 Orca recovery 참조({RECOVERY_REF})대로 정리한다. 그다음 {retry_command(st)}. "
                f"복구할 수 없으면 orch drop {task_id} --reason \"<이유>\"")
    if status in FINISHED:
        return None
    return f"알 수 없는 상태({status}). .orchestration/state/tasks/{task_id}.json 을 확인한다"
```

- [ ] **Step 4: plan.py — design_review**

import 를 다음으로 바꾼다(`.config` 줄부터 `.util` 줄까지).

```python
from . import comm
from .ask import build_render
from .config import Config
from .globs import any_overlap
from .paths import OrchError, Project
from .tasks import (FINISHED, all_states, check_lock, deps_of, format_unmet, load_design_reviews, load_doc, load_state,
                    max_task_number, owns_of, parse_text, save_state, start, task_path, unmet_deps, validate)
from .util import git, git_commit_paths, now, read_json, require_coordinator, write_json
```

모듈 docstring:

```python
"""태스크 그래프. docs/planning/tasks.toml 을 검증해 태스크 spec 과 pending 상태로 만들고(plan apply),
시작할 수 있는 태스크를 동시 상한까지 띄우며(start-ready), 디자인을 모아 사용자 승인을 받는다(design-review)."""
```

`REQUIRED` 줄 아래에 상수 둘을 둔다.

```python
LINK_RE = re.compile(r"https://claude\.ai/[\w./-]*?artifact/[\w-]+", re.ASCII)
REVIEW_WAIT_STATUSES = ("pending", "working", "reviewing", "changes", "approved", "launch_failed")
```

파일 끝에 추가한다.

```python
def design_link(p: Project, task_id: str) -> str | None:
    """`docs/tasks/T-xxx/report.md` 의 마지막 claude.ai 아티팩트 주소. 리포트는 라운드마다 절을 더하므로 마지막이 최신이다."""
    try:
        text = (p.root / "docs" / "tasks" / task_id / "report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    found = LINK_RE.findall(text)
    return found[-1] if found else None


def design_review(p: Project, cfg: Config) -> dict:
    """승인 대상 직무 태스크가 모두 Codex 리뷰를 지나 awaiting_user 가 됐으면 메인에 승인 질문 하나를 올린다."""
    require_coordinator(p)
    roles = cfg.user_approval_roles
    states = [st for st in all_states(p) if st.get("role") in roles]
    busy = [f"{st['id']}({st.get('status')})" for st in states if st.get("status") in REVIEW_WAIT_STATUSES]
    if busy:
        raise OrchError("승인 대상 태스크가 아직 끝나지 않았다: " + ", ".join(busy)
                        + ". 모두 Codex 리뷰를 거쳐 awaiting_user 가 되면 다시 한다", 2)
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
```

- [ ] **Step 5: cli.py**

`GRAPH_COMMANDS` 줄:

```python
GRAPH_COMMANDS = {"plan", "start-ready", "design-review"}  # 태스크 그래프 명령(코디네이터). add_graph_commands·run_graph 가 처리한다
```

`add_graph_commands` 전체:

```python
def add_graph_commands(sub) -> None:
    """태스크 그래프 명령(코디네이터)."""
    s = sub.add_parser("plan", help="태스크 그래프(docs/planning/tasks.toml) 적용(코디네이터)")
    plan_sub = s.add_subparsers(dest="plan_cmd", required=True)
    a = plan_sub.add_parser("apply", help="검증 후 태스크 spec·pending 상태 생성")
    a.add_argument("--append", action="store_true", help="이미 적용한 계획에 새 key 만 더한다")
    sub.add_parser("start-ready", help="시작할 수 있는 pending 태스크를 max_parallel 까지 띄운다(코디네이터)")
    sub.add_parser("design-review", help="awaiting_user 디자인을 모아 사용자 승인 질문 하나를 올린다(코디네이터)")
```

`run_graph` 전체:

```python
def run_graph(p, cfg, args) -> int:
    from . import plan

    if args.cmd == "plan":
        emit(plan.apply(p, cfg, args.append))
        return 0
    if args.cmd == "design-review":
        emit(plan.design_review(p, cfg))
        return 0
    out = plan.start_ready(p, cfg)
    emit(out)
    return 1 if any(s["status"] == "launch_failed" for s in out["started"]) else 0
```

- [ ] **Step 6: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan test_tasks test_comm test_review -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 343 tests in …` 다음 줄 `OK`

- [ ] **Step 7: 커밋**

```bash
git add -- orca-team/lib/orch/tasks.py orca-team/lib/orch/plan.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py && git commit -m "$(cat <<'EOF'
feat(orca-team): 디자인 사용자 승인 게이트(awaiting_user)와 orch design-review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/lib/orch/tasks.py orca-team/lib/orch/plan.py orca-team/lib/orch/cli.py orca-team/tests/test_plan.py
```

---

### Task 6: 템플릿(planner·coordinator·메인 표시 규칙)

기획 지침(설계 4.4), 코디네이터 자율 진행 규칙(7 절), 메인 표시 규칙(4.3)을 템플릿에 쓴다. 문구 테스트가 지침의 핵심 단어와, 지침 안 예시(질문 JSON·tasks.toml)가 orch 검증을 통과하는지 본다. `config.toml`·`worker-spec.md`는 Task 1 에서 이미 바꿨다.

**Files:**
- Modify: `orca-team/templates/roles/planner.md`(전체 교체)
- Modify: `orca-team/templates/coordinator.md`(전체 교체)
- Modify: `orca-team/templates/CLAUDE.section.md`(전체 교체)
- Test: `orca-team/tests/test_templates.py`(import, 테스트 4개), `orca-team/tests/test_skills.py`(`DOCS`)

**Interfaces:**
- Consumes: Task 2 의 `ask.validate_question`, 명령 `orch ask-user`·`orch answer`; Task 3 의 `plan.parse_plan`, `plan.graph_problems`, `orch plan apply [--append]`; Task 4 의 `orch start-ready`; Task 5 의 `orch design-review`, `awaiting_user`.
- Produces: 프로젝트에 scaffold 되는 지침 문서. `test_skills.test_documented_orch_commands_exist`가 문서의 모든 `orch <명령>`이 실제 명령인지 본다(planner.md 를 대상에 더한다).

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_templates.py`의 import 를 다음으로 바꾼다.

```python
import json
import re
import tomllib
import unittest

from helpers import PLUGIN
from orch import ask, plan
from orch.config import Config
```

`TemplateTest` 끝(`test_coordinator_restart_follows_status_next` 뒤)에 추가한다.

```python
    def test_planner_interview_rules(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        for key in ("orch ask-user --task", "--json -", "timeout: 600000", "--wait out-NNNN", "--pending", "exit 4",
                    "exit 3", "AskUserQuestion", "한 번에 한 질문", "예시, 추천, 추천 사유", "자동 결정", "확정 (추천)",
                    "수정할 부분 있음", "docs/planning/spec.md", "docs/planning/contracts.md",
                    "docs/planning/tasks.toml", "docs/planning/interview.md", "depends_on", "질문 수에는 상한이 없다",
                    "`context`", "4000자 이하"):
            self.assertIn(key, text)
        for item in ("목적과 성공 기준", "사용자와 사용 맥락", "기능 범위", "사용자 흐름", "화면 목록", "데이터와 상태",
                     "예외와 오류 처리", "비기능(성능, 접근성, 보안, 지원 환경)", "기술 스택과 실행 환경",
                     "인터페이스 약속(API, 데이터 형태, 모듈 경계)", "디자인 방향(톤, 참고 자료)", "범위 밖"):
            self.assertIn(f"- {item}\n", text)

    def test_planner_examples_pass_orch_validation(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        questions = re.findall(r"<<'EOF'\n(.*?)\nEOF", text, re.S)
        self.assertEqual(len(questions), 2)  # 일반 질문, 확정 질문
        checked = [ask.validate_question(json.loads(raw)) for raw in questions]
        confirm = checked[1]  # 확정 질문: 요약은 context(기획 핵심, 자동 결정, 태스크 그래프 표), example 은 짧게
        for text_part in ("기획 핵심", "자동 결정", "| key | 직무 | 선행 |"):
            self.assertIn(text_part, confirm["context"])
        self.assertLess(len(confirm["example"]), 100)
        block = re.search(r"```toml\n(.*?)```", text, re.S).group(1)
        cfg = Config(tomllib.loads((T / "config.toml").read_text(encoding="utf-8")))
        tasks = plan.parse_plan(tomllib.loads(block), cfg)
        self.assertEqual(plan.graph_problems(tasks), [])
        self.assertTrue(any(t.depends_on for t in tasks))

    def test_main_display_rules(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for key in ("orch answer <id>", "render.question", "render.header", "render.options", "AskUserQuestion",
                    "단일 선택", "Other", "바꾸거나 합치지", "ask", "approval", "question", "render.context"):
            self.assertIn(key, text)
        self.assertRegex(text, r"`render\.context` 가 있으면 .*먼저.*평문.*AskUserQuestion")
        self.assertNotIn("tell --reply", text)

    def test_coordinator_parallel_rules(self):
        text = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("orch task new planner", "orch plan apply", "orch plan apply --append", "orch start-ready",
                    "orch design-review", "awaiting_user", "docs/planning/spec.md", "docs/planning/contracts.md",
                    "docs/planning/interview.md", "max_parallel", "launch_failed", "done 또는 dropped",
                    'orch fix T-xxx --note "<사용자 요청', "사용자에게 묻는 경우는 셋뿐"):
            self.assertIn(key, text)
        self.assertNotIn("이 태스크를 기다리던 태스크가 있으면 `orch start`", text)
```

`orca-team/tests/test_skills.py`의 `DOCS`를 다음으로 바꾼다.

```python
DOCS = [PLUGIN / "skills/init/SKILL.md", PLUGIN / "skills/up/SKILL.md",
        PLUGIN / "templates/coordinator.md", PLUGIN / "templates/CLAUDE.section.md",
        PLUGIN / "templates/roles/planner.md"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_skills -v 2>&1 | tail -5`
Expected: `FAILED (failures=…, errors=…)`. 새 테스트 4개가 실패한다(`AssertionError: 'orch ask-user --task' not found …`, `AttributeError: 'NoneType' object has no attribute 'group'`). `test_skills`는 아직 통과한다.

- [ ] **Step 3: planner.md**

`orca-team/templates/roles/planner.md` 전체:

````markdown
# planner — 기획

## 책임
- 사용자를 인터뷰해 제품 기획과 기술 설계(인터페이스 약속)를 같은 인터뷰에서 확정하고, 병렬로 실행할 태스크 그래프를 낸다.
- 의문점을 남기지 않는다. 디자인·개발·테스트 워커는 사용자에게 다시 묻지 않고 이 문서들만 보고 일한다.

## 입력
- `.orchestration/project.md`, 태스크 spec, `.orchestration/config.toml`(직무와 소유 경로)
- 기존 `docs/planning/`. 증분 기획이면 먼저 읽고 추가 요청만 묻는다.

## 인터뷰 방식
- 질문은 `orch ask-user` 로만 한다. AskUserQuestion 은 쓰지 않는다(훅이 막는다). 질문은 코디네이터를 거치지 않고 메인을 통해 사용자에게 바로 간다.
- 한 번에 한 질문만 한다. 질문은 짧고 분명하게 쓰고, 길게 설명하지 않는다.
- 선택형 질문에는 항상 예시, 추천, 추천 사유를 넣는다.
- 답을 받으면 다음 질문으로 넘어간다. 앞선 답과 모순되면 그 모순을 짚는 질문을 한다.
- 답을 받을 때마다 `docs/planning/interview.md` 에 질문 번호, 질문, 답을 적는다.
- 기술적으로 답이 자명한 항목은 묻지 않는다. 추천안으로 정하고 최종 요약에 "자동 결정"으로 표시한다.

## 점검표
아래 항목이 모두 "결정됨" 또는 "범위 밖"이 될 때까지 인터뷰를 이어 간다. 질문 수에는 상한이 없다.

- 목적과 성공 기준
- 사용자와 사용 맥락
- 기능 범위
- 사용자 흐름
- 화면 목록
- 데이터와 상태
- 예외와 오류 처리
- 비기능(성능, 접근성, 보안, 지원 환경)
- 기술 스택과 실행 환경
- 인터페이스 약속(API, 데이터 형태, 모듈 경계)
- 디자인 방향(톤, 참고 자료)
- 범위 밖

## 질문 보내기
질문을 JSON 으로 써서 표준 입력으로 넘긴다. `T-xxx` 는 이 태스크 ID 다. Bash 는 `timeout: 600000` 으로 실행한다(orch 가 답을 최대 9분 기다린다).

```bash
.orchestration/bin/orch ask-user --task T-xxx --json - <<'EOF'
{"question": "할 일 목록을 어디에 저장할까요?",
 "example": "브라우저 저장소면 서버 없이 바로 쓰지만 기기 간 동기화가 안 된다",
 "options": [{"label": "브라우저 localStorage", "description": "서버가 필요 없다. 기기마다 따로 저장된다"},
             {"label": "서버 DB", "description": "기기 간 동기화가 된다. 서버와 인증이 필요하다"}],
 "recommended": 0,
 "reason": "첫 버전 범위에서 서버 없이 끝낼 수 있다",
 "header": "저장 위치"}
EOF
```

| 필드 | 필수 | 규칙 |
|---|---|---|
| `question` | 예 | 한 문장(줄바꿈 없음), 300자 이하 |
| `example` | 예 | 질문을 구체화하는 예시, 300자 이하 |
| `options` | 아니오 | 없거나 2~4개. 각 항목은 `{"label": 30자 이하, "description": ...}` |
| `recommended` | options 가 있으면 예 | `options` 의 인덱스(0부터) |
| `reason` | options 가 있으면 예 | 추천 사유 |
| `header` | 아니오 | 12자 이하 꼬리표. 없으면 "기획" |
| `context` | 아니오 | 마크다운, 4000자 이하. 메인이 질문 전에 사용자에게 그대로 보여 준다. 긴 요약·표가 필요할 때만 쓴다 |

- 자유 답을 받을 질문은 `options`·`recommended`·`reason` 을 빼고 `question`·`example` 만 보낸다.
- exit 0: `{"id": "out-NNNN", "answer": "<답>"}`. 답을 interview.md 에 적고 다음 질문으로 간다.
- exit 4: `{"id": "out-NNNN", "status": "waiting"}`. 9분 안에 답이 없었다. `.orchestration/bin/orch ask-user --task T-xxx --wait out-NNNN` 을 같은 방식(Bash `timeout: 600000`)으로 다시 실행해 이어서 기다린다. 새 질문을 보내지 않는다.
- exit 3: 답을 기다리는 질문이 이미 있다. 오류 문구의 `--wait out-NNNN` 으로 그 답부터 기다린다.
- exit 2: 질문 형식 오류다. 오류 문구에 적힌 필드를 고쳐 다시 보낸다.

## 확정
점검표가 끝나면 아래 산출물 네 개를 먼저 쓴다. 아직 커밋하지 않는다. 마지막 질문은 확정 질문이다. 요약은 `context`(마크다운, 4000자 이하)에 담는다: 기획 핵심, 자동 결정 목록, 태스크 그래프 표(key, 직무, 선행 key). 메인이 이 요약을 질문 전에 그대로 보여 준다. `example` 은 짧게 둔다.

```bash
.orchestration/bin/orch ask-user --task T-xxx --json - <<'EOF'
{"question": "이 기획으로 확정할까요?",
 "example": "전체 문서는 docs/planning/ 에 있다",
 "context": "## 기획 핵심\n할 일을 추가·완료·삭제하는 정적 웹앱. 데이터는 브라우저에만 저장한다.\n\n## 자동 결정\n- 정적 HTML 한 장, 빌드 도구 없음\n- 저장은 localStorage\n\n## 태스크 그래프\n| key | 직무 | 선행 |\n|---|---|---|\n| todo-design | designer | - |\n| todo-store | developer | - |\n| todo-ui | developer | todo-design, todo-store |",
 "options": [{"label": "확정", "description": "기획 문서를 커밋하고 디자인·개발을 시작한다"},
             {"label": "수정할 부분 있음", "description": "고칠 부분을 Other 로 적어 주세요"}],
 "recommended": 0,
 "reason": "점검표 항목이 모두 정해졌다",
 "header": "기획 확정"}
EOF
```

메인 화면에는 "확정 (추천)"과 "수정할 부분 있음"으로 보인다. "수정할 부분 있음"이면 그 부분을 다시 인터뷰하고 문서를 고친 뒤 확정 질문을 다시 한다. "확정"이면 `docs/planning/` 과 리포트를 커밋하고 worker_done 을 보낸다.

## 산출물
- `docs/planning/spec.md`: 기능마다 목적, 범위, 흐름, 수용 기준(검증 가능한 문장), 범위 밖.
- `docs/planning/contracts.md`: API(경로, 요청, 응답, 오류), 공유 데이터 형태, 모듈 경계와 소유 경로. 병렬로 개발하는 워커들이 이 문서만 보고 서로의 결과를 가정할 수 있어야 한다.
- `docs/planning/tasks.toml`: 아래 형식의 태스크 그래프.
- `docs/planning/interview.md`: 질문 번호, 질문, 답. 코디네이터가 워커 질문에 답할 때 근거로 쓴다.

```toml
[[task]]
key = "todo-design"                 # 영문 소문자·숫자·하이픈, 파일 안에서 유일
role = "designer"                   # config.toml 의 직무
title = "할 일 화면 디자인"            # 한 줄
owns = ["docs/design/todo/**"]      # 비지 않은 glob 목록, 쉼표 금지
depends_on = []                     # 선행 태스크 key
target = "할 일 목록 화면(빈 상태·목록·오류)"
change = "Claude Design 으로 화면 디자인과 토큰을 만든다"
constraints = "contracts.md 2절 데이터 형태를 따른다"
acceptance = ["spec.md 화면 목록의 모든 상태가 디자인에 있다"]

[[task]]
key = "todo-store"
role = "developer"
title = "할 일 저장 모듈"
owns = ["src/store/**"]
depends_on = []
target = "src/store/"
change = "localStorage 기반 할 일 저장 모듈"
constraints = "contracts.md 1절 인터페이스를 지킨다"
acceptance = ["node --test src/store 통과"]

[[task]]
key = "todo-ui"
role = "developer"
title = "할 일 화면 구현"
owns = ["src/ui/**"]
depends_on = ["todo-design", "todo-store"]
target = "src/ui/"
change = "디자인대로 화면을 구현하고 저장 모듈을 쓴다"
constraints = "contracts.md 1절 인터페이스만 쓴다"
acceptance = ["index.html 에서 추가·완료·삭제가 된다"]
```

- 서로 의존하지 않는 태스크에는 `depends_on` 을 걸지 않는다. 병렬 실행이 기본이다. 순환은 orch 가 거부한다.
- 의존으로 이어지지 않은 두 태스크의 `owns` 가 겹치면 orch 가 거부한다. 병렬 태스크끼리는 경로를 나눈다.
- `target`·`change`·`constraints` 는 비지 않은 문자열이고 `## ` 로 시작하는 줄을 넣지 않는다. `constraints` 에는 지킬 `contracts.md` 절을 적는다. `acceptance` 는 한 줄짜리 문장 목록이다.
- 화면 디자인(designer) 태스크는 Codex 리뷰 뒤 사용자 일괄 승인을 받는다. 디자인을 입력으로 쓰는 개발 태스크는 `depends_on` 에 디자인 key 를 넣는다.
- 증분 기획: 기존 `[[task]]` 는 한 글자도 바꾸지 않고 새 `[[task]]` 만 덧붙인다(`orch plan apply --append` 가 기존 key 변경·삭제를 거부한다).
- tasks.toml 이 검증을 통과하지 못하면 리뷰가 REQUEST_CHANGES 로 돌려보낸다.

## 수정 라운드에서 이어 가기
이전 워커가 질문의 답을 기다리다 끝났을 수 있다. 먼저 `docs/planning/interview.md` 를 읽고 `.orchestration/bin/orch ask-user --task T-xxx --pending` 을 실행한다. `pending` 의 답(이전 워커가 받지 못한 답)은 interview.md 에 옮겨 적는다. `waiting` 에 id 가 있으면 새 질문 대신 `--wait <그 id>` 로 그 답부터 기다린다.

## 품질 기준
- 수용 기준은 테스트로 확인할 수 있게 쓴다.
- 모호한 표현("적절히", "빠르게")에 수치나 조건을 붙인다.

## 하지 않는 것
- 화면 디자인, 코드, 테스트 작성.
````

- [ ] **Step 4: coordinator.md**

`orca-team/templates/coordinator.md` 전체. 기존 테스트가 보는 문구(`worker-release` 를 손으로 하지 않는다, `untracked`·`released: false` 줄, `orch drop T-xxx --force`, spec 커밋 줄 2개, `orch review` 줄마다 `run_in_background: true`)를 그대로 둔다.

```markdown
# 코디네이터 운영 절차

너는 orca-team 코디네이터다. 사용자와 직접 대화하지 않는다. 메인 에이전트가 사용자와 대화하고, 너는 메인이 넘긴 요청을 기획 워커에게 먼저 맡긴다. 기획 워커가 사용자 인터뷰로 기획을 확정하면 그 태스크 그래프(`docs/planning/tasks.toml`)대로 직무 워커를 병렬로 띄우고 Codex 리뷰를 돌린다.

모든 명령은 `.orchestration/bin/orch`(아래 `orch`)로 한다. Orca 배치·모델 플래그를 직접 고르지 않는다.

## 금지
- 산출물(기획 문서, 디자인, 코드, 테스트) 작성·수정. 쓸 수 있는 곳은 `.orchestration/` 뿐이고, 그중 `.orchestration/state/` 는 orch 만 쓴다(훅이 막는다).
- AskUserQuestion. 사용자 결정이 필요하면 `orch notify --type question`.
- `orca orchestration check` 직접 호출. 메일함은 `orch wait` 로만 본다(훅이 막는다).
- `[main]` 메시지에 `orca orchestration reply`. 메인에게는 `orch notify` 로만 답한다(reply 는 메인 입력란을 깨운다). `orch wait` 가 받은 `[main]` 메시지 id 를 기록하고 훅이 그 id 로의 reply 를 막는다.
- Claude 서브에이전트(Agent tool)·Workflow 로 작업 대신하기. 모든 작업은 `orch start`·`orch start-ready` 로 Orca 워커에게 맡긴다.
- `orca orchestration worker-start` 직접 호출. 실패한 start·fix 를 `--retry` 없이 다시 실행하기.
- 기획 인터뷰에 끼어들기. 기획 워커의 `orch ask-user` 질문은 메인으로 바로 간다.

## 시작
1. `orch bind` — Run 을 만들거나 기존 Run 에 다시 붙는다.
2. `orch status` — 태스크마다 `next` 에 적힌 다음 행동을 따른다(`null` 이면 끝난 태스크다). `pending` 이면 `orch start-ready`, `awaiting_user` 면 `orch design-review` 또는 메인 답 대기다. 끊긴 리뷰(`reviewing`)는 `orch review T-xxx` 를 Bash `run_in_background: true` 로 다시 돌리면 된다. `next` 가 `orch fix T-xxx --note "워커 종료"` 인데 `docs/tasks/T-xxx/report.md` 와 `[T-xxx]` 작업 커밋이 이미 있으면 워커가 worker_done 을 보낸 뒤 끝난 것일 수 있으니 `orch review T-xxx` 를 Bash `run_in_background: true` 로 먼저 한다. `.orchestration/state/` 의 JSON 은 직접 고치지 않는다(훅이 막는다).
3. `.orchestration/project.md`, `.orchestration/config.toml`, `.orchestration/roles/*.md`, 있으면 `docs/planning/` 을 읽는다.
4. `orch notify --type progress "코디네이터 준비 완료"`.
5. 대기 루프로 간다.

## 대기 루프
- `orch wait` 를 Bash `run_in_background: true` 로 띄운다. 한 번에 하나만 띄운다(중복이면 exit 3).
- 완료 알림이 오면 출력 JSON 의 `result.messages` 를 모두 처리한다. 그다음 `orch wait --ack <result.deliveryId>` 를 다시 백그라운드로 띄운다. 메시지가 없거나 타임아웃이면 `--ack` 없이 다시 띄운다.
- 워커 메시지가 어느 태스크인지는 `orch which <taskId 또는 dispatchId>` 로 찾는다.

| 메시지 | 처리 |
|---|---|
| subject `[main] reply out-NNNN` | 네가 올린 question·approval 에 대한 사용자 답. 디자인 승인 답이면 아래 "디자인 일괄 승인", 워커 ask 였으면 `orca orchestration reply --id <워커 메시지 id> --body "<답>"`, 그 밖에는 기다리던 일에 반영한다 |
| subject `[main] ...` | 사용자 요청. 아래 "요청 처리" |
| question (워커 ask) | 먼저 `docs/planning/spec.md`, `docs/planning/contracts.md`, `docs/planning/interview.md` 와 spec·직무 정의·project.md 로 답한다: `orca orchestration reply --id <id> --body "<답>"`. 문서가 다루지 않는 제품 결정이 필요하거나 문서끼리 모순될 때만 아래 "사용자에게 묻기" 형식으로 `orch notify --type question` 하고, 답이 오면 reply 한다 |
| worker_done (outcome succeeded) | `orch review T-xxx` 를 Bash `run_in_background: true` 로 실행 |
| worker_done (outcome failed) | 리포트를 읽고 `orch fix T-xxx --note "<원인과 지시>"` 한 뒤 `orch start-ready`. 지시로 풀 수 없으면 `orch notify --type question` |
| escalation | 내용을 정리해 `orch notify --type question` |
| heartbeat, 그 외 status | 처리할 것 없음 |

리뷰 완료 알림이 오면 출력의 verdict 로 정한다.
- APPROVE → `orch accept T-xxx`.
  - 출력 `status` 가 `done` 이고 기획(planner) 태스크였으면 아래 "요청 처리" 4단계(`orch plan apply`)로 간다. 그 밖에는 `orch start-ready`.
  - 출력 `status` 가 `awaiting_user` 면(사용자 승인 대상 직무, 기본 designer) 워커는 해제됐고 소유 경로 잠금은 남는다. `orch start-ready` 를 한 뒤 `orch design-review` 를 해 본다. exit 2 면 아직 리뷰 중인 승인 대상 태스크가 있다는 뜻이다(오류 문구가 무엇을 기다리는지 알려 준다).
- REQUEST_CHANGES → `orch fix T-xxx`(리뷰 파일을 읽고 필요하면 `--note`) 다음 `orch start-ready`. fix 는 이전 워커를 스스로 해제하고 새 워커를 띄우므로(터미널 재사용 없음, `--model`·`--effort` 적용) `worker-release` 를 손으로 하지 않는다. 최대 라운드 초과로 거부되면 "사용자에게 묻기" 형식으로 `orch notify --type question` 한다(리뷰 요약과 선택지). 사용자가 중단을 고르면 `orch drop`(아래 "중단").
- exit≠0 → 출력의 지시를 따른다. 해결하지 못하면 `orch notify --type question`.
- 출력에 `untracked`(리뷰 중 새로 생긴 추적 안 된 파일)가 있고 그중 이 태스크 소유 경로 안 파일이 있으면 다음 `orch fix T-xxx --note` 에 그 목록을 넣는다(커밋하거나 지우게 한다).
- `orch accept`·`orch drop` 출력이 `released: false` 거나 `release_error` 가 있으면 `orch notify --type progress` 로 메인에 워커 해제 실패를 알린다.

## 요청 처리
1. 사용자 요청을 받으면 먼저 기획 태스크 하나를 만든다: `orch task new planner "<요청 요약>"`. `.orchestration/tasks/T-xxx.md` 의 Target·Change·Constraints·Acceptance 를 채우고, Change 에 사용자 요청 원문을 넣는다. `docs/planning/tasks.toml` 이 이미 있고 사용자가 추가 요청만 했다면 증분 기획을 맡긴다: Change 에 "기존 `docs/planning/` 을 읽고 추가 요청만 인터뷰한다. tasks.toml 의 기존 태스크는 그대로 두고 새 `[[task]]` 만 덧붙인다"를 적는다. `<...>` 자리표시가 남으면 start 가 거부한다.
2. spec 을 커밋한다: `git add -- .orchestration/tasks/T-xxx.md && git commit -m "[T-xxx] spec: <제목>" -- .orchestration/tasks/T-xxx.md`. 워커와 인덱스를 같이 쓰므로 경로 없는 `git commit` 은 쓰지 않는다(남이 스테이징한 파일까지 커밋된다).
3. `orch start T-xxx`. 기획 워커는 `orch ask-user` 로 메인을 거쳐 사용자를 직접 인터뷰한다. 너는 worker_done 을 기다린다.
4. 기획 태스크를 accept 하면 `orch plan apply`(증분 기획이면 `orch plan apply --append`)로 tasks.toml 을 태스크 spec 과 `pending` 상태로 만든다. orch 가 spec 을 만들고 `[plan] apply` 커밋까지 하므로 `orch task new` 를 따로 하지 않는다. 이어서 `orch start-ready`.
5. `orch notify --type progress "<태스크 목록: id, 직무, 제목, 선행 태스크>"`.
6. `orch start-ready` 는 `pending` 태스크 중 선행 태스크가 모두 done 이고 잠금이 겹치지 않는 것을 tasks.toml 순서로 `max_parallel`(config.toml, 기본 4)까지 띄운다. `waiting` 의 reason 은 deps(선행 대기), lock(소유 경로 잠금), limit(동시 상한), spec(spec 오류. 고쳐 커밋한다)이다. `started` 에 `launch_failed` 가 있으면(exit 1) `orch status` 의 `next` 대로 복구한다. accept·fix·drop 을 하거나 디자인 승인 답을 처리할 때마다 `orch start-ready` 를 한다.
7. 모든 태스크가 done 또는 dropped 면 `orch notify --type done "<결과 요약, 바뀐 주요 파일, 태스크별 리뷰 판정>"`.
8. 진행 중 범위를 더하면 spec 파일에 "추가 범위"로 적고 커밋한다: `git add -- .orchestration/tasks/T-xxx.md && git commit -m "[T-xxx] spec: 추가 범위" -- .orchestration/tasks/T-xxx.md`. 그다음 `orca orchestration send --to dispatch:<dispatchId> --subject "추가 범위" --body "<내용>"` 으로 워커에게 알린다.
9. tasks.toml 오류는 사용자에게 묻지 않는다. 리뷰 사전 검사가 plan apply 와 같은 규칙으로 잡아 REQUEST_CHANGES 로 돌려보내므로 리뷰 파일을 읽고 `orch fix <기획 태스크> --note "<orch 오류>"`. accept 뒤 `orch plan apply` 가 exit 2 로 실패하면 오류 원문을 Change 에 적은 새 기획 태스크로 tasks.toml 을 고치게 한다.

## 디자인 일괄 승인
- config.toml 의 `user_approval_roles`(기본 designer) 태스크는 Codex APPROVE 뒤 `orch accept` 하면 `done` 이 아니라 `awaiting_user` 가 된다.
- 승인 대상 직무 태스크가 모두 `awaiting_user` 이거나 끝나면 `orch design-review`. 디자인 링크(리포트의 claude.ai 아티팩트 주소)를 모은 승인 질문 하나가 메인으로 간다. 같은 화면을 이미 물었으면 exit 3 이다. 답을 기다린다.
- 답(`[main] reply out-NNNN`)을 해석한다.
  - 승인한 화면: `orch accept T-xxx`(awaiting_user → done). 이 경우만 사용자 승인으로 인정한다.
  - 수정을 요청한 화면: `orch fix T-xxx --note "<사용자 요청 원문>"`. 최대 라운드와 상관없이 새 워커가 고친다. 고친 화면이 다시 awaiting_user 가 되면 `orch design-review` 는 그 화면들만 묻는다.
  - 어느 화면인지 알 수 없으면 "사용자에게 묻기" 형식의 확인 질문 하나를 `orch notify --type question` 으로 올린다.
  - 처리가 끝나면 `orch start-ready`.
- 디자인에 의존하지 않는 태스크는 승인을 기다리지 않는다. 그래프의 depends_on 만 따른다.

## 사용자에게 묻기
사용자에게 묻는 경우는 셋뿐이다.
- 디자인 일괄 승인(`orch design-review` 가 만든다)
- 리뷰가 최대 라운드를 넘었을 때
- `docs/planning/` 문서로 답할 수 없는 문제(문서가 다루지 않는 제품 결정, 문서끼리의 모순)

`orch notify --type question` 본문은 이 형식을 따른다: 질문 한 문장, `예:` 로 시작하는 예시, 선택지 2~4개, 추천 선택지와 추천 사유.

## 중단
`orch drop T-xxx --reason "<이유>"` 는 태스크를 `dropped` 로 바꾸고 소유 경로 잠금을 푼다. 워커가 있으면 해제하고 메인에 progress 로 알린다. 다음 경우에만 쓴다.
- 최대 라운드에 도달했고 메인(사용자)이 중단하기로 했을 때.
- `launch_failed` 를 복구할 수 없을 때(`orch status` 의 `next` 를 따라도 다시 띄울 수 없을 때).
- 선행 태스크가 dropped 라 시작할 수 없는 `pending` 태스크를 메인(사용자)이 진행하지 않기로 했을 때.

`working` 태스크는 워커가 끝났을 때(worker-show 의 stage 가 settled 이거나 liveness 가 exited 등)만 drop 된다. 워커가 일하는 중이거나 확인할 수 없으면 거부된다. `orch fix` 도 `working` 태스크에는 같은 규칙으로 거부하고 `--force` 로만 넘긴다(이력에 `forced` 로 남는다). 잠금을 풀면 다음 태스크가 같은 파일을 동시에 고칠 수 있기 때문이다. 워커를 먼저 멈추거나 worker_done 을 기다린다. 그래도 중단해야 하면 `orch drop T-xxx --force --reason "<이유>"`(이력과 메인 알림에 강제 중단으로 남는다).

drop 한 태스크는 다시 시작하지 않는다. 다시 하려면 `orch task new` 로 새 태스크를 만든다.

## 직무 선택
- planner: 사용자 인터뷰(`orch ask-user`), 기획(`docs/planning/spec.md`), 인터페이스 약속(`docs/planning/contracts.md`), 태스크 그래프(`docs/planning/tasks.toml`)
- designer: 앱·웹 화면 디자인(Claude Design), 디자인 시스템, 래스터 에셋(/orca-team:image)
- developer: 구현
- tester: 테스트 작성·실행, QA 리포트

## 디자인 산출물 규칙
- 앱·웹 화면 디자인은 designer 가 Claude Design(Artifact 도구의 Design 타입)으로 만든다.
- 래스터 에셋(일러스트, 아이콘 비트맵, 배경, 사진풍 이미지)만 /orca-team:image(Codex)로 만들고, 요청한 워커가 직접 보고 검토한다.
- SVG·CSS·다이어그램은 코드로 만든다.
```

- [ ] **Step 5: CLAUDE.section.md**

`orca-team/templates/CLAUDE.section.md` 전체:

```markdown
<!-- orca-team -->
## orca-team 오케스트레이션

이 저장소는 orca-team 플러그인으로 운영한다. 세션을 시작하면 역할부터 판별한다.

| 첫 프롬프트 | 역할 | 따를 문서 |
|---|---|---|
| `[orca-team:coordinator]` 로 시작 | 코디네이터 | `.orchestration/coordinator.md` |
| Orca 디스패치 프리앰블(Task ID·Dispatch ID) | 직무 워커 | 프리앰블의 spec |
| 그 외 | 메인 | 아래 "메인 규칙" |

### 메인 규칙
- 사용자와의 대화에만 집중한다. 기획·디자인·코드·테스트 산출물을 직접 만들지 않는다(훅이 막는다). 서브에이전트·Workflow 로 대신 시키지도 않는다.
- 사용자 요청은 원문과 맥락을 담아 `.orchestration/bin/orch tell "<요청>"` 으로 코디네이터에게 넘기고, 넘겼다고 한 줄로 알린다.
  - `-` 로 시작하는 메시지는 플래그로 읽히지 않게 `.orchestration/bin/orch tell -- "<메시지>"` 로 보낸다.
- `orca orchestration` 을 직접 호출하지 않는다. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 들어온다.
- 코디네이터·기획 워커 메시지는 `.orchestration/bin/orch watch` 로 받는다. 항상 Bash `run_in_background: true` 로 하나 띄워 둔다. 끝나면(메시지 도착) 출력의 메시지를 처리하고 바로 다시 띄운다.
  - progress: 한두 줄로 전한다.
  - ask(기획 워커의 인터뷰 질문), approval(디자인 일괄 승인), question(코디네이터 질문): 아래 "질문 표시 규칙"대로 묻고, 답을 `.orchestration/bin/orch answer <id> "<답>"` 으로 돌려준다. `-` 로 시작하는 답은 `.orchestration/bin/orch answer <id> -- "<답>"`. 답은 `orch answer` 하나로만 돌려준다.
  - done: 결과, 바뀐 주요 파일, 리뷰 판정을 요약해 전한다.
- 세션을 다시 열었으면 `/orca-team:up` 을 먼저 실행한다.

### 질문 표시 규칙
- `render.context` 가 있으면 그 내용을 먼저 사용자에게 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부른다(선택지가 없으면 평문 질문 앞에 둔다). 요약하거나 자기 설명을 덧붙이지 않는다.
- 메시지에 `render` 가 있으면(ask, approval) 그대로 AskUserQuestion 에 넘긴다. 질문은 1개, 단일 선택(`multiSelect: false`)이다. question 은 `render.question`, header 는 `render.header`, 선택지는 `render.options` 의 label·description 을 순서 그대로 쓴다.
- `render.options` 가 없으면 AskUserQuestion 을 쓰지 않고 `render.question`(질문과 예시)을 평문으로 묻는다. 사용자가 채팅으로 답하면 그 내용을 그대로 `orch answer` 로 넘긴다.
- 사용자가 선택지를 고르면 고른 label 을, Other 로 자유 입력하면 그 내용을 그대로 넘긴다.
- `render` 가 없는 question 은 본문을 AskUserQuestion 질문 하나로 묻는다. 본문에 선택지가 있으면 그대로 옮긴다.
- 질문을 바꾸거나 합치지 않고, 설명을 덧붙이지 않는다. 메시지 여러 개가 한꺼번에 오면 하나씩 따로 묻는다.

### 위치
- 설정: `.orchestration/config.toml`(직무별 모델·effort·소유 경로, `max_parallel`, `user_approval_roles`)
- 기획: `docs/planning/`(spec.md, contracts.md, tasks.toml, interview.md)
- 태스크 spec: `.orchestration/tasks/`, 워커 리포트: `docs/tasks/`, Codex 리뷰: `docs/reviews/`
- 디자인: 앱·웹 화면은 Claude Design(소스 `docs/design/`), 래스터 에셋은 Codex `/orca-team:image`(`assets/`)
```

- [ ] **Step 6: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_skills -v 2>&1 | tail -3`
Expected: `OK`

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 347 tests in …` 다음 줄 `OK`

- [ ] **Step 7: 커밋**

```bash
git add -- orca-team/templates/roles/planner.md orca-team/templates/coordinator.md orca-team/templates/CLAUDE.section.md orca-team/tests/test_templates.py orca-team/tests/test_skills.py && git commit -m "$(cat <<'EOF'
docs(orca-team): 그릴링 기획 지침, 병렬 진행 코디네이터 규칙, 메인 질문 표시 규칙

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- orca-team/templates/roles/planner.md orca-team/templates/coordinator.md orca-team/templates/CLAUDE.section.md orca-team/tests/test_templates.py orca-team/tests/test_skills.py
```

---

### Task 7: README, 기반 설계 §13, 버전 0.3.0

**Files:**
- Modify: `README.md`(새 절, 복구 표, 개발 링크)
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md`(§13 끝에 절 추가)
- Modify: `orca-team/lib/orch/paths.py:8`(`VERSION`), `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Test: `orca-team/tests/test_cli.py:9`, `orca-team/tests/test_templates.py`(README 테스트 1개)

**Interfaces:**
- Consumes: Task 1–6 의 명령·상태·설정 이름(`orch ask-user`, `orch answer`, `orch plan apply`, `orch start-ready`, `orch design-review`, `pending`, `awaiting_user`, `max_parallel`, `user_approval_roles`).
- Produces: `paths.VERSION = "0.3.0"`(plugin.json·marketplace.json 과 같다).

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_cli.py`의 `test_version` 을 다음으로 바꾼다.

```python
    def test_version(self):
        self.assertIn("orca-team 0.3.0", self.orch("--version").stdout)
```

`orca-team/tests/test_templates.py`의 `TemplateTest` 끝에 추가한다.

```python
    def test_readme_summarizes_grilling_and_parallel(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("orch ask-user", "orch answer", "orch plan apply", "orch start-ready", "orch design-review",
                    "max_parallel", "user_approval_roles", "awaiting_user", "`pending`"):
            self.assertIn(key, text)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_cli test_templates.TemplateTest.test_readme_summarizes_grilling_and_parallel -v 2>&1 | tail -5`
Expected: `FAILED (failures=2)` — `'orca-team 0.3.0' not found in 'orca-team 0.2.0'`, `'orch ask-user' not found`.

- [ ] **Step 3: 버전**

- `orca-team/lib/orch/paths.py`: `VERSION = "0.2.0"` → `VERSION = "0.3.0"`
- `orca-team/.claude-plugin/plugin.json`: `"version": "0.2.0"` → `"version": "0.3.0"`
- `.claude-plugin/marketplace.json`: orca-team 항목의 `"version": "0.2.0"` → `"version": "0.3.0"`

- [ ] **Step 4: README**

`README.md`의 `## 사용` 절 끝(`이후에는 메인에게 평소처럼 요청한다. 세션을 다시 열면 \`/orca-team:up\`.` 줄) 뒤에 다음 절을 넣는다.

```markdown

## 그릴링 기획과 병렬 실행

- 요청을 받으면 코디네이터가 기획(planner) 태스크부터 띄운다. 기획 워커는 `orch ask-user` 로 메인을 거쳐 사용자를 직접 인터뷰한다. 질문은 한 번에 하나이고, 예시·추천 선택지·추천 사유가 붙는다. 메인은 질문의 `render` 를 그대로 AskUserQuestion 에 넘기고(긴 요약 `render.context` 가 있으면 먼저 평문으로 그대로 보여 준다) 답을 `orch answer <id> "<답>"` 으로 돌려준다. 답을 9분 안에 받지 못하면 `ask-user` 는 exit 4 로 끝나고 기획 워커가 `--wait` 로 이어서 기다린다.
- 기획 워커는 마지막에 확정 질문을 하고 `docs/planning/` 에 spec.md(기능), contracts.md(인터페이스 약속), tasks.toml(태스크 그래프), interview.md(질문과 답)를 커밋한다.
- 기획 태스크를 accept 하면 `orch plan apply` 가 tasks.toml 을 검증해 태스크 spec 과 `pending` 상태를 만든다(증분 기획은 `--append`). `orch start-ready` 는 선행 태스크가 모두 done 이고 소유 경로가 겹치지 않는 태스크를 `max_parallel`(기본 4)까지 한 번에 띄운다.
- `user_approval_roles`(기본 designer) 태스크는 Codex APPROVE 뒤 `awaiting_user` 로 기다린다. 모두 모이면 `orch design-review` 가 디자인 링크를 모은 승인 질문 하나를 메인에 올린다. 승인하면 done, 수정을 요청하면 새 라운드다.
- 설정은 `.orchestration/config.toml` 최상위 `max_parallel = 4`, `user_approval_roles = ["designer"]` 다. 0.2.0 으로 만든 프로젝트는 이 값이 없어도 기본값으로 돈다. 다만 `.orchestration/coordinator.md`, `.orchestration/roles/planner.md`, CLAUDE.md 의 orca-team 절은 scaffold 가 덮어쓰지 않으므로 새 흐름을 쓰려면 플러그인 `templates/` 의 같은 파일로 직접 바꾼다.
```

`## 복구` 절의 표를 다음으로 바꾼다.

```markdown
  | status | next |
  |---|---|
  | `pending` | `orch start-ready`. 선행 태스크가 dropped 면 메인에 묻고 `orch drop` |
  | `working` | 워커가 일하는 중이면 worker_done 대기. 끝났는데(settled) 정상 종료면 `orch review T-xxx` 를 백그라운드로, 실패·종료면 `orch fix T-xxx --note "워커 종료"` |
  | `reviewing` | 리뷰 프로세스가 없으면 `orch review T-xxx` 를 백그라운드로(끊긴 리뷰를 다시 돌린다) |
  | `approved` | `orch accept T-xxx` |
  | `awaiting_user` | 아직 묻지 않았으면 `orch design-review`. 물었으면 메인 답을 기다린 뒤 승인은 `orch accept T-xxx`, 수정 요청은 `orch fix T-xxx --note "<사용자 요청>"` |
  | `changes` | `orch fix T-xxx`. 최대 라운드면 메인에 묻고 `orch drop` |
  | `launch_failed` | Orca 에 남은 워커를 확인한 뒤 `orch start T-xxx --retry` 또는 `orch fix T-xxx --retry` |
  | `done`, `dropped` | 없음(`null`) |
```

`## 개발` 절의 마지막 두 줄(`- 설계: …`, `- 구현 계획: …`)을 다음으로 바꾼다.

```markdown
- 설계: `docs/superpowers/specs/2026-10-30-orca-team-design.md`, 그릴링 기획·병렬 실행 `docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md`
- 구현 계획: `docs/superpowers/plans/2026-10-30-orca-team.md`, `docs/superpowers/plans/2026-10-31-orca-team-grilling-parallel.md`
```

- [ ] **Step 5: 기반 설계 §13**

`docs/superpowers/specs/2026-10-30-orca-team-design.md` 끝(`### Codex 래퍼 내장 (2026-10-31)` 절 뒤)에 추가한다.

```markdown

### 그릴링 기획·병렬 실행 (2026-10-31, 0.3.0)

후속 설계 `2026-10-31-orca-team-grilling-parallel-design.md` 를 반영했다. 구현 계획은 `docs/superpowers/plans/2026-10-31-orca-team-grilling-parallel.md` 다.

- 새 명령: `orch ask-user`(기획 워커 → 메인 직통 질문, 2초 간격으로 답 파일 확인, 540초 뒤 exit 4), `orch answer`(메인. ask 는 `state/answers/<out-id>.json`, question·approval 은 Run 으로), `orch plan apply [--append]`·`orch start-ready`·`orch design-review`(코디네이터). §6 표의 `tell --reply` 는 호환용으로 남고 메인 지침은 `orch answer` 만 쓴다.
- 종료 코드 4(ask-user 답 대기 중)를 더했다.
- 태스크 상태: `pending`(plan apply 가 만들고 잠금 없음, next 는 `orch start-ready`), `awaiting_user`(사용자 승인 대기, 잠금 유지). front matter `depends_on` 은 `orch start` 도 검사한다.
- config.toml 최상위 `max_parallel`(기본 4, `working` 만 센다), `user_approval_roles`(기본 `["designer"]`).
- 구현 조정
  - tasks.toml 검증을 `orch review` 사전 검사에도 둔다. accept 뒤(done)에는 fix 로 기획 태스크를 되돌릴 수 없기 때문이다.
  - `awaiting_user` 의 fix 는 최대 라운드 검사를 하지 않는다(라운드 번호는 올린다). config.toml 이 깨지면 accept 는 승인 대상 직무를 기본값 designer 로 본다.
  - start-ready 는 deps → lock → limit 순으로 보고, spec 오류는 reason `spec`, 실행 실패가 있으면 exit 1 이다. 단건 `orch start` 는 `max_parallel` 을 보지 않는다.
  - design-review 는 같은 태스크·라운드를 다시 물으면 exit 3 이고, 링크는 report.md 의 마지막 claude.ai 아티팩트 주소다.
  - `state/plan.json` 은 key → T-id 와 지문(digest)을 담아 `--append` 의 기존 key 변경을 막는다.
  - 워커 spec 에 `선행 태스크: …` 줄과 contracts.md 를 먼저 읽으라는 지시를 넣었다.
  - `ask-user --json -` 로 표준 입력을 받는다. 기획 지침은 heredoc 을 쓴다. `--pending` 은 돌려준 답을 읽음으로 기록하고 대기 중 질문 id 를 함께 준다.
  - 질문 스키마에 선택 필드 `context`(마크다운, 4000자 이하)를 더했다. `render.context` 로 실리고, 메인은 그것을 먼저 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부른다. 확정 질문은 요약(기획 핵심, 자동 결정, 태스크 그래프 표)을, design-review 는 화면별 링크 목록을 `context` 에 담는다.
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `Ran 348 tests in …` 다음 줄 `OK`(`test_version_matches_manifests`·`test_no_ccc_dependency` 포함)

- [ ] **Step 7: 커밋**

```bash
git add -- README.md docs/superpowers/specs/2026-10-30-orca-team-design.md orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json orca-team/tests/test_cli.py orca-team/tests/test_templates.py && git commit -m "$(cat <<'EOF'
chore(orca-team): README·설계 §13 에 그릴링·병렬 실행을 반영하고 0.3.0 으로 올린다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- README.md docs/superpowers/specs/2026-10-30-orca-team-design.md orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json orca-team/tests/test_cli.py orca-team/tests/test_templates.py
```

---

### Task 8: 실환경 확인(수동, 컨트롤러가 직접 한다)

실제 Orca·Codex·Claude 세션으로 작은 버리는 프로젝트를 돌려, 인터뷰 질문 3~5개가 메인을 거쳐 오가는지와 독립 태스크 2개 이상이 동시에 시작되는지 본다. 사용자의 Orca 화면에 새 탭이 열리므로 시작 전에 사용자에게 한 줄로 알린다.

**이 태스크는 코드를 고치지 않는다.** 기대와 다른 점은 증상, 재현 절차, 관련 파일·함수(예: `lib/orch/ask.py:wait_answer`)만 기록해 보고한다. 수정은 사용자가 결정한 뒤 별도 작업으로 한다.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md`(끝에 "12. 실환경 확인 결과" 추가)

**Interfaces:**
- Consumes: Task 1–7 전체(테스트 348개 통과 상태), 디렉터리 소스 마켓플레이스 `setup-with-orca`의 orca-team 0.3.0.
- Produces: 확인 결과 표(관찰 사실만).

- [ ] **Step 1: 사전 확인**

Run:
```bash
PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3
grep -n '"version"' orca-team/.claude-plugin/plugin.json
orca status --json | python3 -c "import json,sys;print(json.load(sys.stdin)['result']['runtime']['state'])"
```
Expected: `Ran 348 tests` / `OK`, `"version": "0.3.0"`, `ready`.

- [ ] **Step 2: 검증용 프로젝트 준비**

Run:
```bash
test ! -e <시험 프로젝트> && mkdir -p <시험 프로젝트>
cd <시험 프로젝트> && git init -q -b main && git commit -q --allow-empty -m "chore: init"
orca repo add --path <시험 프로젝트> --json
```
Expected: 마지막 출력에 `"ok": true`. 폴더가 이미 있어 `test` 가 실패하면 멈추고 사용자에게 지워도 되는지 묻는다(지우지 않는다).

- [ ] **Step 3: 메인 세션 기동과 init**

Run:
```bash
orca agent launch --agent claude --surface terminal --worktree path:<시험 프로젝트> \
  --prompt "/orca-team:init 할 일 목록 웹앱. 정적 HTML 한 장으로 할 일 추가·완료 체크·삭제를 한다. 검증용 버리는 프로젝트다." --json
```
Expected: `"ok": true`. 영수증의 터미널 handle 을 `MAIN_HANDLE` 로 적어 둔다.

`orca terminal read --terminal <MAIN_HANDLE> --json` 으로 진행을 본다. 설치 승인 질문이 뜨면 `orca terminal send --terminal <MAIN_HANDLE> --text "" --enter --json` 으로 첫 선택지를 고른다. "준비 완료" 보고까지 반복한다.

Run:
```bash
cd <시험 프로젝트>
.orchestration/bin/orch --version
grep -c "orch ask-user" .orchestration/roles/planner.md
grep -E "^(max_parallel|user_approval_roles)" .orchestration/config.toml
```
Expected: `orca-team 0.3.0`, 1 이상, `max_parallel = 4` 와 `user_approval_roles = ["designer"]`.

- [ ] **Step 4: 요청 보내기**

Run:
```bash
orca terminal send --terminal <MAIN_HANDLE> --text "할 일 웹앱을 만들어 줘. 화면 디자인, 할 일 저장 모듈(localStorage, src/store/), 화면 구현(src/ui/)까지. 검증용이라 기획 질문은 꼭 필요한 것만 5개 이내로 해 줘." --enter --json
```
Expected(몇 분 안): `cd <시험 프로젝트> && .orchestration/bin/orch status` 의 `tasks` 에 `planner` 태스크 하나가 `working` 으로 있다. 코디네이터가 기획 태스크 외의 태스크를 먼저 만들지 않았다.

- [ ] **Step 5: 인터뷰 왕복 3~5회**

질문이 올 때마다 메인 터미널에 AskUserQuestion 이 뜨면 `orca terminal send --terminal <MAIN_HANDLE> --text "" --enter --json` 으로 첫 선택지(추천)를 고른다. 선택지 없는 질문이면 `--text "<짧은 답>" --enter` 로 답한다. 확정 질문("이 기획으로 확정할까요?")에도 첫 선택지(확정)를 고른다. 각 왕복 뒤 다음을 실행한다.

Run:
```bash
cd <시험 프로젝트>
python3 - <<'EOF'
import json
for line in open(".orchestration/state/outbox.jsonl", encoding="utf-8"):
    e = json.loads(line)
    r = e.get("render") or {}
    print(e["id"], e["type"], e.get("task"), repr(e["body"][:50]), r.get("header"), [o["label"] for o in r.get("options", [])])
EOF
ls .orchestration/state/answers/
```
Expected:
- `ask` 줄이 확정 질문을 빼고 3~5개다. 모두 같은 기획 태스크 id 를 `task` 로 가진다.
- 선택지가 있는 ask 는 첫 label 이 ` (추천)` 으로 끝나고, header 가 12자 이하다.
- `answers/` 에 ask 마다 `out-NNNN.json` 과 `out-NNNN.read` 가 있다(읽음 기록).
- 메인 터미널(`orca terminal read --terminal <MAIN_HANDLE> --json`)의 AskUserQuestion 질문 문구가 `render.question`(`예:` 줄 포함)과 같고, 메인이 문구를 바꾸거나 설명을 덧붙이지 않았다.
- 확정 질문 ask 줄에 `render.context`(기획 핵심, 자동 결정, 태스크 그래프 표)가 있고, 메인이 AskUserQuestion 전에 그 내용을 평문으로 그대로 출력했다(요약·추가 설명 없음).
- 기획 워커 터미널에서 `ask-user` 가 exit 3 으로 거부된 적이 없다. exit 4 가 있었다면 그 뒤 `--wait out-NNNN` 으로 이어 기다렸다. 기획 워커 터미널 handle 은 `python3 -c "import json;print(json.load(open('.orchestration/state/tasks/T-001.json'))['dispatch_id'])"` 로 dispatch id 를 얻고 `orca orchestration worker-show --dispatch <id> --json` 의 `agentTerminalHandle` 로 찾는다.
- 기획 워커가 답을 기다리는 동안 `worker-show` 의 liveness 가 계속 `live` 다(오래 기다려도 Orca 가 워커를 끝난 것으로 보지 않는다).

- [ ] **Step 6: 기획 확정과 plan apply**

기획 태스크가 리뷰를 거쳐 done 이 될 때까지 기다린다(`orch status`).

Run:
```bash
cd <시험 프로젝트>
ls docs/planning/
git log --format=%s | head -15
python3 -c "import json;print(json.load(open('.orchestration/state/plan.json'))['tasks'])"
grep -H -E "^(owns|depends_on|plan_key):" .orchestration/tasks/T-*.md
```
Expected:
- `docs/planning/` 에 `spec.md`, `contracts.md`, `tasks.toml`, `interview.md` 가 있다. interview.md 에 질문 번호·질문·답이 있다.
- 커밋 제목에 `[T-001] review r1: APPROVE`(또는 이후 라운드) 다음 `[plan] apply: N tasks` 가 있다.
- plan.json 이 tasks.toml 의 key 순서대로 T-id 를 매핑한다. 디자인·저장 모듈 태스크는 `depends_on:` 이 비어 있고, 화면 구현 태스크는 둘을 선행으로 가진다.

- [ ] **Step 7: 동시 시작 확인**

Run(plan apply 직후, 코디네이터의 `orch start-ready` 뒤):
```bash
cd <시험 프로젝트>
.orchestration/bin/orch status | python3 -c "import json,sys;[print(t['id'],t['role'],t['status'],t['next']) for t in json.load(sys.stdin)['tasks']]"
python3 - <<'EOF'
import glob, json
for f in sorted(glob.glob(".orchestration/state/tasks/T-*.json")):
    st = json.load(open(f))
    work = [h["at"] for h in st.get("history", []) if h.get("stage") == "work" and h.get("ok")]
    print(st["id"], st["role"], st["status"], work[:1])
EOF
```
Expected:
- 의존이 없는 태스크 2개 이상(디자인, 저장 모듈)이 같은 start-ready 실행에서 시작됐다: work 시작 시각이 몇 초 안이고, 한 시점의 `status` 출력에 둘이 함께 `working` 이다.
- 화면 구현 태스크는 `pending` 이고 `next` 가 `orch start-ready` 다.
- 코디네이터 터미널에 `orch start-ready` 출력(`started` 2개 이상, `waiting` 에 reason `deps`)이 보인다.

- [ ] **Step 8: 디자인 일괄 승인**

디자인 태스크가 Codex APPROVE 뒤 accept 되면 `awaiting_user` 가 되고, 코디네이터가 `orch design-review` 를 한다. 메인에 "디자인 N개를 확인해 주세요" 질문이 뜨면 첫 선택지(전부 승인)를 고른다.

Run:
```bash
cd <시험 프로젝트>
grep '"type": "approval"' .orchestration/state/outbox.jsonl | tail -1
cat .orchestration/state/design-review.json
.orchestration/bin/orch status | python3 -c "import json,sys;[print(t['id'],t['role'],t['status']) for t in json.load(sys.stdin)['tasks']]"
```
Expected:
- approval 줄의 `render.context` 에 디자인 태스크마다 `- T-xxx <제목>: https://claude.ai/…artifact/…` 줄이 있다(링크가 없으면 `(링크 없음: …)` 이고, 이 경우 designer 리포트에 아티팩트 주소가 있는지 기록한다). 메인이 질문 전에 이 목록을 평문으로 보여 줬다.
- 답한 뒤 디자인 태스크가 `done` 이 되고, 화면 구현 태스크가 `working` 으로 시작된다.

- [ ] **Step 9: 입력 방해 0건과 마무리 관찰**

Run: `orca terminal read --terminal <MAIN_HANDLE> --json | grep -c "orchestration message"`
Expected: `0`

끝까지 진행되면(모든 태스크 done) outbox 마지막 줄이 `type: "done"` 인지 본다. 시간이 오래 걸리면 Step 8 까지의 관찰로 마치고 진행 상태를 적는다. 검증용 Orca 탭과 폴더는 사용자 확인 없이 닫거나 지우지 않는다.

- [ ] **Step 10: 결과 기록과 커밋**

`docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md` 끝에 다음 표를 붙이고, 각 칸을 Step 3–9 에서 관찰한 사실로 채운다. 결과 칸은 통과·실패·미확인 중 하나만 쓴다. 실패는 근거 칸에 증상과 관련 파일·함수를 적는다(코드는 고치지 않는다).

```markdown

## 12. 실환경 확인 결과 (확인 날짜, Orca 버전 / codex-cli 버전)

| 항목 | 결과 | 근거 |
|---|---|---|
| 0.3.0 scaffold(planner 지침, config 기본값) | | |
| 인터뷰 왕복 3~5회(ask → 메인 AskUserQuestion → answer) | | |
| 확정 질문 `render.context` 를 메인이 먼저 그대로 표시 | | |
| render 그대로 표시(추천 꼬리표, 예시 줄) | | |
| 답 파일·읽음 기록, exit 3·4 동작 | | |
| 기획 워커 장시간 대기 중 liveness | | |
| docs/planning 산출물과 [plan] apply 커밋 | | |
| 독립 태스크 2개 이상 동시 시작 | | |
| design-review 링크와 일괄 승인 | | |
| 입력 방해 0건 | | |
```

```bash
cd <저장소 루트>
git add -- docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md && git commit -m "$(cat <<'EOF'
docs: 그릴링·병렬 실행 실환경 확인 결과 기록

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)" -- docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md
```

보고: 표의 결과와, 실패·미확인 항목마다 증상·재현 절차·관련 파일·함수를 컨트롤러에게 그대로 전한다.

---

## 자기 검토

**설계 범위 대조**

| 설계 절 | 담당 |
|---|---|
| §1 목적, §2 결정 1–6 | Task 2(직통 질문), Task 3·4(태스크 그래프·동시 상한 4), Task 6(기획이 기술 설계 포함), Task 5(디자인 일괄 승인 1회) |
| §3 전체 흐름 | Task 6 coordinator.md(요청 처리 1–7, 디자인 일괄 승인), Task 8 실환경 |
| §4.1 ask-user(허용 조건, 스키마, render, 2초·540초, exit 4, 한 번에 하나 exit 3, `--wait`, `--pending`), 선택 필드 `context`(결정 2) | Task 2, Task 5(design-review 링크 목록), Task 6(확정 질문·메인 표시) |
| §4.2 answer(타입 분기, exit 3, `tell --reply` 유지) | Task 2 |
| §4.3 메인 표시 규칙 | Task 6 CLAUDE.section.md |
| §4.4 기획 지침(인터뷰 방식, 점검표, 자동 결정, 확정 질문, 산출물 4개, AskUserQuestion 금지) | Task 6 planner.md, Task 1 worker-spec 규칙 5 |
| §5.1 tasks.toml 형식 | Task 3 `parse_plan`, Task 6 planner.md 예시(검증 테스트) |
| §5.2 plan apply(검증 6종, spec·커밋·plan.json·pending, `--append`) | Task 3 |
| §5.3 start-ready(조건 3개, 순서, 결과 형식, 일부 실패, pending 은 ACTIVE 아님, 단건 start 의 선행 검사) | Task 1(start 선행 검사, pending), Task 4 |
| §5.4 max_parallel(기본 4, working 만 셈) | Task 1(설정), Task 4 |
| §6 디자인 일괄 승인(awaiting_user, 잠금 유지, 워커 해제·progress, design-review 조건·질문·링크·design-review.json, fix 허용, 재질문은 수정분만) | Task 5 |
| §7 자율 진행 규칙 | Task 6 coordinator.md |
| §8 바뀌는 것(파일 표) | Task 1–7. hooks 는 바꾸지 않는다(파일 구조 절) |
| §9 오류 처리(죽은 기획 워커 → `--pending`, apply 실패 → fix, 일부 실행 실패, 모호한 답) | Task 2(`--pending`), Task 3(리뷰 사전 검사, 결정 4), Task 4, Task 6 |
| §10 테스트(ask·plan·승인 게이트·템플릿·실환경) | Task 2·3·4·5·6·8 |
| §11 범위 밖 | 다루지 않는다 |

**자리표시 검사:** TBD·TODO·"적절히 처리"·"Task N 과 같다" 없음. 각 코드 단계는 전체 함수나 전체 파일을 싣는다. Task 8 의 표 빈칸은 실환경 관찰로 채울 기록 칸이다.

**이름·형식 일관성:** `deps_of`·`unmet_deps`·`format_unmet`(Task 1) → Task 4 `start_ready`. `parse_text`·`max_task_number`(Task 3) → Task 3·4·5 plan.py import. `load_plan_state`(Task 3) → Task 3 `review.plan_problem`, Task 4 `plan_order`. `comm.post`·`ask.build_render`(Task 2) → Task 5 `design_review`. `load_design_reviews`(Task 5, tasks.py) → Task 5 `next_action`·`design_review`. `Project.answers_dir`·`plan_json`·`design_review_json`(Task 1) → Task 2·3·5. pending 상태 dict(Task 1 테스트·Task 3 `apply`)의 키가 같다. `GRAPH_COMMANDS`·`add_graph_commands`·`run_graph`(Task 3) 를 Task 4·5 가 전체로 다시 쓴다. 종료 코드 4 는 `ask.WAITING_EXIT`(Task 2)뿐이다.

