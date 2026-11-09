# orca-team 고정 에이전트 구현 계획 (0.7.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 코디네이터 LLM 을 없애고, 정해진 절차는 `orch drive` 프로세스가, 판단은 메인이 맡게 한다. 직무 워커는 생산·조사·테스트 고정 에이전트(탭 하나에 작업 pane 과 리뷰 보기 pane)로 바꾼다.

**Architecture:** 새 모듈 `agents.py` 가 고정 에이전트 상태(`state/agents.json`)와 배정·띄우기·리셋·복구를 맡고, `tasks.launch` 가 태스크를 고정 에이전트나 임시 워커로 보낸다. 새 모듈 `drive.py` 는 관제 탭 셸에서 Run 을 갖고 Orca 메일·메인 요청 큐(`queue.py`)·리뷰 자식 프로세스를 돌리며 `coordinator.md` 의 절차를 코드로 실행하고, 판단이 필요한 일은 outbox `decide` 로 메인에 올린다. 메인이 실행한 Orca 명령은 큐를 거쳐 drive 가 실행한다.

**Tech Stack:** Python 3.11 표준 라이브러리(`subprocess`, `fcntl`, `tomllib`, `shlex`, `threading`(테스트)), unittest, 가짜 orca(`tests/fakes/orca`, 순서 응답 추가)·Codex 래퍼.

**설계:** `docs/superpowers/specs/2026-11-09-orca-team-persistent-agents-design.md`(§2.1 에 이 계획을 쓰며 바뀐 점이 있다).

## Global Constraints

- 표준 라이브러리만 쓴다. Python 3.11 이상.
- 테스트 전체: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` 이 경고 없이 통과해야 한다(0.6.0 의 674개에서 지운 것 빼고 더한 것 더해서). 전체 출력에서 `Warning:` 이 없어야 한다. 한 파일만: `cd orca-team/tests && python3 -m unittest test_agents -v`.
- 테스트는 전경에서 긴 시간 제한으로 돌린다. 백그라운드로 돌리지 않는다. 끝난 뒤 `ps -ax | grep "[u]nittest discover"` 가 비어 있어야 한다(다른 프로젝트의 것이면 `lsof -a -p <pid> -d cwd` 로 확인).
- **테스트가 실제 Orca 를 부르면 안 된다.** 같은 프로세스 안에서 orch 함수를 부르는 테스트는 `mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})` 를 쓴다. `orch` 하위 프로세스는 `ProjectCase.env` 가 정한다.
- **테스트가 사용자 홈 캐시에 쓰면 안 된다.** 같은 프로세스에서 리뷰·테스트 로그를 쓰는 함수를 부르면 `XDG_CACHE_HOME` 도 패치한다.
- 버전은 0.7.0 이고 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다(Task 16).
- 공개 저장소다. 커밋 전에 `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'` 결과가 비어야 한다. 서브에이전트는 `.superpowers/sdd/persist/privacy-check.md` 의 전체 검사도 한다.
- Markdown 에서 숫자 범위를 `~` 로 쓰지 않는다(`3–6`).
- 템플릿·문서 문체: 한다체, 코드 표기 뒤 조사는 띄운다("`x` 를").
- 작업 브랜치는 `feat/0.7.0-persistent-agents` 다. main 에 직접 커밋하지 않는다.
- 커밋 메시지 끝에 실제 작성 모델의 `Co-Authored-By` 트레일러를 붙인다.
- 서브에이전트는 `~/.claude/plugins` 와 Orca 를 건드리지 않는다. `.superpowers/` 를 커밋하지 않는다.
- 상태 파일(`.orchestration/state/` 아래)을 쓰는 것은 drive 와 그 자식 프로세스, 그리고 메인 쪽 `orch` 명령의 정해진 파일(outbox, answered.json, 큐 요청 파일)뿐이다.
- 고정 에이전트 이름은 `production`·`research`·`test`, 제목은 "생산"·"조사"·"테스트" 다. 임시 워커는 `agent` 필드 값 `"temp"` 다.
- outbox `decide` 의 kind 는 정확히 `worker_failed`, `worker_question`, `escalation`, `max_rounds`, `repeat_test_failure`, `test_config`, `planning_gaps`, `unknown_regression`, `token_update` 다.
- 메인 → drive 큐 명령 이름은 정확히 `start`, `start-ready`, `fix`, `accept`, `drop`, `reply`, `design-decide`, `scope`, `agent-reset` 이다.

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `orca-team/lib/orch/orca.py` | `worker_info` 전체·간략 응답, `shell_alive` | 1 |
| `orca-team/tests/fakes/orca`, `orca-team/tests/helpers.py` | 순서 응답(`*.seq.json`), `respond_seq`, `scaffold(agents=…)`, 환경 | 1, 4, 9 |
| `orca-team/lib/orch/review.py` | 없는 `worker-retain` 호출 제거 | 1 |
| `orca-team/lib/orch/config.py`, `orca-team/templates/config.toml` | `[agents]`·`[overflow]`·`[drive]`, `[coordinator]` 거부, `codex_effort = "xhigh"` | 2 |
| `orca-team/lib/orch/queue.py` (새) | 메인 → drive 요청 큐 | 3 |
| `orca-team/lib/orch/comm.py` | outbox `decide`·`result`, `post_decide` | 3, 8, 9, 13 |
| `orca-team/lib/orch/agents.py` (새) | 고정 에이전트 상태·배정·띄우기·리셋·복구 | 4, 5, 6, 7 |
| `orca-team/lib/orch/tasks.py` | `launch` 경로, `start` 배정, `fix` 대기열·`--extra-round`, accept·drop 해제 규칙, 인수인계 줄, `start_unknown` 복구, `scope` | 4, 5, 6, 7, 8 |
| `orca-team/lib/orch/plan.py` | `start_ready` 의 에이전트 대기·수정 대기열, `design_decide` | 4, 5, 8 |
| `orca-team/templates/handoff.md` (새), `orca-team/templates/worker-spec.md` | 인수인계 태스크 spec, `{handoff_block}` | 6 |
| `orca-team/lib/orch/ask.py` | `decide` 답, 열린 질문 | 8, 9 |
| `orca-team/lib/orch/util.py`, `orca-team/lib/orch/paths.py` | drive 정체성(`drive_alive`·`is_drive`·`require_drive`), 새 경로 | 3, 9 |
| `orca-team/lib/orch/drive.py` (새) | 관제 루프, 큐 실행, 이벤트 처리, 자동 흐름 | 9, 10, 11 |
| `orca-team/lib/orch/setup.py`, `orca-team/lib/orch/autoup.py` | `up`(관제 탭), `status`, 자동 부활 대상 drive, `upgrade` | 9, 15 |
| `orca-team/lib/orch/cli.py` | 새 명령, 큐 경유, 없어지는 명령 | 5, 6, 8, 9, 12 |
| `orca-team/lib/orch/reviewview.py` (새) | `orch review-view` | 12 |
| `orca-team/hooks/orca_team_hook.py` | 고정 에이전트 판별, 메인 규칙, 코디네이터 규칙 제거 | 13 |
| `orca-team/templates/CLAUDE.section.md`, `templates/roles/*.md`, `templates/coordinator.md`(지움), `skills/up`, `skills/init` | 메인 규칙·문구 | 14 |
| `README.md`, 상태 문서, 버전 세 곳 | 문서와 0.7.0 | 16 |

---

### Task 1: Orca 응답 모양과 가짜 orca 순서 응답

지금 Orca(1.4.240)의 `worker-show --json` 은 간략형(`result.state`·`stage`·`terminal`·`liveness`)만 준다. `orca.worker_info` 가 읽는 `result.worker.*` 는 `--full` 을 붙여야 나온다. 그래서 지금은 바쁨 판정이 늘 "확인 불가"다. 또 `review.py` 가 부르는 `orchestration worker-retain` 은 지금 Orca 에 없는 명령이다(보존은 `worker-keep-open`). drive 테스트에는 같은 명령의 응답을 차례로 바꾸는 가짜 orca 가 필요하다.

**Files:**
- Modify: `orca-team/lib/orch/orca.py` (`worker_info`, 새 `shell_alive`)
- Modify: `orca-team/lib/orch/review.py` (`run_review` 의 `worker-retain` 블록)
- Modify: `orca-team/tests/fakes/orca`
- Modify: `orca-team/tests/helpers.py` (`respond_seq`)
- Test: `orca-team/tests/test_orca.py`, `orca-team/tests/test_review.py`

**Interfaces:**
- Produces: `orca.worker_info(dispatch_id) -> dict | None` (`{"handle", "verdict", "stage", "state"}`, 전체·간략 응답 모두), `orca.shell_alive(handle) -> "alive" | "dead" | "unknown"`, `ProjectCase.respond_seq(key, *bodies, code=0)`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_orca.py` 끝에 더한다(파일 위쪽 import 에 `from unittest import mock` 가 없으면 더한다).

```python
class WorkerShapeTest(ProjectCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_worker_info_reads_full_record_with_full_flag(self):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w", "stage": "settled", "state": "succeeded"},
            "projection": {"liveness": {"verdict": "unverifiable"}}}})
        self.assertEqual(orca.worker_info("ctx_1"),
                         {"handle": "term_w", "verdict": "unverifiable", "stage": "settled", "state": "succeeded"})
        self.assertIn("--full", self.calls_for("orchestration worker-show")[-1])

    def test_worker_info_reads_concise_view(self):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "dispatchId": "ctx_1", "state": "start_unknown", "stage": "turn_start_unobserved",
            "liveness": "live", "terminal": "term_w"}})
        self.assertEqual(orca.worker_info("ctx_1"), {"handle": "term_w", "verdict": "live",
                                                     "stage": "turn_start_unobserved", "state": "start_unknown"})

    def test_shell_alive(self):
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}})
        self.assertEqual(orca.shell_alive("term_d"), "alive")
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": False}}})
        self.assertEqual(orca.shell_alive("term_d"), "dead")
        self.respond("terminal show", {"ok": False, "error": {"code": "terminal_handle_stale"}}, code=1)
        self.assertEqual(orca.shell_alive("term_d"), "dead")
        self.respond("terminal show", {"ok": False, "error": {"code": "runtime_unavailable"}}, code=1)
        self.assertEqual(orca.shell_alive("term_d"), "unknown")
        self.assertEqual(orca.shell_alive(None), "unknown")

    def test_fake_sequence_then_fallback(self):
        self.respond("orchestration check", {"ok": True, "result": {"messages": []}})
        self.respond_seq("orchestration check", {"ok": True, "result": {"messages": [{"id": "m1"}]}},
                         ({"ok": False, "error": {"code": "x"}}, 1))
        self.assertEqual(orca.call(["orchestration", "check"])[1]["result"]["messages"], [{"id": "m1"}])
        self.assertEqual(orca.call(["orchestration", "check"], check=False)[0], 1)
        self.assertEqual(orca.call(["orchestration", "check"])[1]["result"]["messages"], [])
```

`orca-team/tests/test_review.py` 에서:
- `ReviewTest.test_approve_commits_review_and_sets_state` 의 `self.assertTrue(self.calls_for("orchestration worker-retain"))` 를 `self.assertEqual(self.calls_for("orchestration worker-retain"), [])` 로 바꾼다.
- `test_worker_retain_failure_is_best_effort` 메서드를 통째로 지운다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_orca.WorkerShapeTest test_review.ReviewTest -v`
Expected: FAIL — `respond_seq` 없음(AttributeError), `--full` 없음, 간략형 handle `None`, `shell_alive` 없음, worker-retain 호출이 남아 있음.

- [ ] **Step 3: 가짜 orca 와 도우미를 고친다**

`orca-team/tests/fakes/orca` 전체를 바꾼다.

```python
#!/usr/bin/env python3
"""가짜 orca. argv 를 $FAKE_DIR/orca.log 에 JSON 한 줄로 남기고 등록된 응답을 돌려준다.

응답 파일: $FAKE_DIR/<플래그가 아닌 앞 두 토큰을 _ 로 이은 이름>.json = {"code": int, "body": {...}}
예: orchestration worker-start → orchestration_worker-start.json, status → status.json
순서 응답: 같은 이름의 .seq.json(응답 목록)이 있으면 호출마다 앞에서 하나씩 꺼내 쓰고, 다 쓰면 .json 으로 돌아간다.
없으면 {"ok": true, "result": {}} 를 출력하고 exit 0. 응답에 "sleep": <초> 가 있으면 그만큼 잔 뒤 응답한다.
"""
import json
import os
import sys
import time
from pathlib import Path

fake = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with (fake / "orca.log").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\n")
name = "_".join([a for a in args if not a.startswith("--")][:2])
spec = None
seq = fake / (name + ".seq.json")
if seq.exists():
    items = json.loads(seq.read_text(encoding="utf-8"))
    if items:
        spec = items.pop(0)
        seq.write_text(json.dumps(items), encoding="utf-8")
single = fake / (name + ".json")
if spec is None and single.exists():
    spec = json.loads(single.read_text(encoding="utf-8"))
if spec is not None:
    time.sleep(spec.get("sleep", 0))
    if "raw" in spec:
        sys.stdout.write(spec["raw"])
        sys.exit(spec.get("code", 0))
    print(json.dumps(spec["body"], ensure_ascii=False))
    sys.exit(spec.get("code", 0))
print(json.dumps({"ok": True, "result": {}}))
sys.exit(0)
```

`orca-team/tests/helpers.py` 의 `ProjectCase.respond_raw` 바로 뒤에 더한다.

```python
    def respond_seq(self, key: str, *bodies, code: int = 0) -> None:
        """가짜 orca 순서 응답. 호출마다 bodies 를 앞에서 하나씩 쓰고, 다 쓰면 respond 로 등록한 응답(없으면 기본)을 쓴다.
        body 대신 (body, code) 튜플을 주면 그 종료 코드를 쓴다."""
        items = []
        for body in bodies:
            value, exit_code = body if isinstance(body, tuple) else (body, code)
            items.append({"code": exit_code, "body": value})
        path = self.fake_dir / (key.replace(" ", "_") + ".seq.json")
        path.write_text(json.dumps(items), encoding="utf-8")
```

- [ ] **Step 4: `orca.py` 를 고친다**

`worker_info` 전체를 바꾸고, `terminal_alive` 뒤에 `shell_alive` 를 더한다.

```python
def worker_info(dispatch_id: str) -> dict | None:
    """worker-show 요약 {handle, verdict, stage, state}. 조회 실패면 None. 코드 12(orca 없음)는 전파.

    전체 기록(--full: worker.*, projection.liveness.verdict)과 간략형(result.state·stage·terminal·liveness)을 모두 읽는다.
    Orca 1.4.240 은 --full 이 없으면 간략형만 준다."""
    try:
        code, data = call(["orchestration", "worker-show", "--dispatch", dispatch_id, "--full"], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return None
    if code != 0 or not data.get("ok"):
        return None
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    worker = result.get("worker") if isinstance(result.get("worker"), dict) else {}
    projection = result.get("projection") if isinstance(result.get("projection"), dict) else {}
    liveness = projection.get("liveness") if isinstance(projection.get("liveness"), dict) else {}
    concise = lambda key: result.get(key) if isinstance(result.get(key), str) else None  # noqa: E731
    return {"handle": worker.get("agentTerminalHandle") or concise("terminal"),
            "verdict": liveness.get("verdict") or concise("liveness"),
            "stage": worker.get("stage") or concise("stage"),
            "state": worker.get("state") or concise("state")}


def shell_alive(handle: str | None) -> str:
    """셸 터미널(관제 pane, 리뷰 보기 pane) 생존 → "alive" | "dead" | "unknown". 안에서 도는 에이전트 종류는 보지 않는다.
    예외를 내지 않는다."""
    if not handle:
        return "unknown"
    try:
        code, data = call(["terminal", "show", "--terminal", handle], check=False, timeout=TERMINAL_STATE_TIMEOUT)
    except (OrchError, OSError, ValueError):
        return "unknown"
    if code == 0 and data.get("ok"):
        result = data.get("result")
        term = result.get("terminal") if isinstance(result, dict) else None
        if not isinstance(term, dict) or "connected" not in term:
            return "unknown"
        return "alive" if term.get("connected") is True else "dead"
    error = data.get("error")
    if isinstance(error, dict) and error.get("code") == "terminal_handle_stale":
        return "dead"
    return "unknown"
```

- [ ] **Step 5: `review.py` 의 `worker-retain` 블록을 지운다**

`run_review` 에서 다음 다섯 줄을 지운다.

```python
    if st.get("dispatch_id"):
        try:  # 보존 요청은 최선 노력이다. orca 가 없을 때(12)만 멈춘다.
            orca.call(["orchestration", "worker-retain", "--dispatch", st["dispatch_id"]], check=False)
        except OrchError as exc:
            if exc.code == 12:
                raise
```

지운 뒤 `grep -n "orca\." orca-team/lib/orch/review.py` 가 비면 맨 위 `from . import orca` 의 `orca` 도 import 에서 뺀다(다른 이름과 같이 import 돼 있으면 그 이름만 뺀다).

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_orca test_review test_tasks -v 2>&1 | tail -5`
Expected: OK. `test_tasks.show_worker` 는 전체 기록 모양을 그대로 쓰므로 통과한다.

- [ ] **Step 7: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/orca.py orca-team/lib/orch/review.py orca-team/tests/fakes/orca orca-team/tests/helpers.py orca-team/tests/test_orca.py orca-team/tests/test_review.py
git commit -m "fix(orca-team): worker-show 전체 기록을 읽고 없는 worker-retain 호출을 지운다"
```

---

### Task 2: 설정 `[agents]`·`[overflow]`·`[drive]`

**Files:**
- Modify: `orca-team/lib/orch/config.py`
- Modify: `orca-team/templates/config.toml`
- Test: `orca-team/tests/test_config.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Produces: `config.Agent(name: str, title: str, roles: tuple[str, ...], launch: Launch)`, `Config.agents -> dict[str, Agent]`, `Config.agent_for(role) -> Agent | None`, `Config.overflow -> bool`, `Config.auto_up -> bool`(`[drive]`). `Config.load` 는 `[coordinator]` 가 있으면 exit 2 다. `Config.coordinator()` 는 없어진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_config.py`:
- 맨 위 `DATA` 에서 `"coordinator": {"model": "opus", "effort": "xhigh"},` 줄을 지운다.
- `test_auto_up_defaults_true_and_rejects_non_bool` 의 `"coordinator"` 키 세 곳을 `"drive"` 로 바꾼다.
- `test_coordinator_title_rounds` 를 `test_title_rounds` 로 이름을 바꾸고 `self.assertEqual(self.cfg.coordinator(), Launch("opus", "xhigh"))` 줄을 지운다.
- 파일 끝에 더한다(위 import 에 `import tempfile`, `from pathlib import Path` 가 없으면 더한다).

```python
class AgentsConfigTest(unittest.TestCase):
    BASE = {"defaults": {"claude_model": "sonnet", "claude_effort": "xhigh"},
            "roles": {"planner": {"owns": ["docs/planning/**"]}, "developer": {"owns": ["src/**"]},
                      "tester": {"owns": ["tests/**"]}}}

    def cfg(self, agents):
        return Config({**self.BASE, "agents": agents})

    def test_agents_and_lookup(self):
        cfg = self.cfg({"production": {"title": "생산", "roles": ["planner", "developer"], "claude_model": "opus"},
                        "test": {"roles": ["tester"]}})
        prod = cfg.agents["production"]
        self.assertEqual((prod.title, prod.roles, prod.launch), ("생산", ("planner", "developer"), Launch("opus", "xhigh")))
        self.assertEqual((cfg.agents["test"].title, cfg.agents["test"].launch), ("test", Launch("sonnet", "xhigh")))
        self.assertEqual(cfg.agent_for("developer").name, "production")
        self.assertIsNone(Config(self.BASE).agent_for("developer"))

    def test_agents_validation(self):
        for bad in ({"a": {"roles": ["planner"]}, "b": {"roles": ["planner"]}},
                    {"a": {"roles": ["nobody"]}},
                    {"a": {"roles": []}},
                    {"Bad Name": {"roles": ["planner"]}},
                    {"a": "planner"}):
            with self.subTest(bad=bad), self.assertRaises(OrchError):
                self.cfg(bad).agents  # noqa: B018

    def test_overflow_and_drive_auto_up(self):
        self.assertTrue(Config({}).overflow)
        self.assertFalse(Config({"overflow": {"enabled": False}}).overflow)
        self.assertFalse(Config({"drive": {"auto_up": False}}).auto_up)
        for bad in ({"overflow": {"enabled": "no"}}, {"drive": {"auto_up": 1}}):
            with self.subTest(bad=bad), self.assertRaises(OrchError):
                cfg = Config(bad)
                cfg.overflow  # noqa: B018
                cfg.auto_up  # noqa: B018

    def test_load_rejects_legacy_coordinator(self):
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text('[coordinator]\nmodel = "opus"\n', encoding="utf-8")
        with self.assertRaises(OrchError) as ctx:
            Config.load(path)
        self.assertIn("orch upgrade", str(ctx.exception))
        self.assertEqual(ctx.exception.code, 2)
```

`orca-team/tests/test_templates.py` 의 설정 테스트 두 개를 바꾼다.
- `test_config_enables_auto_up` 본문: `self.assertIs(tomllib.loads(text)["drive"]["auto_up"], True)` 와 `self.assertIn("# auto_up:", text)`, 그리고 `self.assertNotIn("[coordinator]", text)`.
- `test_model_table_matches_spec`: 기대 표의 네 번째 값을 모두 `"xhigh"` 로 바꾸고, `cfg["coordinator"]` 줄을 다음으로 바꾼다.

```python
        self.assertEqual({name: (a["title"], a["roles"], a["claude_model"]) for name, a in cfg["agents"].items()}, {
            "production": ("생산", ["planner", "designer", "developer"], "opus"),
            "research": ("조사", ["director"], "opus"),
            "test": ("테스트", ["tester"], "sonnet")})
        self.assertEqual((cfg["overflow"]["enabled"], cfg["defaults"]["codex_effort"]), (True, "xhigh"))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_config test_templates -v 2>&1 | tail -15`
Expected: FAIL — `Config` 에 `agents`·`agent_for`·`overflow` 없음, 템플릿에 `[drive]` 없음.

- [ ] **Step 3: `config.py` 를 고친다**

맨 위 import 에 `import re` 를 더하고, `Launch` 정의 뒤에 더한다.

```python
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class Agent:
    """고정 에이전트([agents.<name>]). roles 의 직무 태스크를 한 세션이 이어 맡는다."""
    name: str
    title: str
    roles: tuple[str, ...]
    launch: Launch
```

`Config.load` 를 바꾼다.

```python
    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            cfg = cls(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
            raise OrchError(f"{path} 읽기 실패: {exc}", 2) from exc
        if "coordinator" in cfg.data:
            raise OrchError(f"{path}: [coordinator] 는 0.7.0 에서 없어졌다(코디네이터 대신 관제 orch drive). "
                            "`orch upgrade` 로 [drive]·[agents] 설정으로 옮긴다", 2)
        return cfg
```

`coordinator()` 메서드와 기존 `auto_up` 프로퍼티를 지우고 다음을 넣는다.

```python
    @property
    def auto_up(self) -> bool:
        """[drive] auto_up. 메인 세션 훅이 죽은 관제(orch drive)를 자동으로 다시 띄울지. 없으면 true."""
        return self._flag("drive", "auto_up")

    @property
    def overflow(self) -> bool:
        """[overflow] enabled. 고정 에이전트가 바쁠 때 준비된 태스크를 임시 워커로 돌릴지. 없으면 true."""
        return self._flag("overflow", "enabled")

    def _flag(self, section: str, key: str) -> bool:
        value = self.data.get(section, {}).get(key, True)
        if not isinstance(value, bool):
            raise OrchError(f"config.toml [{section}].{key} 은 true 또는 false 여야 한다: {value!r}", 2)
        return value

    @property
    def agents(self) -> dict[str, Agent]:
        """[agents.<name>] 고정 에이전트. 한 직무는 한 에이전트에만, 직무는 [roles] 에 있어야 한다."""
        section = self.data.get("agents", {})
        if not isinstance(section, dict):
            raise OrchError("config.toml [agents] 는 표여야 한다", 2)
        defaults, found, owner = self.data.get("defaults", {}), {}, {}
        for name, body in section.items():
            if not AGENT_NAME_RE.match(name) or not isinstance(body, dict):
                raise OrchError(f"config.toml [agents.{name}] 형식이 틀렸다(이름은 소문자·숫자·-, 값은 표)", 2)
            roles = body.get("roles")
            if not isinstance(roles, list) or not roles or not all(isinstance(r, str) and r for r in roles):
                raise OrchError(f"config.toml [agents.{name}].roles 는 직무 이름 목록이다(값: {roles!r})", 2)
            unknown = [r for r in roles if r not in self.roles]
            if unknown:
                raise OrchError(f"config.toml [agents.{name}].roles 에 정의되지 않은 직무가 있다: {', '.join(unknown)}", 2)
            for role in roles:
                if role in owner:
                    raise OrchError(f"config.toml 직무 {role} 가 [agents.{owner[role]}] 와 [agents.{name}] 에 함께 있다", 2)
                owner[role] = name
            values = []
            for key in ("claude_model", "claude_effort"):
                value = body.get(key) or defaults.get(key)
                if not value:
                    raise OrchError(f"[agents.{name}]: {key} 미설정(config.toml)", 2)
                values.append(str(value))
            found[name] = Agent(name, str(body.get("title", name)), tuple(roles), Launch(*values))
        return found

    def agent_for(self, role: str) -> Agent | None:
        return next((agent for agent in self.agents.values() if role in agent.roles), None)
```

모듈 docstring 첫 줄을 다음으로 바꾼다: `"""config.toml 해석: 직무, 소유 경로, 모델 우선순위, 고정 에이전트([agents]), 병렬 상한, 사용자 승인 직무."""`

- [ ] **Step 4: 템플릿 `config.toml` 을 바꾼다**

`orca-team/templates/config.toml` 전체를 다음으로 바꾼다.

```toml
# orca-team 설정. orch 가 디스패치·리뷰 때 읽는다.
# 모델 우선순위: 고정 에이전트는 [agents.<이름>] > [defaults]. 임시 워커는 orch start/fix --model·--effort > 태스크 front matter(claude_model 등) > [roles.<직무>] > [defaults]
# Claude 모델: fable | opus | sonnet | haiku (또는 전체 모델 ID). effort: low | medium | high | xhigh | max
# Codex 모델: gpt-6-astra | gpt-6.1-sol | gpt-6-luna. effort: low | medium | high | xhigh
# owns: 직무가 수정·커밋할 수 있는 경로(glob). 진행 중 태스크끼리 겹치면 orch start 가 거부한다.
# max_parallel: 동시에 working 인 태스크 수 상한(고정 에이전트와 임시 워커를 합친 수).
# user_approval_roles: Codex APPROVE 뒤 orch accept 해도 done 이 아니라 awaiting_user(사용자 일괄 승인 대기)가 되는 직무.
#   [roles] 에 있는 직무만 쓴다. 키가 없으면 [](승인 게이트 없음, 0.2.0 흐름).
# director: 디자인 방향(레퍼런스·톤 시안·디자인 시스템). 이 직무가 있으면 orch plan check 가 designer 태스크에 director 선행을 요구한다.
# auto_up: 메인 세션 훅이 관제(orch drive)가 확실히 죽은 것을 보면 자동으로 다시 띄운다(기본 true). false 면 안내만 한다.
# agents: 고정 에이전트. roles 의 직무 태스크를 한 세션이 이어 맡는다. 한 직무는 한 에이전트에만 둔다.
# overflow: 고정 에이전트가 바쁠 때 준비된 독립 태스크를 임시 워커로 돌린다(기본 true).

max_parallel = 4
user_approval_roles = ["designer"]

[drive]
auto_up = true

[review]
max_rounds = 3

[overflow]
enabled = true

[defaults]
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"

[agents.production]
title = "생산"
roles = ["planner", "designer", "developer"]
claude_model = "opus"
claude_effort = "xhigh"

[agents.research]
title = "조사"
roles = ["director"]
claude_model = "opus"
claude_effort = "xhigh"

[agents.test]
title = "테스트"
roles = ["tester"]
claude_model = "sonnet"
claude_effort = "xhigh"

[roles.planner]
title = "기획"
claude_model = "opus"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"
owns = ["docs/planning/**"]

[roles.director]
title = "디자인 디렉터"
claude_model = "opus"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"
owns = ["docs/design/**", "assets/**"]

[roles.designer]
title = "디자인"
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"
owns = ["docs/design/**", "assets/**"]

[roles.developer]
title = "개발"
claude_model = "opus"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"
owns = ["src/**", "README.md", "docs/user/**", "docs/policy/**"]

[roles.tester]
title = "테스트"
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "xhigh"
owns = ["tests/**", "docs/qa/**"]
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_config test_templates -v 2>&1 | tail -5`
Expected: OK. `test_templates` 에 `Config.coordinator` 를 부르는 다른 테스트가 있으면 그 줄만 지운다.

- [ ] **Step 6: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK. 실패하는 테스트가 `[coordinator]` 를 쓰는 설정을 직접 만드는 경우(예: 자동 부활 테스트)만 그 설정을 `[drive]` 로 바꾼다. 코디네이터 기동 자체를 검사하는 `test_coordinator.py` 는 `Config.coordinator()` 를 부르면 `setup.up` 이 깨지므로, 이 단계에서 `setup.up` 의 `cfg.coordinator()` 를 `Launch("opus", "xhigh")` 로 바꿔 둔다(Task 9 에서 함수째 바뀐다).

```bash
git add orca-team/lib/orch/config.py orca-team/lib/orch/setup.py orca-team/templates/config.toml orca-team/tests/test_config.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 고정 에이전트·임시 워커·관제 설정을 더하고 리뷰 effort 를 xhigh 로 올린다"
```

---

### Task 3: 메인 → drive 요청 큐와 outbox `decide`·`result`

**Files:**
- Create: `orca-team/lib/orch/queue.py`
- Modify: `orca-team/lib/orch/paths.py` (`Project` 새 경로)
- Modify: `orca-team/lib/orch/util.py` (`drive_alive`)
- Modify: `orca-team/lib/orch/comm.py` (`POST_TYPES`, `DECIDE_KINDS`, `post_decide`)
- Test: `orca-team/tests/test_queue.py` (새), `orca-team/tests/test_comm.py`

**Interfaces:**
- Produces: `Project.agents_json`, `Project.drive_dir`, `Project.queue_dir`, `Project.drive_pid`, `Project.drive_heartbeat`, `Project.drive_log`; `util.drive_alive(p) -> bool`; `queue.submit(p, command: str, args: dict, wait: float = 120.0) -> dict`, `queue.pending(p) -> list[Path]`, `queue.complete(p, path: Path, request: dict, result: dict) -> None`; `comm.post_decide(p, kind, task, summary, files=(), commands=(), extra=None) -> dict`.
- 결과 dict 형식: `{"ok": True, "result": <명령 결과>}` 또는 `{"ok": False, "error": <문구>, "code": <종료 코드>}`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_queue.py`:

```python
import json
import os
import threading
import time
import unittest

from helpers import ProjectCase
from orch import comm, queue
from orch.paths import OrchError, Project
from orch.util import now, write_text_atomic


class QueueTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.p = Project(self.root)
        self.p.ensure_state()

    def make_alive(self):
        self.p.drive_dir.mkdir(parents=True, exist_ok=True)
        self.p.drive_pid.write_text(f"{os.getpid()}\n", encoding="utf-8")
        write_text_atomic(self.p.drive_heartbeat, now())

    def serve(self, result):
        """drive 역할: 첫 요청을 기다렸다가 result 로 끝낸다."""
        def run():
            for _ in range(200):
                found = queue.pending(self.p)
                if found:
                    request = json.loads(found[0].read_text(encoding="utf-8"))
                    queue.complete(self.p, found[0], request, result(request))
                    return
                time.sleep(0.02)
        thread = threading.Thread(target=run)
        thread.start()
        return thread

    def test_submit_refuses_when_drive_is_down(self):
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)
        self.assertIn("/orca-team:up", str(ctx.exception))
        self.assertEqual(queue.pending(self.p), [])

    def test_stale_heartbeat_counts_as_down(self):
        self.make_alive()
        write_text_atomic(self.p.drive_heartbeat, "2000-01-01T00:00:00+00:00")
        with self.assertRaises(OrchError):
            queue.submit(self.p, "start", {"id": "T-001"}, wait=0.1)

    def test_submit_returns_drive_result(self):
        self.make_alive()
        thread = self.serve(lambda req: {"ok": True, "result": {"echo": req["args"], "command": req["command"]}})
        out = queue.submit(self.p, "start", {"id": "T-001"}, wait=5)
        thread.join()
        self.assertEqual(out, {"echo": {"id": "T-001"}, "command": "start"})
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_failed_result_raises_with_code(self):
        self.make_alive()
        thread = self.serve(lambda req: {"ok": False, "error": "거부한다", "code": 2})
        with self.assertRaises(OrchError) as ctx:
            queue.submit(self.p, "fix", {"id": "T-001"}, wait=5)
        thread.join()
        self.assertEqual((str(ctx.exception), ctx.exception.code), ("거부한다", 2))

    def test_timeout_leaves_marker_and_result_goes_to_outbox(self):
        self.make_alive()
        out = queue.submit(self.p, "fix", {"id": "T-001"}, wait=0.1)
        self.assertEqual(out["status"], "queued")
        [path] = queue.pending(self.p)
        request = json.loads(path.read_text(encoding="utf-8"))
        queue.complete(self.p, path, request, {"ok": True, "result": {"id": "T-001"}})
        last = comm.read_outbox(self.p)[-1]
        self.assertEqual((last["type"], last["request"], last["result"]["ok"]), ("result", request["id"], True))
        self.assertEqual(sorted(self.p.queue_dir.iterdir()), [])

    def test_pending_keeps_submit_order(self):
        self.make_alive()
        queue.submit(self.p, "start", {"id": "T-001"}, wait=0)
        queue.submit(self.p, "start", {"id": "T-002"}, wait=0)
        ids = [json.loads(path.read_text(encoding="utf-8"))["args"]["id"] for path in queue.pending(self.p)]
        self.assertEqual(ids, ["T-001", "T-002"])

    def test_post_decide(self):
        entry = comm.post_decide(self.p, "worker_failed", "T-001", "빌드 실패", files=["docs/tasks/T-001/report.md"],
                                 commands=['orch fix T-001 --note "<지시>"'])
        self.assertEqual((entry["type"], entry["kind"], entry["task"], entry["files"]),
                         ("decide", "worker_failed", "T-001", ["docs/tasks/T-001/report.md"]))
        self.assertIn("빌드 실패", entry["body"])
        with self.assertRaises(OrchError):
            comm.post_decide(self.p, "nope", "T-001", "x")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_queue -v 2>&1 | tail -5`
Expected: FAIL — `No module named 'orch.queue'`.

- [ ] **Step 3: 경로와 `drive_alive` 를 더한다**

`paths.py` `Project.__init__` 끝(`answered_json` 줄 뒤)에 더한다.

```python
        self.agents_json = self.state / "agents.json"  # 고정 에이전트 상태(drive 만 쓴다)
        self.drive_dir = self.state / "drive"
        self.queue_dir = self.drive_dir / "queue"  # 메인 → drive 요청(queue.py)
        self.drive_pid = self.drive_dir / "drive.pid"
        self.drive_heartbeat = self.drive_dir / "heartbeat"  # drive 가 바퀴마다 시각을 쓴다
        self.drive_log = self.drive_dir / "drive.log"
```

`util.py` 맨 위 상수 아래에 `DRIVE_STALE_SECONDS = 120` 을 더하고, `pid_alive` 뒤에 더한다.

```python
def drive_alive(p: Project) -> bool:
    """관제(orch drive)가 살아 있는가: pid 가 살아 있고 heartbeat 가 DRIVE_STALE_SECONDS 안이다."""
    if not pid_alive(p.drive_pid):
        return False
    try:
        stamp = p.drive_heartbeat.read_text(encoding="utf-8").strip()
        age = (datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()
    except (OSError, ValueError):
        return False
    return 0 <= age < DRIVE_STALE_SECONDS
```

- [ ] **Step 4: `comm.py` 에 `decide`·`result` 를 더한다**

`POST_TYPES` 줄을 바꾸고 `DECIDE_KINDS` 와 `post_decide` 를 `notify` 뒤에 더한다.

```python
POST_TYPES = (*TYPES, "ask", "approval", "decide", "result")  # outbox 에 쓰는 모든 타입
DECIDE_KINDS = ("worker_failed", "worker_question", "escalation", "max_rounds", "repeat_test_failure",
                "test_config", "planning_gaps", "unknown_regression", "token_update")
```

```python
def post_decide(p: Project, kind: str, task: str | None, summary: str, files=(), commands=(),
                extra: dict | None = None) -> dict:
    """메인에 판단을 맡긴다(drive). 메인은 files 만 읽고 commands 중 하나로 처리한 뒤 orch answer 로 닫는다."""
    if kind not in DECIDE_KINDS:
        raise OrchError(f"decide 종류가 아니다: {kind}", 2)
    summary = summary.strip() or "(요약 없음)"
    return post(p, "decide", f"[{kind}] {task or '-'}: {summary}",
                {"kind": kind, "task": task, "summary": summary, "files": list(files), "commands": list(commands),
                 **(extra or {})})
```

- [ ] **Step 5: `queue.py` 를 만든다**

```python
"""메인 → drive 요청 큐(.orchestration/state/drive/queue/).

메인이 Orca 를 부르는 명령을 실행하면 요청 파일(<번호>.json)을 쓰고 결과 파일(<번호>.result.json)을 기다린다.
drive 만 요청을 실행한다(메인 터미널에서 worker-start 를 부르면 Orca 가 메인에 Run 을 묶는다). 기다리다 그만둔 요청은
<번호>.abandoned 표시를 남기고, drive 는 그 결과를 outbox `result` 로 보낸다."""
from __future__ import annotations

import time
from pathlib import Path

from . import comm
from .paths import OrchError, Project
from .util import drive_alive, now, read_json, write_json

WAIT_SECONDS = 120.0
POLL_SECONDS = 0.2
NAME_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9].json"


def _paths(p: Project, number: str) -> tuple[Path, Path, Path]:
    return (p.queue_dir / f"{number}.json", p.queue_dir / f"{number}.result.json", p.queue_dir / f"{number}.abandoned")


def _next_number(p: Project) -> str:
    numbers = [int(f.name[:6]) for f in p.queue_dir.iterdir() if f.name[:6].isdigit()]
    return f"{max(numbers, default=0) + 1:06d}"


def submit(p: Project, command: str, args: dict, wait: float = WAIT_SECONDS) -> dict:
    """요청을 넣고 결과를 기다린다. drive 가 꺼져 있으면 exit 1. 실패 결과는 그 코드로 OrchError.
    wait 초 안에 끝나지 않으면 {"status": "queued"} 를 돌려주고 결과는 outbox `result` 로 온다."""
    if not drive_alive(p):
        raise OrchError("관제(drive)가 꺼져 있다. 메인에서 /orca-team:up 으로 살린 뒤 다시 한다", 1)
    p.queue_dir.mkdir(parents=True, exist_ok=True)
    with comm._file_lock(p.queue_dir / ".lock"):
        number = _next_number(p)
        request_path, result_path, abandoned = _paths(p, number)
        write_json(request_path, {"id": f"q-{number}", "command": command, "args": args, "at": now()})
    deadline = time.monotonic() + wait
    while True:
        if result_path.exists():
            return _take(result_path)
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    abandoned.touch()
    if result_path.exists():  # 표시를 남기는 사이에 끝났다
        abandoned.unlink(missing_ok=True)
        return _take(result_path)
    return {"status": "queued", "request": f"q-{number}",
            "message": "관제가 처리 중이다. 결과는 orch watch 의 result 메시지로 온다"}


def _take(result_path: Path) -> dict:
    data = read_json(result_path, {})
    result_path.unlink(missing_ok=True)
    if not data.get("ok"):
        raise OrchError(str(data.get("error") or "관제가 요청을 처리하지 못했다"), int(data.get("code") or 1))
    return data.get("result") if isinstance(data.get("result"), dict) else {"result": data.get("result")}


def pending(p: Project) -> list[Path]:
    """drive 가 실행할 요청 파일. 넣은 순서."""
    if not p.queue_dir.is_dir():
        return []
    return sorted(p.queue_dir.glob(NAME_GLOB))


def complete(p: Project, path: Path, request: dict, result: dict) -> None:
    """요청을 끝낸다. 요청한 쪽이 기다리면 결과 파일을, 그만뒀으면 outbox `result` 를 쓴다."""
    number = path.name[:6]
    _, result_path, abandoned = _paths(p, number)
    if abandoned.exists():
        _post_result(p, request, result)
        abandoned.unlink(missing_ok=True)
    else:
        write_json(result_path, result)
        if abandoned.exists() and result_path.exists():  # 결과를 쓰는 사이에 그만뒀다
            result_path.unlink(missing_ok=True)
            _post_result(p, request, result)
            abandoned.unlink(missing_ok=True)
    path.unlink(missing_ok=True)


def _post_result(p: Project, request: dict, result: dict) -> None:
    status = "완료" if result.get("ok") else f"실패: {result.get('error')}"
    comm.post(p, "result", f"{request.get('command')} {request.get('args', {}).get('id', '')} — {status}".strip(),
              {"request": request.get("id"), "command": request.get("command"), "result": result})
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_queue test_comm -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 7: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/queue.py orca-team/lib/orch/paths.py orca-team/lib/orch/util.py orca-team/lib/orch/comm.py orca-team/tests/test_queue.py
git commit -m "feat(orca-team): 메인에서 관제로 가는 요청 큐와 outbox decide·result 를 더한다"
```

---

### Task 4: 고정 에이전트 배정과 띄우기 (`agents.py`)

**Files:**
- Create: `orca-team/lib/orch/agents.py`
- Modify: `orca-team/lib/orch/tasks.py` (`launch`, `start`)
- Modify: `orca-team/lib/orch/plan.py` (`start_ready`)
- Modify: `orca-team/tests/helpers.py` (`scaffold(agents=False)`)
- Test: `orca-team/tests/test_agents.py` (새)

**Interfaces:**
- Consumes: `Config.agents`, `Config.agent_for`, `Config.overflow`(Task 2), `Project.agents_json`(Task 3).
- Produces: `agents.TEMP = "temp"`, `agents.WAIT = "wait"`, `agents.load_all(p)`, `agents.entry(p, name) -> dict`, `agents.update(p, name, fn) -> dict`, `agents.working(p, name) -> bool`, `agents.place(p, cfg, role) -> str`, `agents.start(p, agent, spec, title) -> (code, receipt, fresh)`, `agents.record_start(p, agent, receipt, fresh, task_id) -> dict`, `agents.release_task(p, name, task_id)`, `agents.summary(p, cfg) -> list[dict]`, `agents._best_effort(args) -> dict | None`. `tasks.launch(..., agent: str | None = None)`. 태스크 상태와 이력 entry 의 `agent` 필드(에이전트 이름 또는 `"temp"`), 이력 entry 의 `note`.
- `agents.json` 의 entry 필드: `handle`, `review_handle`, `tab_id`, `model`, `effort`, `task`, `queue`, `since_reset`, `reset_pending`, `resetting`, `launched_at`, `last_reset_at`, `layout_error`.

- [ ] **Step 1: 테스트 도우미를 고친다**

`helpers.py` 의 `scaffold` 를 바꾼다. 0.6.0 까지의 테스트는 모든 태스크가 임시 워커로 도는 흐름을 검사하므로 기본은 `[agents.*]` 를 지운다.

```python
    def scaffold(self, agents: bool = False) -> None:
        """orch scaffold 후 Run 이 있는 상태로 둔다. agents=False 면 config.toml 의 [agents.*] 를 지워
        모든 태스크가 임시 워커로 돈다(0.6.0 까지의 흐름)."""
        desc = self.tmp / "desc.txt"
        desc.write_text("테스트 프로젝트. 할 일 웹앱.", encoding="utf-8")
        self.orch("scaffold", "--description-file", str(desc))
        (self.root / ".orchestration/state/run.json").write_text(
            json.dumps({"run_id": "run_x", "coordinator_handle": "term_coord"}), encoding="utf-8")
        if not agents:
            path = self.root / ".orchestration/config.toml"
            text = path.read_text(encoding="utf-8")
            path.write_text(re.sub(r"\n\[agents\.[a-z0-9-]+\]\n(?:[^\[\n].*\n|\n)*", "\n", text), encoding="utf-8")
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`orca-team/tests/test_agents.py`:

```python
import json
import os
import unittest
from unittest import mock

from helpers import FAKES, ProjectCase
from test_tasks import TaskCase
from orch import agents
from orch.config import Config
from orch.paths import Project

LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
FRESH = {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1", "terminal": "term_a1"}}
SPLIT = {"ok": True, "result": {"split": {"handle": "term_r1", "tabId": "tab_1"}}}
NEXT = {"ok": True, "result": {"taskId": "task_2", "dispatchId": "ctx_2", "terminal": "term_a1"}}


class AgentCase(TaskCase):
    def setUp(self):
        ProjectCase.setUp(self)
        self.scaffold(agents=True)
        self.respond("orchestration worker-start", FRESH)
        self.respond("terminal split", SPLIT)
        self.respond("terminal show", LIVE)
        self.respond("orchestration task-create", {"ok": True, "result": {"taskId": "task_2"}})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": self.env["ORCA_TERMINAL_HANDLE"],
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cfg(self):
        return Config.load(self.root / ".orchestration/config.toml")

    def agents_state(self):
        return json.loads((self.root / ".orchestration/state/agents.json").read_text(encoding="utf-8"))

    def commit_spec(self, task_id, role="developer", extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"[{task_id}] spec")

    def no_overflow(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"), encoding="utf-8")

    def pending(self, task_id, role, owns):
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.write_text(json.dumps({"id": task_id, "role": role, "title": "x", "status": "pending", "stage": "work",
                                    "round": 1, "base": None, "owns": owns, "depends_on": [], "task_id": None,
                                    "dispatch_id": None, "history": [], "verdicts": {}}), encoding="utf-8")


class PlaceTest(AgentCase):
    def test_place_rules(self):
        cfg = self.cfg()
        self.assertEqual(agents.place(self.p, cfg, "developer"), "production")
        agents.update(self.p, "production", lambda e: e.update(task="T-001"))
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.TEMP)
        cfg.data["overflow"] = {"enabled": False}
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.WAIT)
        agents.update(self.p, "production", lambda e: e.update(task=None, reset_pending=True))
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.WAIT)
        cfg.data["agents"].pop("production")
        self.assertEqual(agents.place(self.p, cfg, "planner"), agents.TEMP)


class AgentStartTest(AgentCase):
    def test_first_start_launches_kept_open_agent_with_review_pane(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        call = self.last_start()
        self.assertIn("--keep-open", call)
        self.assertNotIn("--terminal", call)
        self.assertEqual((self.arg(call, "--model"), self.arg(call, "--effort")), ("opus", "xhigh"))
        split = self.calls_for("terminal split")[-1]
        self.assertEqual(self.arg(split, "--direction"), "vertical")
        self.assertTrue(self.arg(split, "--command").endswith("review-view production"))
        self.assertEqual(self.arg(self.calls_for("terminal rename")[-1], "--title"), "orca-team 생산")
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["review_handle"], state["task"]), ("term_a1", "term_r1", "T-001"))
        self.assertEqual(self.state("T-001")["agent"], "production")
        self.assertEqual(self.state("T-001")["history"][-1]["agent"], "production")

    def test_next_task_goes_to_same_terminal(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond("orchestration worker-start", NEXT)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        call = self.last_start()
        self.assertEqual((self.arg(call, "--task"), self.arg(call, "--terminal")), ("task_2", "term_a1"))
        self.assertNotIn("--model", call)
        self.assertEqual(len(self.calls_for("terminal split")), 1)
        self.assertEqual(self.calls_for("orchestration task-create")[-1][self.calls_for("orchestration task-create")[-1].index("--task-title") + 1],
                         "T-002 work r1 [planner] 로그인")
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)

    def test_dead_terminal_relaunches(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")
        self.respond("terminal show", STALE, code=1)
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        self.assertIn("--keep-open", self.last_start())
        self.assertEqual(len(self.calls_for("terminal split")), 2)

    def test_busy_agent_overflows_to_temp_worker(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_9", "dispatchId": "ctx_9"}})
        self.commit_spec("T-002", role="planner")
        self.orch("start", "T-002")
        call = self.last_start()
        self.assertNotIn("--keep-open", call)
        self.assertEqual(self.arg(call, "--model"), "opus")  # planner 직무 모델
        self.assertEqual(self.state("T-002")["agent"], "temp")
        self.assertEqual(self.agents_state()["production"]["task"], "T-001")

    def test_busy_agent_without_overflow_refuses_and_start_ready_waits(self):
        self.no_overflow()
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.commit_spec("T-002", role="planner")
        self.assertEqual(self.orch("start", "T-002", check=False).returncode, 3)
        self.pending("T-002", "planner", ["docs/planning/**"])
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["waiting"], [{"id": "T-002", "reason": "agent", "detail": "생산"}])

    def test_front_matter_model_is_ignored_for_agent(self):
        self.commit_spec("T-001", extra="claude_model: haiku\n")
        self.orch("start", "T-001")
        self.assertEqual(self.arg(self.last_start(), "--model"), "opus")
        self.assertTrue(self.state("T-001")["history"][-1]["model_ignored"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents -v 2>&1 | tail -5`
Expected: FAIL — `cannot import name 'agents'`.

- [ ] **Step 4: `agents.py` 를 만든다**

```python
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
```

- [ ] **Step 5: `tasks.launch` 와 `start` 를 고친다**

`tasks.py` 맨 위 import 를 `from . import agents, orca, testgate` 로 바꾼다. `launch` 전체를 바꾼다.

```python
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
```

`start` 의 끝 부분(`owns = owns_of(doc, cfg)` 부터)을 바꾼다.

```python
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
```

- [ ] **Step 6: `plan.start_ready` 에 에이전트 대기를 더한다**

`plan.py` 맨 위 import 에 `from . import agents` 를 더한다(이미 `from . import ...` 줄이 있으면 거기에 `agents` 를 넣는다). `start_ready` 루프에서 `check_lock` 블록 다음, `if running >= limit:` 앞에 넣는다.

```python
        if agents.place(p, cfg, doc.role) == agents.WAIT:
            waiting.append({"id": task_id, "reason": "agent", "detail": cfg.agent_for(doc.role).title})
            continue
```

`start(p, cfg, task_id)` 를 감싼 `except OrchError as exc:` 의 `if exc.code == 12: raise` 다음에 넣는다(배정과 실행 사이에 에이전트가 바빠진 경우).

```python
            if exc.code == 3:
                waiting.append({"id": task_id, "reason": "agent", "detail": str(exc)})
                continue
```

docstring 의 waiting reason 설명에 `agent(고정 에이전트 대기)` 를 더한다.

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents test_tasks test_plan test_review -v 2>&1 | tail -5`
Expected: OK. 0.6.0 테스트는 `scaffold()` 가 `[agents]` 를 지우므로 임시 워커 흐름 그대로다.

- [ ] **Step 8: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/agents.py orca-team/lib/orch/tasks.py orca-team/lib/orch/plan.py orca-team/tests/helpers.py orca-team/tests/test_agents.py
git commit -m "feat(orca-team): 태스크를 고정 에이전트에 배정하고 같은 세션에 이어 넣는다"
```

---

### Task 5: 수정 라운드·accept·drop 과 `--extra-round`

**Files:**
- Modify: `orca-team/lib/orch/agents.py` (`enqueue`, `dequeue`, `detach`)
- Modify: `orca-team/lib/orch/tasks.py` (`fix`, 새 `drain_fix_queues`, `accept`, `drop`)
- Modify: `orca-team/lib/orch/plan.py` (`start_ready`)
- Modify: `orca-team/lib/orch/cli.py` (`fix --extra-round`)
- Test: `orca-team/tests/test_agents.py`, `orca-team/tests/test_tasks.py`, `orca-team/tests/test_plan.py`

**Interfaces:**
- Consumes: `agents.working`, `agents.entry`, `agents.update`, `tasks.launch(..., agent=)`(Task 4).
- Produces: `agents.enqueue(p, name, task_id)`, `agents.dequeue(p, name, task_id)`, `agents.detach(p, name, task_id)`; `tasks.fix(p, cfg, task_id, note=None, retry=False, overrides=None, force=False, supplement=None, extra_round=False, from_queue=False) -> dict`(대기열에 넣으면 `{"id", "status": "queued", "agent"}`); `tasks.drain_fix_queues(p, cfg) -> list[dict]`; `start_ready` 결과에 `"fixes"`. 태스크 상태의 `queued_fix` (`{"note", "supplement", "extra_round", "force", "retry", "at"}`). `accept`·`drop` 결과에서 `notified` 가 없어진다(outbox 에 쓰지 않는다). 고정 에이전트 태스크의 `released` 는 `None`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_agents.py` 에 더한다.

```python
class AgentFixTest(AgentCase):
    def started(self, task_id="T-001", role="developer"):
        self.commit_spec(task_id, role=role)
        self.orch("start", task_id)

    def test_fix_goes_to_same_agent_without_release(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="changes")
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--note", "고친다")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        call = self.last_start()
        self.assertEqual(self.arg(call, "--terminal"), "term_a1")
        self.assertEqual((self.state("T-001")["round"], self.state("T-001")["history"][-1]["note"]), (2, "고친다"))

    def test_fix_queues_while_agent_busy_then_drains(self):
        self.started()                      # 생산 에이전트가 T-001 을 하는 중
        self.commit_spec("T-002", role="planner")
        head = self.git("rev-parse", "HEAD").strip()
        (self.root / ".orchestration/state/tasks/T-002.json").write_text(json.dumps({
            "id": "T-002", "role": "planner", "title": "로그인", "status": "changes", "stage": "work", "round": 1,
            "base": head, "owns": ["docs/planning/**"], "depends_on": [], "agent": "production", "task_id": "task_x",
            "dispatch_id": "ctx_x", "history": [], "verdicts": {"1": "REQUEST_CHANGES"}}), encoding="utf-8")
        out = json.loads(self.orch("fix", "T-002", "--note", "보강").stdout)
        self.assertEqual((out["status"], out["agent"]), ("queued", "production"))
        self.assertEqual(self.agents_state()["production"]["queue"], ["T-002"])
        self.assertEqual(self.state("T-002")["queued_fix"]["note"], "보강")
        agents.release_task(self.p, "production", "T-001")
        self.respond("orchestration worker-start", NEXT)
        out = json.loads(self.orch("start-ready").stdout)
        self.assertEqual(out["fixes"][0]["id"], "T-002")
        self.assertEqual(self.arg(self.last_start(), "--terminal"), "term_a1")
        self.assertNotIn("queued_fix", self.state("T-002"))
        self.assertEqual(self.agents_state()["production"]["queue"], [])

    def test_extra_round(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="changes", round=3)
        proc = self.orch("fix", "T-001", check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--extra-round", proc.stderr)
        self.respond("orchestration worker-start", NEXT)
        self.orch("fix", "T-001", "--extra-round")
        last = self.state("T-001")["history"][-1]
        self.assertEqual((self.state("T-001")["round"], last["extra_round"]), (4, True))

    def test_accept_and_drop_do_not_release_agent(self):
        self.started()
        agents.release_task(self.p, "production", "T-001")
        self.set_state("T-001", status="approved")
        out = json.loads(self.orch("accept", "T-001").stdout)
        self.assertEqual((out["status"], out["released"]), ("done", None))
        self.assertNotIn("notified", out)
        self.commit_spec("T-002", role="planner")
        self.respond("orchestration worker-start", NEXT)
        self.orch("start", "T-002")
        agents.release_task(self.p, "production", "T-002")
        self.set_state("T-002", status="changes")
        agents.enqueue(self.p, "production", "T-002")
        self.orch("drop", "T-002", "--reason", "필요 없어졌다")
        self.assertEqual(self.calls_for("orchestration worker-release"), [])
        self.assertEqual(self.agents_state()["production"]["queue"], [])
```

`test_tasks.py` 를 고친다(accept·drop 이 더는 outbox 에 쓰지 않는다).
- `test_accept_releases_notifies_and_unlocks` 를 `test_accept_releases_and_unlocks` 로 이름을 바꾸고 outbox 를 읽는 세 줄(`outbox = ...`, `assertIn("T-001", outbox)`, `assertIn("APPROVE", outbox)`)을 지운다.
- `test_force_drops_live_worker_and_records_it` 의 `self.assertIn("강제", self.outbox()[-1]["body"])` 를 `self.assertTrue(self.state("T-001")["history"][-1]["forced"])` 로 바꾼다.
- `test_drop_notifies_main` 을 지운다.

`test_plan.py` 에서 `outbox(self.root)[-1]["body"]` 로 "사용자 승인 대기"·"사용자 승인" 문구를 보던 두 줄을 각각 `self.assertEqual(self.state_of("T-xxx")["status"], "awaiting_user")`·`"done"` 처럼 상태 검사로 바꾼다(그 테스트가 쓰는 태스크 ID 와 상태 읽기 도우미 이름을 그대로 쓴다).

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents.AgentFixTest -v 2>&1 | tail -8`
Expected: FAIL — fix 가 worker-release 를 부르고, `--extra-round` 가 없고, `agents.enqueue` 가 없다.

- [ ] **Step 3: `agents.py` 에 대기열 함수를 더한다**

`release_task` 뒤에 더한다.

```python
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
```

- [ ] **Step 4: `tasks.fix`·`accept`·`drop` 을 고치고 `drain_fix_queues` 를 더한다**

`fix` 전체를 바꾼다.

```python
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
                raise OrchError(f"{task_id}: 워커가 아직 일하고 있을 수 있다({desc}). fix 하면 같은 경로를 동시에 고친다. "
                                "worker_done 을 기다리거나 워커가 끝난 뒤 한다. 그래도 하려면 --force", 2)
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
```

`accept` 전체를 바꾼다.

```python
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
```

`drop` 에서 해제 블록과 끝부분을 바꾼다. `released, release_error = None, None` 부터 `save_state(p, st)` 까지를 다음으로 바꾼다.

```python
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
```

그리고 `finally` 뒤의 `kind = ...`, `msg = notify(...)` 두 줄을 지우고 결과를 다음으로 만든다.

```python
    result = {"id": task_id, "status": "dropped", "released": released, "forced": force}
    if release_error is not None:
        result["release_error"] = release_error
    return result
```

`tasks.py` 에서 `notify` 를 더 쓰지 않으면 맨 위 `from .comm import notify` 를 지운다.

- [ ] **Step 5: `start_ready` 가 대기열을 먼저 비우게 한다**

`plan.start_ready` 에서 `limit = cfg.max_parallel` 바로 앞에 `fixes = drain_fix_queues(p, cfg)` 를 넣고(`from .tasks import ...` 에 `drain_fix_queues` 를 더한다), 반환 dict 에 `"fixes": fixes` 를 더한다. `running` 은 대기열을 비운 뒤에 센다(지금 위치 그대로면 된다).

- [ ] **Step 6: `orch fix --extra-round` 를 더한다**

`cli.py` `build_parser` 의 `if name == "fix":` 블록에 더한다.

```python
            s.add_argument("--extra-round", action="store_true",
                           help="최대 라운드를 넘긴 태스크를 한 번 더 고친다(사용자가 정했을 때만)")
```

`run` 의 fix 호출을 바꾼다.

```python
            emit(tasks.fix(p, cfg, args.id, args.note, args.retry, overrides(args), args.force, args.supplement,
                           args.extra_round))
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents test_tasks test_plan -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 8: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/agents.py orca-team/lib/orch/tasks.py orca-team/lib/orch/plan.py orca-team/lib/orch/cli.py orca-team/tests/test_agents.py orca-team/tests/test_tasks.py orca-team/tests/test_plan.py
git commit -m "feat(orca-team): 고정 에이전트 태스크의 수정 라운드를 같은 에이전트로 보내고 --extra-round 를 더한다"
```

---

### Task 6: 세션 리셋과 인수인계

**Files:**
- Create: `orca-team/templates/handoff.md`
- Modify: `orca-team/templates/worker-spec.md` (`{handoff_block}`)
- Modify: `orca-team/lib/orch/agents.py` (리셋 함수)
- Modify: `orca-team/lib/orch/tasks.py` (`compose_spec`)
- Modify: `orca-team/lib/orch/cli.py` (`orch agent reset`)
- Test: `orca-team/tests/test_agents.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: `agents.start`, `agents.update`, `agents.entry`, `agents._best_effort`(Task 4).
- Produces: `agents.handoff_rel(name) -> str`, `agents.handoff_block(p, cfg, role) -> str`, `agents.mark_reset_pending(p, cfg) -> list[str]`, `agents.request_reset(p, cfg, name) -> dict`, `agents.begin_reset(p, cfg, name) -> dict | None`(`{"name", "status": "handoff"|"skipped"|"failed", ...}`), `agents.reset_owner(p, dispatch_id) -> str | None`, `agents.finish_reset(p, name, ok, sleep=time.sleep) -> dict`(`status`: `"reset"`|`"handoff_failed"`), `agents.RESET_SETTLE_SECONDS = 5.0`. entry 의 `resetting = {"dispatch_id", "at"}`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_agents.py` 에 더한다(import 에 `from orch import agents, tasks` 로 `tasks` 를 더한다).

```python
class ResetTest(AgentCase):
    def started(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        agents.release_task(self.p, "production", "T-001")

    def test_spec_reads_handoff_file_when_present(self):
        (self.root / "docs/handoff").mkdir(parents=True)
        (self.root / "docs/handoff/production.md").write_text("# 인수인계\n", encoding="utf-8")
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.assertIn("먼저 `docs/handoff/production.md` 를 읽는다", self.arg(self.last_start(), "--spec"))

    def test_spec_has_no_handoff_line_without_file(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        spec = self.arg(self.last_start(), "--spec")
        self.assertNotIn("docs/handoff", spec)
        self.assertNotIn("\n\n\n", spec)

    def test_mark_reset_pending_only_used_agents(self):
        self.started()
        self.assertEqual(agents.mark_reset_pending(self.p, self.cfg()), ["production"])
        self.assertTrue(self.agents_state()["production"]["reset_pending"])
        self.assertNotIn("research", self.agents_state())

    def test_begin_and_finish_reset(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        out = agents.begin_reset(self.p, self.cfg(), "production")
        self.assertEqual((out["status"], out["dispatch_id"]), ("handoff", "ctx_h"))
        created = self.calls_for("orchestration task-create")[-1]
        self.assertIn("docs/handoff/production.md", self.arg(created, "--spec"))
        self.assertEqual(self.arg(created, "--task-title"), "[handoff] 생산 인수인계")
        self.assertEqual(self.arg(self.last_start(), "--terminal"), "term_a1")
        self.assertEqual(agents.reset_owner(self.p, "ctx_h"), "production")
        self.assertEqual(agents.place(self.p, self.cfg(), "developer"), agents.WAIT)
        out = agents.finish_reset(self.p, "production", True, sleep=lambda _: None)
        self.assertEqual(out["status"], "reset")
        sent = self.calls_for("terminal send")[-1]
        self.assertEqual((self.arg(sent, "--terminal"), self.arg(sent, "--text")), ("term_a1", "/clear"))
        self.assertEqual(self.arg(self.calls_for("terminal wait")[-1], "--for"), "tui-idle")
        state = self.agents_state()["production"]
        self.assertEqual((state["since_reset"], state["reset_pending"], state["resetting"]), (0, False, None))
        self.assertEqual(agents.place(self.p, self.cfg(), "developer"), "production")

    def test_failed_handoff_keeps_session(self):
        self.started()
        agents.mark_reset_pending(self.p, self.cfg())
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_h", "dispatchId": "ctx_h"}})
        agents.begin_reset(self.p, self.cfg(), "production")
        out = agents.finish_reset(self.p, "production", False, sleep=lambda _: None)
        self.assertEqual(out["status"], "handoff_failed")
        self.assertEqual(self.calls_for("terminal send"), [])
        self.assertEqual(self.agents_state()["production"]["since_reset"], 1)

    def test_begin_reset_skips_dead_terminal_and_busy_agent(self):
        self.started()
        agents.update(self.p, "production", lambda e: e.update(reset_pending=True, task="T-009"))
        self.assertIsNone(agents.begin_reset(self.p, self.cfg(), "production"))
        agents.update(self.p, "production", lambda e: e.update(task=None))
        self.respond("terminal show", STALE, code=1)
        self.assertEqual(agents.begin_reset(self.p, self.cfg(), "production")["status"], "skipped")
        self.assertEqual((self.agents_state()["production"]["reset_pending"], self.agents_state()["production"]["handle"]),
                         (False, None))

    def test_orch_agent_reset(self):
        self.orch("agent", "reset", "production")
        self.assertTrue(self.agents_state()["production"]["reset_pending"])
        self.assertEqual(self.orch("agent", "reset", "nobody", check=False).returncode, 2)
```

`test_templates.py` 에 더한다.

```python
    def test_handoff_template(self):
        text = (T / "handoff.md").read_text(encoding="utf-8")
        for key in ("{agent}", "{agent_title}", "{path}", "200줄", "worker_done"):
            self.assertIn(key, text)
        self.assertIn("{handoff_block}## 소유 경로", (T / "worker-spec.md").read_text(encoding="utf-8"))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents.ResetTest test_templates -v 2>&1 | tail -8`
Expected: FAIL — `handoff.md` 없음, `agents.mark_reset_pending` 없음, `orch agent` 없음.

- [ ] **Step 3: 템플릿을 만든다**

`orca-team/templates/handoff.md`:

```markdown
너는 orca-team 의 고정 에이전트 "{agent_title}"({agent}) 다. 이번 태스크는 인수인계 하나만 한다. 리뷰하지 않는다.

이 세션은 이 태스크가 끝나면 비워진다. 다음 세션이 이 파일 하나로 이어서 일할 수 있게 `{path}` 를 새로 쓴다(있으면 덮어쓴다).

## 쓸 내용 (200줄 이하)
1. 이 프로젝트에서 정한 결정과 그 이유. 문서(`docs/planning/`, `docs/design/`)에 이미 있는 것은 경로만 적는다.
2. 지켜 온 작업 규칙과 자주 틀렸던 점(리뷰에서 반복된 지적).
3. 남은 일과 알려진 문제. 태스크 ID 를 함께 적는다.
4. 다음 세션이 먼저 읽을 파일 목록.

## 하지 않는 것
- `{path}` 밖의 파일 수정. 기획·디자인·코드·테스트를 고치지 않는다.
- 리포트나 리뷰 내용 옮겨 적기. 경로만 적는다.

## 끝내기
1. `git add -- {path} && git commit -m "[handoff] {agent}: 인수인계" -- {path}`
2. worker_done 을 `--outcome succeeded` 로 한 번 보낸다. 쓰지 못했으면 `--outcome failed` 와 이유를 보낸다.
```

`orca-team/templates/worker-spec.md` 에서 "선행 태스크: {depends_on}. …먼저 읽는다." 줄 다음의 빈 줄과 `## 소유 경로` 사이를 `{handoff_block}## 소유 경로` 로 바꾼다. 결과는 다음과 같다.

```markdown
선행 태스크: {depends_on}. 시작 전에 `docs/planning/contracts.md`(있으면)와 선행 태스크 리포트 `docs/tasks/<선행 태스크 ID>/report.md` 를 먼저 읽는다.

{handoff_block}## 소유 경로
```

- [ ] **Step 4: `agents.py` 에 리셋 함수를 더한다**

import 에 `import time`, `from .paths import TEMPLATES, OrchError, Project`, `from .util import now, read_json, render, write_json` 을 반영하고, 상수 `HANDOFF_DIR = "docs/handoff"`, `RESET_SETTLE_SECONDS = 5.0` 을 `TAB_PREFIX` 아래에 둔다. 파일 끝에 더한다.

```python
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
    작업 터미널이 없으면 비울 세션이 없으므로 표시만 지운다."""
    agent = cfg.agents[name]
    e = entry(p, name)
    if not e.get("reset_pending") or e.get("task") or e.get("resetting") or e.get("queue"):
        return None
    handle = e.get("handle")
    if not handle or not orca.terminal_alive(handle):
        update(p, name, lambda x: x.update(reset_pending=False, since_reset=0, handle=None))
        return {"name": name, "status": "skipped"}
    spec = render((TEMPLATES / "handoff.md").read_text(encoding="utf-8"),
                  {"agent": name, "agent_title": agent.title, "path": handoff_rel(name)})
    code, receipt, _ = start(p, agent, spec, f"[handoff] {agent.title} 인수인계")
    dispatch = orca.find_key(receipt, ("dispatchId",))
    if code != 0 or not receipt.get("ok") or not dispatch:
        return {"name": name, "status": "failed", "receipt": receipt}
    update(p, name, lambda x: x.update(resetting={"dispatch_id": dispatch, "at": now()}))
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


def finish_reset(p: Project, name: str, ok: bool, sleep=time.sleep) -> dict:
    """인수인계 worker_done 뒤. 성공이면 /clear 로 세션을 비우고 입력 준비를 기다린다. 실패면 세션을 그대로 둔다.
    새 세션 화면을 그리는 동안 넣은 입력은 사라질 수 있어(실험 E4) tui-idle 뒤 RESET_SETTLE_SECONDS 를 더 기다린다."""
    handle = entry(p, name).get("handle")
    if not ok:
        update(p, name, lambda e: e.update(resetting=None, reset_pending=False))
        return {"name": name, "status": "handoff_failed"}
    if handle:
        _best_effort(["terminal", "send", "--terminal", handle, "--text", "/clear", "--enter", "--wait-submit", "10"])
        _best_effort(["terminal", "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", "30000"])
        sleep(RESET_SETTLE_SECONDS)
    update(p, name, lambda e: e.update(resetting=None, reset_pending=False, since_reset=0, last_reset_at=now()))
    return {"name": name, "status": "reset"}
```

- [ ] **Step 5: `compose_spec` 와 CLI 를 고친다**

`tasks.compose_spec` 의 `render(...)` 값 dict 에 `"handoff_block": agents.handoff_block(p, cfg, doc.role),` 를 더한다.

`cli.py` `build_parser` 의 `sub.add_parser("which")` 줄 앞에 더한다.

```python
    s = sub.add_parser("agent", help="고정 에이전트")
    agent_sub = s.add_subparsers(dest="agent_cmd", required=True)
    agent_sub.add_parser("reset", help="다음 태스크 전에 인수인계를 쓰고 세션을 비운다").add_argument("name")
```

`CONFIG_COMMANDS` 에 `"agent"` 를 더하고, `run` 의 마지막 `else:` 블록(`from . import tasks`) 앞에 더한다.

```python
    elif args.cmd == "agent":
        from . import agents

        emit(agents.request_reset(p, cfg, args.name))
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents test_templates test_tasks -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 7: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/templates/handoff.md orca-team/templates/worker-spec.md orca-team/lib/orch/agents.py orca-team/lib/orch/tasks.py orca-team/lib/orch/cli.py orca-team/tests/test_agents.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 묶음이 끝나면 고정 에이전트가 인수인계를 쓰고 세션을 비운다"
```

---

### Task 7: 고정 에이전트 복구

**Files:**
- Modify: `orca-team/lib/orch/agents.py` (`forget`, `forget_dead`)
- Modify: `orca-team/lib/orch/tasks.py` (`recover_unknown_start`, `UNKNOWN_START_LIMIT`, `DIED_NOTE`)
- Test: `orca-team/tests/test_agents.py`

**Interfaces:**
- Consumes: `orca.worker_info`(Task 1), `orca.terminal_state`, `tasks.launch(..., agent=)`, 이력 entry 의 `note`(Task 4).
- Produces: `agents.forget(p, name)`, `agents.forget_dead(p, cfg) -> list[str]`, `tasks.recover_unknown_start(p, cfg, task_id) -> dict`(`status`: `"not_unknown"`|`"relaunched"`|`"escalate"`), `tasks.UNKNOWN_START_LIMIT = 3`, `tasks.DIED_NOTE`(`{task_id}` 자리표시 문자열). 태스크 상태의 `unknown_starts`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_agents.py` 에 더한다.

```python
UNKNOWN = {"ok": True, "result": {"dispatchId": "ctx_1", "state": "start_unknown", "stage": "turn_start_unobserved",
                                  "liveness": "live", "terminal": "term_a1"}}


class RecoveryTest(AgentCase):
    def test_forget_dead_clears_only_dead_handles(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.assertEqual(agents.forget_dead(self.p, self.cfg()), [])
        self.respond("terminal show", STALE, code=1)
        self.assertEqual(agents.forget_dead(self.p, self.cfg()), ["production"])
        self.assertIsNone(self.agents_state()["production"]["handle"])
        self.assertEqual(self.agents_state()["production"]["task"], "T-001")  # 태스크 처리는 drive 가 한다

    def test_unknown_start_is_relaunched_on_new_agent(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-show", UNKNOWN)
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_5", "dispatchId": "ctx_5",
                                                                           "terminal": "term_a2"}})
        self.respond("terminal split", {"ok": True, "result": {"split": {"handle": "term_r2", "tabId": "tab_2"}}})
        out = tasks.recover_unknown_start(self.p, self.cfg(), "T-001")
        self.assertEqual((out["status"], out["count"]), ("relaunched", 1))
        for verb in ("worker-abandon", "worker-release"):
            self.assertEqual(self.arg(self.calls_for(f"orchestration {verb}")[-1], "--dispatch"), "ctx_1")
        self.assertIn("--keep-open", self.last_start())
        state = self.agents_state()["production"]
        self.assertEqual((state["handle"], state["task"]), ("term_a2", "T-001"))
        self.assertEqual(self.state("T-001")["dispatch_id"], "ctx_5")

    def test_third_unknown_start_escalates(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.set_state("T-001", unknown_starts=2)
        self.respond("orchestration worker-show", UNKNOWN)
        starts = len(self.calls_for("orchestration worker-start"))
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "escalate")
        self.assertEqual(len(self.calls_for("orchestration worker-start")), starts)

    def test_not_unknown_does_nothing(self):
        self.commit_spec("T-001")
        self.orch("start", "T-001")
        self.respond("orchestration worker-show", {"ok": True, "result": {"state": "running", "stage": "working"}})
        self.assertEqual(tasks.recover_unknown_start(self.p, self.cfg(), "T-001")["status"], "not_unknown")
        self.assertEqual(self.calls_for("orchestration worker-abandon"), [])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents.RecoveryTest -v 2>&1 | tail -5`
Expected: FAIL — `forget_dead`, `recover_unknown_start` 없음.

- [ ] **Step 3: `agents.py` 에 더한다**

```python
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
```

- [ ] **Step 4: `tasks.py` 에 더한다**

상수 블록(`LAUNCHING_NEXT` 아래)에 더한다.

```python
UNKNOWN_START_LIMIT = 3  # 같은 태스크에서 start_unknown 이 이만큼이면 다시 띄우지 않고 메인에 올린다
DIED_NOTE = "이전 작업자가 중간에 끝났다. 리포트와 [{task_id}] 커밋을 확인하고 남은 일을 이어서 한다"
```

`next_action` 앞에 더한다.

```python
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
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_agents -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 6: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/agents.py orca-team/lib/orch/tasks.py orca-team/tests/test_agents.py
git commit -m "feat(orca-team): 죽은 고정 에이전트와 입력이 확인되지 않은 시작을 새 작업자로 복구한다"
```

---

### Task 8: `decide` 답, `orch design-decide`, `orch scope`

**Files:**
- Modify: `orca-team/lib/orch/comm.py` (`REPLY_TYPES`)
- Modify: `orca-team/lib/orch/ask.py` (`answer`)
- Modify: `orca-team/lib/orch/plan.py` (새 `design_decide`)
- Modify: `orca-team/lib/orch/tasks.py` (새 `scope`)
- Modify: `orca-team/lib/orch/cli.py` (`design-decide`, `scope`)
- Test: `orca-team/tests/test_decide.py` (새), `orca-team/tests/test_ask.py`

**Interfaces:**
- Consumes: `comm.post_decide`, `queue.submit`(Task 3), `tasks.fix`·`accept`(Task 5).
- Produces: `comm.REPLY_TYPES = ("approval", "decide")`; `ask.answer` 가 `decide` 를 닫고, `kind == "worker_question"` 이면 `queue.submit(p, "reply", {"message_id", "body"})`; `approval` 에 `answer` 하면 exit 2(`design-decide` 안내); `plan.design_decide(p, cfg, out_id, approve: list[str], fixes: dict[str, str]) -> dict`(`approved`, `fixed`, `dependent_fixes`, `deferred`, `start_ready`); 태스크 상태 `pending_dep_fix`(문자열, Task 11 drive 가 처리); `tasks.scope(p, task_id, body) -> dict`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_decide.py`:

```python
import json
import os
import unittest
from unittest import mock

from helpers import FAKES
from test_tasks import TaskCase
from orch import ask, comm, plan, tasks
from orch.config import Config
from orch.paths import OrchError, Project


class DecideCase(TaskCase):
    def setUp(self):
        super().setUp()
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": self.env["ORCA_TERMINAL_HANDLE"],
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def cfg(self):
        return Config.load(self.root / ".orchestration/config.toml")


class AnswerDecideTest(DecideCase):
    def test_decide_is_closed_once(self):
        entry = comm.post_decide(self.p, "worker_failed", "T-001", "실패")
        self.assertIn(entry["id"], [q["id"] for q in ask.open_questions(self.p)])
        ask.answer(self.p, entry["id"], "fix 로 다시 보냈다")
        self.assertNotIn(entry["id"], [q["id"] for q in ask.open_questions(self.p)])
        with self.assertRaises(OrchError) as ctx:
            ask.answer(self.p, entry["id"], "또")
        self.assertEqual(ctx.exception.code, 3)

    def test_worker_question_reply_goes_through_queue(self):
        entry = comm.post_decide(self.p, "worker_question", "T-001", "토큰 이름?", extra={"message_id": "msg_9",
                                                                                          "question": "토큰 이름?"})
        with mock.patch("orch.queue.submit", return_value={"replied": "msg_9"}) as submit:
            out = ask.answer(self.p, entry["id"], "color.primary 를 쓴다")
        submit.assert_called_once_with(self.p, "reply", {"message_id": "msg_9", "body": "color.primary 를 쓴다"})
        self.assertEqual(out["sent"], {"replied": "msg_9"})

    def test_approval_answer_points_to_design_decide(self):
        entry = comm.post(self.p, "approval", "디자인 확인", {"tasks": ["T-001"]})
        with self.assertRaises(OrchError) as ctx:
            ask.answer(self.p, entry["id"], "전부 승인")
        self.assertIn("design-decide", str(ctx.exception))


class DesignDecideTest(DecideCase):
    def awaiting(self, task_id, depends=()):
        extra = f"owns: docs/design/{task_id}/**\n" + (f"depends_on: {', '.join(depends)}\n" if depends else "")
        self.write_spec(task_id, role="designer", extra=extra)  # 화면마다 소유 경로가 달라야 fix 의 잠금 검사를 지난다
        (self.root / f".orchestration/state/tasks/{task_id}.json").write_text(json.dumps({
            "id": task_id, "role": "designer", "title": "화면", "status": "awaiting_user", "stage": "work", "round": 1,
            "base": self.git("rev-parse", "HEAD").strip(), "owns": [f"docs/design/{task_id}/**"],
            "depends_on": list(depends), "agent": "temp", "task_id": "t", "dispatch_id": "d", "released": True,
            "history": [], "verdicts": {"1": "APPROVE"}}), encoding="utf-8")

    def test_fix_spreads_to_dependent_screen(self):
        self.awaiting("T-001")
        self.awaiting("T-002", depends=["T-001"])
        self.awaiting("T-003")
        entry = comm.post(self.p, "approval", "디자인 3개", {"tasks": ["T-001", "T-002", "T-003"]})
        out = plan.design_decide(self.p, self.cfg(), entry["id"], ["T-002", "T-003"], {"T-001": "색을 밝게"})
        self.assertEqual((out["approved"], out["fixed"], out["dependent_fixes"]), (["T-003"], ["T-001"], ["T-002"]))
        notes = [json.loads((self.root / f".orchestration/state/tasks/{t}.json").read_text(encoding="utf-8"))
                 for t in ("T-001", "T-002", "T-003")]
        self.assertEqual([n["status"] for n in notes], ["working", "working", "done"])
        self.assertIn("선행 디자인 T-001 가 바뀐다: 색을 밝게", notes[1]["history"][-1]["note"])
        self.assertIn(entry["id"], comm.load_answered(self.p)["ids"])

    def test_every_screen_needs_a_decision(self):
        self.awaiting("T-001")
        self.awaiting("T-002")
        entry = comm.post(self.p, "approval", "디자인 2개", {"tasks": ["T-001", "T-002"]})
        with self.assertRaises(OrchError) as ctx:
            plan.design_decide(self.p, self.cfg(), entry["id"], ["T-001"], {})
        self.assertIn("T-002", str(ctx.exception))
        with self.assertRaises(OrchError):
            plan.design_decide(self.p, self.cfg(), entry["id"], ["T-001", "T-002"], {"T-001": "x"})

    def test_working_dependent_is_deferred(self):
        self.awaiting("T-001")
        self.awaiting("T-002", depends=["T-001"])
        self.set_state("T-002", status="working")
        entry = comm.post(self.p, "approval", "디자인 1개", {"tasks": ["T-001"]})
        out = plan.design_decide(self.p, self.cfg(), entry["id"], [], {"T-001": "여백"})
        self.assertEqual(out["deferred"], ["T-002"])
        self.assertIn("T-001", self.state("T-002")["pending_dep_fix"])

    def test_cli_parses_fix_pairs(self):
        self.awaiting("T-001")
        entry = comm.post(self.p, "approval", "디자인 1개", {"tasks": ["T-001"]})
        out = json.loads(self.orch("design-decide", entry["id"], "--fix", "T-001=제목을 키운다 = 크게").stdout)
        self.assertEqual(out["fixed"], ["T-001"])
        self.assertEqual(self.state("T-001")["history"][-1]["note"], "제목을 키운다 = 크게")


class ScopeTest(DecideCase):
    def test_scope_sends_to_dispatch(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        out = tasks.scope(self.p, "T-001", "-다크 모드도 맞춘다")
        self.assertTrue(out["sent"])
        call = self.calls_for("orchestration send")[-1]
        self.assertEqual(self.arg(call, "--to"), "dispatch:ctx_1")
        self.assertIn("--body=-다크 모드도 맞춘다", call)
        self.set_state("T-001", status="changes")
        with self.assertRaises(OrchError):
            tasks.scope(self.p, "T-001", "x")


if __name__ == "__main__":
    unittest.main()
```

`test_ask.py` 에서 `question` 타입에 `orch answer` 하면 코디네이터 Run 으로 보내는지 보는 테스트(본문에 `comm.tell` 이나 `orchestration send` 를 확인하는 것)를 지운다. `grep -n "question" orca-team/tests/test_ask.py` 로 찾는다. ask 타입 답 파일 테스트는 그대로 둔다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_decide -v 2>&1 | tail -8`
Expected: FAIL — decide 가 열린 질문에 없음, `design_decide`·`scope` 없음.

- [ ] **Step 3: `comm.py`·`ask.py` 를 고친다**

`comm.py`: `REPLY_TYPES = ("approval", "decide")  # 메인이 답해 닫는 타입. 답한 id 는 state/answered.json 에 남긴다`.

`ask.answer` 전체를 바꾼다(맨 위 import 에 `from . import queue` 를 더한다. `queue` 가 `comm` 만 import 하므로 순환은 없다).

```python
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
```

- [ ] **Step 4: `plan.design_decide` 와 `tasks.scope` 를 더한다**

`plan.py` 끝에 더한다(import 에 `accept`, `fix`, `FINISHED`, `save_state` 가 `from .tasks import ...` 에 없으면 더한다. `ask` 는 함수 안에서 import 한다).

```python
def design_decide(p: Project, cfg: Config, out_id: str, approve: list[str], fixes: dict[str, str]) -> dict:
    """디자인 승인 질문(approval out_id)에 대한 사용자 답을 화면별 결정으로 적용한다.

    물은 화면마다 승인(approve) 또는 수정(fixes: T-id → 사용자 요청 원문)이 하나씩 있어야 한다. 고치는 화면을 선행으로 둔
    승인 대상 태스크는 승인했더라도 accept 하지 않고 함께 고친다. 그 태스크가 아직 일하는 중이면 pending_dep_fix 로
    남기고 drive 가 리뷰 뒤에 고친다. 끝나면 질문을 닫고 start-ready."""
    from .ask import find_entry

    require_coordinator(p)
    entry = find_entry(p, out_id)
    if not entry or entry.get("type") != "approval":
        raise OrchError(f"{out_id} 는 디자인 승인 질문(approval)이 아니다", 2)
    asked = [t for t in entry.get("tasks") or [] if isinstance(t, str)]
    approve = list(dict.fromkeys(approve))
    problems = []
    unknown = [t for t in [*approve, *fixes] if t not in asked]
    both = sorted(set(approve) & set(fixes))
    missing = [t for t in asked if t not in approve and t not in fixes]
    if unknown:
        problems.append(f"묻지 않은 화면: {', '.join(unknown)}")
    if both:
        problems.append(f"승인과 수정에 함께 있다: {', '.join(both)}")
    if missing:
        problems.append(f"결정이 없는 화면: {', '.join(missing)}. 사용자에게 다시 묻는다")
    empty = [t for t, note in fixes.items() if not note.strip()]
    if empty:
        problems.append(f"수정 요청이 비었다: {', '.join(empty)}")
    if problems:
        raise OrchError("; ".join(problems), 2)
    roles = cfg.user_approval_roles
    via: dict[str, str] = {}  # 함께 고칠 태스크 → 바뀐 선행 태스크
    changed = set(fixes)
    grew = True
    while grew:
        grew = False
        for st in all_states(p):
            if st["id"] in changed or st.get("role") not in roles or st.get("status") in FINISHED:
                continue
            hit = next((d for d in st.get("depends_on") or [] if d in changed), None)
            if hit:
                via[st["id"]] = hit
                changed.add(st["id"])
                grew = True

    def request_of(task_id: str) -> str:
        while task_id in via:
            task_id = via[task_id]
        return fixes[task_id]

    out = {"id": out_id, "approved": [], "fixed": [], "dependent_fixes": [], "deferred": []}
    with comm.answered_lock(p):
        if out_id in comm.load_answered(p)["ids"]:
            raise OrchError(f"{out_id} 에는 이미 답했다(state/answered.json)", 3)
        for task_id in approve:
            if task_id not in via:
                accept(p, task_id)
                out["approved"].append(task_id)
        for task_id, note in fixes.items():
            fix(p, cfg, task_id, note.strip())
            out["fixed"].append(task_id)
        for task_id, source in via.items():
            note = f"선행 디자인 {source} 가 바뀐다: {request_of(task_id)}. 바뀐 선행 디자인에 맞춘다"
            st = load_state(p, task_id) or {}
            if st.get("status") in ("awaiting_user", "changes"):
                fix(p, cfg, task_id, note)
                out["dependent_fixes"].append(task_id)
            else:
                st["pending_dep_fix"] = note
                save_state(p, st)
                out["deferred"].append(task_id)
        comm.record_answered(p, out_id)
    out["start_ready"] = start_ready(p, cfg)
    return out
```

`tasks.py` 의 `which` 앞에 더한다.

```python
def scope(p: Project, task_id: str, body: str) -> dict:
    """일하는 중인 태스크에 추가 범위를 알린다. 메인이 spec 에 "추가 범위"를 먼저 커밋한다.
    pending 태스크는 spec 을 고쳐 커밋하면 되므로 받지 않는다."""
    require_coordinator(p)
    st = must_state(p, task_id)
    if st.get("status") != "working" or not st.get("dispatch_id"):
        raise OrchError(f"{task_id}: 일하는 중(working)인 태스크에만 범위를 더한다(status={st.get('status')}). "
                        "시작 전이면 spec 을 고쳐 커밋한다", 2)
    body = body.strip()
    if not body:
        raise OrchError("추가 범위가 비었다", 2)
    orca.call(["orchestration", "send", "--to", f"dispatch:{st['dispatch_id']}", "--subject", "추가 범위",
               f"--body={body}"])  # '-' 로 시작하는 본문이 플래그로 읽히지 않게
    st["history"].append({"stage": "scope", "round": st.get("round"), "at": now(), "body": body})
    save_state(p, st)
    return {"id": task_id, "sent": True}
```

- [ ] **Step 5: CLI 를 더한다**

`cli.py` `add_graph_commands` 끝에 더한다.

```python
    s = sub.add_parser("design-decide", help="디자인 승인 답을 화면별 결정으로 적용한다(메인)")
    s.add_argument("id", help="approval 메시지 id(out-NNNN)")
    s.add_argument("--approve", default="", help="승인한 태스크 ID, 쉼표로 구분")
    s.add_argument("--fix", action="append", default=[], metavar="T-xxx=<요청>", help="고칠 태스크와 사용자 요청 원문")
```

`GRAPH_COMMANDS` 에 `"design-decide"` 를 더하고 `run_graph` 첫 줄 아래에 더한다.

```python
    if args.cmd == "design-decide":
        fixes = {}
        for pair in args.fix:
            task_id, sep, note = pair.partition("=")
            if not sep:
                raise OrchError(f"--fix 는 T-xxx=<요청> 형식이다: {pair}", 2)
            fixes[task_id.strip()] = note.strip()
        approve = [t.strip() for t in args.approve.split(",") if t.strip()]
        emit(plan.design_decide(p, cfg, args.id, approve, fixes))
        return 0
```

`build_parser` 에서 `sub.add_parser("which")` 앞에 더한다.

```python
    s = sub.add_parser("scope", help="일하는 중인 태스크에 추가 범위를 알린다(메인, spec 커밋 뒤)")
    s.add_argument("id")
    s.add_argument("body")
```

`run` 의 마지막 `else:` 블록 안 `elif args.cmd == "drop":` 다음에 더한다.

```python
        elif args.cmd == "scope":
            emit(tasks.scope(p, args.id, args.body))
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_decide test_ask test_plan -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 7: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/comm.py orca-team/lib/orch/ask.py orca-team/lib/orch/plan.py orca-team/lib/orch/tasks.py orca-team/lib/orch/cli.py orca-team/tests/test_decide.py orca-team/tests/test_ask.py
git commit -m "feat(orca-team): decide 답과 디자인 화면별 결정, 추가 범위 명령을 더한다"
```

---

### Task 9: 훅 — 고정 에이전트 판별과 메인 규칙

코디네이터 역할을 지우고, `/clear` 뒤 새 세션 id 로 SessionStart 가 와도 고정 에이전트 작업 터미널이면 워커로 본다(그래야 메인 안내와 자동 부활 검사가 에이전트에서 돌지 않는다). 고정 에이전트는 한 세션에서 여러 직무를 받으므로 워커 세션은 태스크 프리앰블마다 직무를 다시 적는다.

**Files:**
- Modify: `orca-team/hooks/orca_team_hook.py`
- Test: `orca-team/tests/test_hooks.py`

**Interfaces:**
- Consumes: `Project.agents_json`(Task 3).
- Produces: 훅 함수 `env_agent(p) -> bool`, `worker_role(prompt) -> str | None`. 메인 Bash 에서 `orca orchestration …`·`orch drive` 를 거부한다. `stop` 은 `run.json` 의 `run_id` 로 세팅 끝을 본다. 훅은 더 이상 `orch.comm.main_message_ids` 를 import 하지 않는다.

- [ ] **Step 1: 테스트를 고친다**

`test_hooks.py`:
- 지운다: `test_coordinator_rules`, `test_session_start_silent_for_env_coordinator_without_marker`, `test_coordinator_after_clear_with_nudge_prompt_keeps_role`, `test_env_coordinator_overrides_existing_main_marker`, `test_reply_to_recorded_main_message_is_denied`, `test_reply_to_other_message_is_allowed`, `test_coordinator_raw_check_is_denied_worker_keeps_it`, `test_unparseable_reply_is_allowed`, `test_coordinator_may_edit_only_orchestration`.
- `test_roles_from_first_prompt`: `s-co` 줄을 지우고 기대값을 `["main", "worker"]`(s-main, s-w)로 바꾼다.
- 메인 Bash 허용 검사(`orch tell "로그인"` 을 허용하던 줄)를 `.orchestration/bin/orch start T-001` 허용으로 바꾼다.
- `test_stop_ignores_coordinator_and_missing_coordinator` 를 `test_stop_needs_run_id` 로 바꾼다: `run.json` 을 `{}` 로 쓰면 stop 이 None, `{"run_id": "run_x"}` 이고 watch 가 없으면 `decision == "block"`.
- `test_bad_session_id_falls_back_to_env_check_or_main`: 잘못된 session id 에서 `ORCA_TERMINAL_HANDLE` 이 `agents.json` 의 handle 이면 워커(메인 안내 없음), 아니면 메인으로 기대값을 바꾼다.
- `test_coordinator_and_worker_deny_messages_are_unchanged`: 코디네이터 부분을 지우고 워커 AskUserQuestion 거부 이유에 "프리앰블의 ask 명령" 과 "메인" 이 있는지 본다.
- 자동 부활 클래스의 `test_coordinator_and_worker_sessions_skip`: 코디네이터 프롬프트 줄을 지운다(워커만 본다).

`RoleMarkTest` 끝에 더한다.

```python
    def test_worker_marker_follows_each_preamble(self):
        self.mark("s-w", "Your task ID is: t1\n너는 orca-team 의 개발(developer) 워커다.")
        self.assertEqual(self.role("s-w"), "worker")
        self.mark("s-w", "Your task ID is: t2\n너는 orca-team 의 기획(planner) 워커다.")
        self.assertEqual(self.role("s-w"), "worker:planner")
        self.mark("s-main", "안녕")
        self.mark("s-main", "Your task ID is: t3")
        self.assertEqual(self.role("s-main"), "main")

    def test_agent_terminal_is_worker_without_marker(self):
        (self.root / ".orchestration/state/agents.json").write_text(
            json.dumps({"production": {"handle": "term_a1"}}), encoding="utf-8")
        env = {"ORCA_TERMINAL_HANDLE": "term_a1"}
        self.assertIsNone(self.hook("session-start", {"session_id": "after-clear", "source": "clear"}, env=env))
        out = self.hook("guard", {"session_id": "after-clear", "tool_name": "Write",
                                  "tool_input": {"file_path": str(self.root / "src/a.ts")}}, env=env)
        self.assertIsNone(self.decision(out))
```

`GuardTest` 에 더한다.

```python
    def test_main_cannot_call_orca_orchestration_or_run_drive(self):
        self.mark("m", "안녕")
        for command in ("orca orchestration send --to run:x", ".orchestration/bin/orch drive", "cd x && orch drive"):
            with self.subTest(command=command):
                out = self.tool("m", "Bash", {"command": command})
                self.assertEqual(self.decision(out), "deny")
        self.assertIn("관제", self.tool("m", "Bash", {"command": "orca orchestration check"})
                      ["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self.tool("m", "Bash", {"command": ".orchestration/bin/orch drop T-001 --reason x"}))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_hooks -v 2>&1 | tail -8`
Expected: FAIL — 워커 직무가 다시 적히지 않음, agents.json handle 판별 없음, `orch drive` 허용.

- [ ] **Step 3: 훅을 고친다**

`orca_team_hook.py`:
- import 줄 `from orch.comm import main_message_ids, read_outbox, unread` 를 `from orch.comm import read_outbox, unread` 로 바꾼다.
- 상수 `COORDINATOR_MARK`, `ORCA_REPLY_RE`, `ORCA_CHECK_RE`, `REPLY_ID_RE`, `COORDINATOR_PREFIX` 와 함수 `env_coordinator`, `main_reply_ids` 를 지운다.
- 상수를 더하고 `DEFAULT_UP_LINE` 을 바꾼다.

```python
ORCH_DRIVE_RE = re.compile(r"(^|[\s;&|(`])(?:\S*/)?orch\s+drive\b")
# 세션 시작 안내의 관제 줄(자동 부활 문구가 없을 때). /orca-team:up 은 수동 복구로 남긴다.
DEFAULT_UP_LINE = ("- 관제(orch drive)는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다. 메시지 watcher 가 꺼져 있으면 Bash "
                   "`run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. 관제 상태가 의심되면 "
                   "`/orca-team:up` 을 실행한다.")
```

- `role_of` 위의 주석과 함수, `role_mark` 를 바꾸고 `env_agent`·`worker_role` 을 더한다.

```python
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
```

- `guard` 를 바꾼다(코디네이터 분기를 모두 지운다).

```python
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
```

- `session_start` 의 `lines` 앞 두 줄과 밀린 메시지 문구를 바꾼다.

```python
    lines = ["orca-team 프로젝트다. 이 안내는 사용자와 대화하는 메인 세션에만 해당한다. Orca 워커 작업 지시라면 이 안내를 무시한다.",
             "- 메인은 사용자와 대화하고 판단한다. 산출물은 태스크로 맡기고, 관제가 올린 decide 를 처리한다(CLAUDE.md \"메인 규칙\")."]
```

```python
        lines.append(f"- 아직 전하지 않은 관제·워커 메시지가 {pending}건 있다. watcher 를 띄우면 바로 받는다.")
```

- `stop` 의 조건과 문구를 바꾼다.

```python
    if p.setup_mode.exists() or not load_run(p).get("run_id") or pid_alive(p.watch_pid):
        return None
    return {"decision": "block",
            "reason": "orca-team: 관제·워커 메시지 watcher 가 꺼져 있다. Bash `run_in_background: true` 로 "
                      "`.orchestration/bin/orch watch` 를 띄우고 턴을 끝낸다. 이미 받은 메시지가 있으면 사용자에게 먼저 전한다."}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_hooks -v 2>&1 | tail -5`
Expected: OK. 자동 부활 테스트는 Task 10 에서 관제 기준으로 바뀐다. 이 단계에서는 코디네이터 부활 흐름 그대로 통과해야 한다.

- [ ] **Step 5: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/hooks/orca_team_hook.py orca-team/tests/test_hooks.py
git commit -m "feat(orca-team): 훅이 고정 에이전트 터미널을 워커로 알아보고 코디네이터 규칙을 지운다"
```

---

### Task 10: 관제 뼈대와 정체성 전환

코디네이터 대신 `orch drive` 가 Run 을 갖는다. `run.json` 의 `coordinator_handle` 은 `drive_handle` 이 된다. 메인이 Orca 를 부르는 명령을 실행하면 큐를 거친다. 이 태스크의 drive 는 큐만 처리한다(메일·리뷰는 Task 11).

**Files:**
- Create: `orca-team/lib/orch/drive.py`
- Modify: `orca-team/lib/orch/util.py` (`is_drive`, `require_drive`, `require_coordinator` 제거)
- Modify: `orca-team/lib/orch/tasks.py`, `plan.py`, `review.py`, `setup.py` (`require_coordinator` → `require_drive`)
- Modify: `orca-team/lib/orch/setup.py` (`up`, `launch_drive`, `status`, `COORDINATOR_PROMPT`·`launch_coordinator` 제거)
- Modify: `orca-team/lib/orch/autoup.py` (관제 기준)
- Modify: `orca-team/lib/orch/comm.py` (`tell`·`wait`·`main_message_ids`·`remember_main_messages` 제거), `orca-team/lib/orch/paths.py` (`wait_pid`·`main_msg_ids` 제거)
- Modify: `orca-team/lib/orch/ask.py` (`notify_question` 제거)
- Modify: `orca-team/lib/orch/cli.py` (`drive`, 큐 경유, `tell`·`wait`·`notify --json` 제거)
- Modify: `orca-team/tests/helpers.py`
- Delete: `orca-team/tests/test_coordinator.py`
- Create: `orca-team/tests/test_up.py`
- Modify: `orca-team/tests/test_autoup.py`, `test_comm.py`, `test_ask.py`, `test_cli.py`, `test_hooks.py`, 그 밖에 `term_coord`·`coordinator_handle` 을 쓰는 테스트

**Interfaces:**
- Consumes: `queue.submit`·`pending`·`complete`, `util.drive_alive`(Task 3), `orca.shell_alive`(Task 1), `agents.summary`(Task 4), `agents.request_reset`(Task 6), `plan.design_decide`·`tasks.scope`(Task 8).
- Produces: `util.is_drive(p) -> bool`, `util.require_drive(p)`; `drive.Drive(p, cfg)` 의 `log(text)`, `beat()`, `bind() -> dict`, `run_queue() -> int`, `execute(request) -> dict`, `accepted(task_id, result) -> dict`(Task 11 이 확장), `tick(wait_ms=WAIT_MS)`, `run_forever()`; `drive.main(p, cfg) -> int`; `setup.launch_drive(p) -> dict`, `setup.DRIVE_TITLE = "orca-team 관제"`, `setup.drive_command(p) -> str`; `orch status` 의 `drive`·`agents`; `cli.QUEUED`. 테스트 환경의 관제 handle 은 `term_drive` 다.

- [ ] **Step 1: 테스트 환경을 바꾼다**

`helpers.py`:
- `ProjectCase` docstring 을 `"""임시 git 프로젝트. 기본 환경은 관제 터미널(ORCA_TERMINAL_HANDLE=term_drive)이다."""` 로 바꾼다.
- env 의 `"ORCA_TERMINAL_HANDLE": "term_coord"` 를 `"term_drive"` 로 바꾸고, `for key in ("ORCA_CLI_COMMAND", "CLAUDE_PROJECT_DIR"):` 를 `for key in ("ORCA_CLI_COMMAND", "CLAUDE_PROJECT_DIR", "CLAUDECODE"):` 로 바꾼다(Claude Code 안에서 테스트를 돌려도 drive 가 거부하지 않게).
- `scaffold` 의 run.json 을 `{"run_id": "run_x", "drive_handle": "term_drive"}` 로 바꾼다.
- `ProjectCase` 에 더한다.

```python
    def drive_alive(self) -> None:
        """관제가 살아 있는 것으로 보이게 한다(이 테스트 프로세스 pid 와 지금 heartbeat)."""
        drive_dir = self.root / ".orchestration/state/drive"
        drive_dir.mkdir(parents=True, exist_ok=True)
        (drive_dir / "drive.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        from orch.util import now
        (drive_dir / "heartbeat").write_text(now(), encoding="utf-8")
```

나머지 테스트의 이름을 한 번에 바꾼다.

```bash
cd orca-team/tests
grep -l 'term_coord\|coordinator_handle' *.py | xargs sed -i '' -e 's/term_coord/term_drive/g' -e 's/coordinator_handle/drive_handle/g'
git rm -q test_coordinator.py
```

`test_comm.py` 에서 `test_tell_*` 세 개와 `test_wait_*` 다섯 개(`test_wait_requires_coordinator_terminal`, `test_wait_uses_fixed_arguments`, `test_wait_records_main_message_ids`, `test_wait_without_main_messages_writes_nothing`, `test_wait_refuses_second_instance`)를 지운다. `test_ask.py` 에서 `NotifyQuestionJsonTest` 클래스를 지운다. `test_cli.py` 에서 `tell`·`wait` 명령을 부르는 검사가 있으면 지운다.

메인 환경(`ORCA_TERMINAL_HANDLE=term_main`)에서 큐 명령(`start`, `fix`, `accept`, `drop`, `start-ready`, `design-decide`)을 불러 "관제 터미널에서만" 거부(exit 2)를 기대하던 테스트는 이제 큐로 가므로 "관제가 꺼져 있다"(exit 1, stderr 에 `/orca-team:up`)를 기대하도록 바꾼다. `grep -n "term_main" orca-team/tests/test_tasks.py orca-team/tests/test_plan.py orca-team/tests/test_ask.py` 로 찾는다(예: `test_tasks.py` 의 메인 `start` 거부, `test_drop_refused_for_non_coordinator`). 큐에 가지 않는 명령(`review`, `plan apply`, `design-review`, `bind`)의 거부는 exit 2 그대로다.

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`orca-team/tests/test_up.py`:

```python
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from helpers import FAKES, ORCH, ProjectCase
from orch import comm, drive, queue, setup
from orch.config import Config
from orch.paths import OrchError, Project

MAIN = {"ORCA_TERMINAL_HANDLE": "term_main"}
SHELL = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}
CREATED = {"ok": True, "result": {"terminal": {"handle": "term_d1", "title": "orca-team 관제"}}}
SPEC = "---\nid: T-001\nrole: developer\ntitle: 로그인\n---\n## Target\nsrc\n\n## Change\n폼\n\n## Constraints\n없음\n\n## Acceptance\n통과\n"


class UpCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.run_json = self.root / ".orchestration/state/run.json"
        self.p = Project(self.root)
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": "term_drive",
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_run(self, **run):
        self.run_json.write_text(json.dumps(run), encoding="utf-8")

    def run_state(self):
        return json.loads(self.run_json.read_text(encoding="utf-8"))


class UpTest(UpCase):
    def test_up_creates_drive_tab(self):
        self.set_run(run_id="run_x")
        self.respond("terminal create", CREATED)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("launched", "term_d1"))
        call = self.calls_for("terminal create")[-1]
        self.assertEqual(call[call.index("--title") + 1], "orca-team 관제")
        self.assertTrue(call[call.index("--command") + 1].endswith("/.orchestration/bin/orch drive"))
        self.assertEqual(self.run_state()["drive_handle"], "term_d1")

    def test_up_restarts_drive_in_old_shell(self):
        self.set_run(run_id="run_x", drive_handle="term_d0")
        self.respond("terminal show", SHELL)
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual((out["status"], out["handle"]), ("relaunched", "term_d0"))
        sent = self.calls_for("terminal send")[-1]
        self.assertTrue(sent[sent.index("--text") + 1].endswith(" drive"))
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_up_leaves_live_drive(self):
        self.set_run(run_id="run_x", drive_handle="term_drive")
        self.drive_alive()
        out = json.loads(self.orch("up", env=MAIN).stdout)
        self.assertEqual(out["status"], "running")
        self.assertEqual(self.calls_for("terminal create"), [])

    def test_up_refuses_own_terminal_as_drive(self):
        self.set_run(run_id="run_x")
        self.respond("terminal create", {"ok": True, "result": {"terminal": {"handle": "term_main"}}})
        proc = self.orch("up", check=False, env=MAIN)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("메인 자신의 터미널", proc.stderr)

    def test_status_shows_drive_and_agents(self):
        self.drive_alive()
        out = json.loads(self.orch("status", env=MAIN).stdout)
        self.assertEqual((out["drive"]["handle"], out["drive"]["alive"]), ("term_drive", True))
        self.assertEqual(out["agents"], [])  # scaffold() 는 [agents] 를 지운다


class DriveMainTest(UpCase):
    def test_refuses_inside_claude_and_without_terminal(self):
        cfg = Config.load(self.root / ".orchestration/config.toml")
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), self.assertRaises(OrchError):
            drive.main(self.p, cfg)
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": ""}), self.assertRaises(OrchError):
            drive.main(self.p, cfg)

    def test_bind_creates_run_from_drive_terminal(self):
        self.set_run(drive_handle="term_drive")
        self.respond("orchestration run-create", {"ok": True, "result": {"runId": "run_new"}})
        out = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml")).bind()
        self.assertEqual((out["status"], self.run_state()["run_id"]), ("created", "run_new"))


class QueueRoutingTest(UpCase):
    def setUp(self):
        super().setUp()
        (self.root / ".orchestration/tasks/T-001.md").write_text(SPEC, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "setup")
        self.respond("orchestration worker-start", {"ok": True, "result": {"taskId": "task_1", "dispatchId": "ctx_1"}})

    def test_main_start_goes_through_drive(self):
        self.drive_alive()
        d = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"))
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                d.run_queue()
                time.sleep(0.05)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            out = json.loads(self.orch("start", "T-001", env=MAIN).stdout)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(out["dispatch_id"], "ctx_1")
        self.assertEqual(json.loads((self.root / ".orchestration/state/tasks/T-001.json").read_text())["status"],
                         "working")

    def test_main_start_refuses_without_drive(self):
        proc = self.orch("start", "T-001", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("/orca-team:up", proc.stderr)
        self.assertEqual(self.calls_for("orchestration worker-start"), [])

    def test_main_spec_error_is_reported_before_queue(self):
        self.drive_alive()
        proc = self.orch("start", "T-009", check=False, env=MAIN)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(queue.pending(self.p), [])

    def test_drive_side_runs_directly(self):
        out = json.loads(self.orch("start", "T-001").stdout)  # 기본 환경이 관제 터미널이다
        self.assertEqual(out["dispatch_id"], "ctx_1")


if __name__ == "__main__":
    unittest.main()
```

`test_autoup.py` 의 `DecideTest` 를 다음으로 바꾼다(`AutoUpCase` 의 `set_run` 은 그대로 쓰고, `setUp` 에서 `terminal show` 응답을 등록하던 줄이 있으면 지운다). `CheckTest`·`SpawnTest` 에서 `terminal show` 로 죽은 코디네이터를 만들던 곳은 "관제 pid 없음"(아무것도 하지 않으면 죽은 것)으로, 살아 있는 경우는 `self.drive_alive()` 로 바꾼다. `test_spawn_failure_reports_unknown` 의 기대 문구는 `autoup.FAILED_SPAWN` 으로 바꾼다.

```python
class DecideTest(AutoUpCase):
    def test_dead_drive_spawns(self):
        self.set_run(run_id="run_x")
        self.assertEqual(autoup.decide(self.p), ("spawn", autoup.REVIVED))

    def test_alive_does_nothing(self):
        self.set_run(run_id="run_x")
        self.drive_alive()
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_hung_drive_reports_without_spawn(self):
        self.set_run(run_id="run_x")
        self.drive_alive()
        (self.root / ".orchestration/state/drive/heartbeat").write_text("2000-01-01T00:00:00+00:00", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, autoup.HUNG))

    def test_disabled_reports_only(self):
        self.set_run(run_id="run_x")
        self.set_auto_up("false")
        self.assertEqual(autoup.decide(self.p), (None, autoup.DISABLED))

    def test_starting_or_spawn_running_skips(self):
        self.set_run(run_id="run_x", launched_at=self.ago(10))
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_redead_within_retry_window_reports_failed(self):
        self.set_run(run_id="run_x")
        self.set_check(spawned_at=self.ago(60))
        self.assertEqual(autoup.decide(self.p), (None, autoup.FAILED))

    def test_not_eligible_or_broken_config_skips(self):
        self.set_run()
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.set_run(run_id="run_x")
        (self.root / ".orchestration/config.toml").write_text("[coordinator]\n", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, None))
```

`AutoUpCase.set_auto_up` 이 `[coordinator]` 아래 값을 바꾸던 것이면 `[drive]` 의 `auto_up = true` 를 바꾸도록 고친다. `test_hooks.py` 의 자동 부활 테스트(`test_prompt_revives_dead_coordinator` 등)는 `terminal show` STALE 대신 관제 pid 가 없는 상태로 두고, `agent launch` 응답 대신 `terminal create` 응답(`CREATED` 모양)을 등록하고, 끝에 `run.json` 의 `drive_handle` 이 `term_new` 인지 본다. 이름도 `test_prompt_revives_dead_drive` 로 바꾼다. `test_disabled_reports_without_launch` 는 `auto_up = true` → `auto_up = false` 치환 그대로 두고 `agent launch` 대신 `terminal create` 가 불리지 않았는지 본다.

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_up test_autoup -v 2>&1 | tail -8`
Expected: FAIL — `orch.drive` 없음, `setup.up` 이 `agent launch` 를 부름.

- [ ] **Step 4: 정체성 함수를 바꾼다**

`util.py`: `require_coordinator` 를 지우고 더한다(맨 위 docstring 의 "코디네이터 확인" 을 "관제 확인" 으로).

```python
def is_drive(p: Project) -> bool:
    """이 프로세스가 관제(orch drive) 터미널에서 도는가: ORCA_TERMINAL_HANDLE 이 run.json 의 drive_handle 이다."""
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    return bool(handle) and handle == load_run(p).get("drive_handle")


def require_drive(p: Project) -> None:
    """관제에서만 도는 명령을 보호한다. 메인이 Run 에 묶이는 사고를 막는다."""
    if not is_drive(p):
        raise OrchError("관제(orch drive) 터미널에서만 실행한다(ORCA_TERMINAL_HANDLE 이 run.json 의 drive_handle 과 다르다). "
                        "메인은 같은 orch 명령을 쓰면 관제 큐를 거친다", 2)
```

```bash
cd orca-team/lib/orch
grep -l require_coordinator *.py | xargs sed -i '' 's/require_coordinator/require_drive/g'
```

`tasks.new_task` 의 `require_drive(p)` 줄은 지운다(메인이 태스크 spec 을 만든다). `tasks.py` 의 `FIX_BLOCK` 의 "코디네이터 지시:" 는 Task 14 에서 바꾼다.

`comm.py`: `tell`, `wait`, `main_message_ids`, `remember_main_messages` 와 상수 `WAIT_TYPES`, `MAIN_SUBJECT`, `MAIN_IDS_KEEP` 를 지운다. 모듈 docstring 을 다음으로 바꾼다.

```python
"""메인 ⇄ 관제·기획·디자인 디렉터 워커 소통.

관제 → 메인: outbox.jsonl(파일). 메인은 Run 에 묶이지 않으므로 Orca 알림이 메인 입력란에 들어오지 않는다.
메인 → 관제: 요청 큐(queue.py). 기획·디자인 디렉터 워커 → 메인: outbox 의 ask 줄(orch ask-user).
메인 → 그 워커: state/answers/ 답 파일(orch answer). ask.py 참고.
"""
```

`paths.py` 에서 `self.wait_pid` 와 `self.main_msg_ids` 줄을 지운다.

`ask.py` 에서 `notify_question` 을 지우고 `DEFAULT_HEADER` 주석을 `# 그 밖의 직무` 로 바꾼다.

- [ ] **Step 5: `drive.py` 를 만든다**

```python
"""관제(orch drive). 관제 탭의 셸에서 돌며 Run 을 갖는다.

메인이 넣은 요청(queue.py)을 실행하고, Orca 메일·리뷰 자식 프로세스를 돌리며 정해진 절차를 실행한다. 판단이 필요한
일은 outbox decide 로 메인에 올린다(Task 11). 상태는 모두 파일에 있어 다시 띄우면 이어서 한다."""
from __future__ import annotations

import os
import time

from . import agents, orca, plan, queue, setup, tasks
from .config import Config
from .paths import OrchError, Project
from .util import acquire_pid, now, read_json, release_pid, update_run, write_text_atomic

WAIT_MS = 10000  # 메일을 한 번에 기다리는 시간. 메인 요청 큐도 이 간격으로 본다
IDLE_SECONDS = 1.0


class Drive:
    def __init__(self, p: Project, cfg: Config):
        self.p, self.cfg = p, cfg

    def log(self, text: str) -> None:
        line = f"{now()} {text}"
        print(line, flush=True)
        try:
            self.p.drive_dir.mkdir(parents=True, exist_ok=True)
            with self.p.drive_log.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def beat(self) -> None:
        write_text_atomic(self.p.drive_heartbeat, now())

    def bind(self) -> dict:
        return setup.bind(self.p)

    def run_queue(self) -> int:
        """메인 요청을 넣은 순서로 실행한다. 실행한 수."""
        done = 0
        for path in queue.pending(self.p):
            request = read_json(path, {})
            try:
                result = {"ok": True, "result": self.execute(request)}
            except OrchError as exc:
                if exc.code == 12:
                    raise
                result = {"ok": False, "error": str(exc), "code": exc.code}
            queue.complete(self.p, path, request, result)
            self.log(f"요청 {request.get('id')} {request.get('command')}: {'완료' if result['ok'] else result['error']}")
            done += 1
        return done

    def execute(self, request: dict) -> dict:
        command, a = request.get("command"), request.get("args") or {}
        p, cfg = self.p, self.cfg
        if command == "start":
            return tasks.start(p, cfg, a["id"], bool(a.get("retry")), a.get("overrides") or {})
        if command == "start-ready":
            return plan.start_ready(p, cfg)
        if command == "fix":
            return tasks.fix(p, cfg, a["id"], a.get("note"), bool(a.get("retry")), a.get("overrides") or {},
                             bool(a.get("force")), a.get("supplement"), bool(a.get("extra_round")))
        if command == "accept":
            return self.accepted(a["id"], tasks.accept(p, a["id"]))
        if command == "drop":
            return tasks.drop(p, a["id"], a["reason"], bool(a.get("force")))
        if command == "reply":
            orca.call(["orchestration", "reply", "--id", str(a["message_id"]), f"--body={a['body']}"])
            return {"replied": a["message_id"]}
        if command == "design-decide":
            return plan.design_decide(p, cfg, a["id"], list(a.get("approve") or []), dict(a.get("fixes") or {}))
        if command == "scope":
            return tasks.scope(p, a["id"], a["body"])
        if command == "agent-reset":
            return agents.request_reset(p, cfg, a["name"])
        raise OrchError(f"알 수 없는 관제 요청: {command}", 2)

    def accepted(self, task_id: str, result: dict) -> dict:
        """accept 뒤 처리. Task 11 에서 기획 적용·묶음 후속·디자인 흐름을 붙인다."""
        return result

    def tick(self, wait_ms: int = WAIT_MS) -> None:
        self.beat()
        self.run_queue()

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(IDLE_SECONDS)


def main(p: Project, cfg: Config) -> int:
    """관제 탭 셸에서 `orch drive` 로 띄운다. 하나만 돈다(state/drive/drive.pid)."""
    if os.environ.get("CLAUDECODE"):
        raise OrchError("orch drive 는 관제 탭 셸에서만 돈다. Claude 세션 안에서는 띄우지 않는다(메인은 /orca-team:up)", 2)
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        raise OrchError("ORCA_TERMINAL_HANDLE 이 없다. orch drive 는 Orca 터미널(관제 탭)에서 돈다", 2)
    p.ensure_state()
    p.drive_dir.mkdir(parents=True, exist_ok=True)
    acquire_pid(p.drive_pid, "orch drive")
    try:
        update_run(p, lambda r: (r.update(drive_handle=handle), r.pop("launched_at", None)))
        drive = Drive(p, cfg)
        drive.beat()
        drive.log(f"관제 시작({handle}): {drive.bind()['status']}")
        drive.run_forever()
    finally:
        release_pid(p.drive_pid)
    return 0
```

- [ ] **Step 6: `setup.py` 를 고친다**

맨 위 import 에 `import shlex` 를 더하고 `from . import agents, orca` 로 바꾸고, `from .util import ...` 에 `drive_alive` 를 더하고 `require_coordinator` 를 `require_drive` 로 바꾼다. `COORDINATOR_PROMPT`, `launch_handle`, `launch_coordinator` 와 `SCAFFOLD_FILES` 의 `coordinator.md` 줄은 그대로 두되(템플릿은 Task 14 에서 지운다) 다음을 바꾼다.

```python
DRIVE_TITLE = "orca-team 관제"
STARTING_MESSAGE = (f"관제가 아직 시작 중이다({LAUNCH_GRACE_SECONDS}초 안에 다시 띄우지 않는다). "
                    "`orch status` 로 확인한다.")


def drive_command(p: Project) -> str:
    return f"{shlex.quote(str(p.orch / 'bin' / 'orch'))} drive"


def up(p: Project, cfg: Config, wait_run: int = 0) -> dict:
    """관제가 살아 있으면 그대로 두고, 없으면 관제 탭을 띄운다(메인과 자동 부활이 호출)."""
    run = load_run(p)
    if drive_alive(p):
        update_run(p, lambda r: r.pop("launched_at", None))
        result = {"status": "running", "handle": run.get("drive_handle")}
    elif starting(run):
        result = {"status": "starting", "handle": run.get("drive_handle"), "message": STARTING_MESSAGE}
    else:
        result = launch_drive(p)
    deadline = time.monotonic() + wait_run
    while not load_run(p).get("run_id") and time.monotonic() < deadline:
        time.sleep(1)
    result["run_id"] = load_run(p).get("run_id")
    if wait_run and not result["run_id"]:
        raise OrchError(f"관제가 {wait_run}초 안에 Run 을 만들지 않았다. 관제 탭({DRIVE_TITLE})을 확인한다")
    return result


def launch_drive(p: Project) -> dict:
    """run.json 잠금 안에서 기동을 선점한 뒤 관제를 띄운다. 셸만 남은 옛 관제 터미널이 있으면 거기서 다시 실행하고,
    없으면 관제 탭을 새로 만든다. 선점에 지면 띄우지 않고 starting 을 돌려준다."""
    p.ensure_state()
    p.drive_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = p.drive_dir / "launch.json"
    launched_at = now()
    claimed = []

    def claim(r: dict) -> None:
        if starting(r):
            return
        r.update(launched_at=launched_at)
        claimed.append(True)

    run = update_run(p, claim)
    if not claimed:
        return {"status": "starting", "handle": run.get("drive_handle"), "message": STARTING_MESSAGE}
    old = run.get("drive_handle")
    if old and orca.shell_alive(old) == "alive":
        try:
            code, data = orca.call(["terminal", "send", "--terminal", old, "--text", drive_command(p), "--enter"],
                                   check=False)
        except OrchError as exc:
            if exc.code == 12:
                raise
            code, data = 1, {}
        if code == 0 and data.get("ok"):
            return {"status": "relaunched", "handle": old}
    try:
        code, receipt = orca.call(["terminal", "create", "--worktree", f"path:{p.root}", "--title", DRIVE_TITLE,
                                   "--command", drive_command(p)], check=False)
    except OrchError as exc:
        if exc.code != 12:
            write_json(receipt_path, {"ok": False, "parse_error": str(exc), "launched_at": launched_at})
        raise
    write_json(receipt_path, receipt)
    handle = orca.find_key(receipt, ("handle",))
    if code != 0 or not receipt.get("ok") or not handle:
        update_run(p, lambda r: r.pop("launched_at", None))
        raise OrchError(f"관제 탭을 만들지 못했다. 영수증: {receipt_path}")
    if handle == os.environ.get("ORCA_TERMINAL_HANDLE"):
        raise OrchError(f"관제 실행 결과가 불확실하다(영수증의 handle {handle} 이 메인 자신의 터미널이다). "
                        f"다시 띄우기 전에 영수증과 `orch status` 를 확인한다: {receipt_path}")
    update_run(p, lambda r: r.update(drive_handle=handle))
    return {"status": "launched", "handle": handle}
```

`COORDINATOR_PROMPT`, `launch_handle`, `launch_coordinator` 를 지운다. `status` 를 바꾼다.

```python
def status(p: Project, cfg: Config | None, config_error: str | None = None) -> dict:
    """현황. 태스크마다 다음 행동(next)을 붙인다. 관제가 살아 있으면 run.json 의 launched_at(기동 유예 표시)을 지운다.
    open_questions 는 메인이 아직 답하지 않은 질문이다(재시작한 메인은 orch reask 로 다시 보여 준다).

    config.toml 이 깨져도(cfg None) 복구 안내는 나와야 하므로 config_error 를 싣고 최대 라운드 검사 없이 next 를 만든다."""
    alive = drive_alive(p)
    run = update_run(p, lambda r: r.pop("launched_at", None)) if alive else load_run(p)
    return {"run_id": run.get("run_id"),
            "drive": {"handle": run.get("drive_handle"), "alive": alive, "starting": not alive and starting(run)},
            "agents": agents.summary(p, cfg) if cfg is not None else [],
            "watch_alive": pid_alive(p.watch_pid), "unread_outbox": len(unread(p)),
            "open_questions": open_questions(p),
            "setup_mode": p.setup_mode.exists(), "config_error": config_error,
            "tasks": [task_row(p, cfg, st) for st in all_states(p)]}
```

`UPGRADE_NEXT` 의 "코디네이터는 시작할 때만 지침을 읽으므로 커밋한 뒤 코디네이터 터미널을 닫고 메인에서 /orca-team:up 으로 다시 띄운다" 는 Task 15 에서 바꾼다.

- [ ] **Step 7: `autoup.py` 를 관제 기준으로 바꾼다**

모듈 docstring 첫 줄을 `"""관제 자동 부활. 메인 세션의 SessionStart·UserPromptSubmit 훅이 check() 를 부른다.` 로, 둘째 문단을 "확실히 죽은(pid 없음) 관제만 `orch up` 을 분리된 프로세스로 다시 띄운다. pid 는 살아 있는데 heartbeat 가 오래됐으면 띄우지 않고 메인에게 알린다. 요청은 큐에 넣기 전에 관제가 살아 있는지 확인하므로 메인은 부활을 기다린 뒤 다시 한다." 로 바꾼다. `from . import orca` 를 지우고 `from .util import drive_alive, load_run, now, pid_alive, read_json, write_json` 으로 바꾼다. 문구와 `decide` 를 바꾼다(`UNKNOWN`, `NO_HANDLE` 은 지운다).

```python
REVIVED = (PREFIX + "관제(orch drive)가 꺼져 있어 자동으로 다시 띄우는 중이다. 답변 첫 줄에 사용자에게 이 사실을 한 줄로 알린다. "
           "관제가 뜨기 전에 낸 orch 명령은 '관제가 꺼져 있다'로 거부되므로 잠시 뒤 다시 한다. "
           "메시지 watcher 가 꺼져 있으면 띄우고, 답하지 않은 질문이 있으면 orch reask 로 다시 보여 준다.")
HUNG = (PREFIX + "관제 프로세스는 살아 있지만 2분 넘게 응답이 없다. 답변 첫 줄에 사용자에게 한 줄로 알리고, "
        "관제 탭(orca-team 관제)의 출력을 확인하게 한다.")
DISABLED = (PREFIX + "관제가 꺼져 있다(config.toml 의 [drive] auto_up = false). 답변 첫 줄에 사용자에게 한 줄로 "
            "알리고 /orca-team:up 을 안내한다.")
FAILED = (PREFIX + "자동으로 다시 띄운 관제가 10분 안에 다시 꺼졌다(기록: .orchestration/state/auto-up.log). "
          "답변 첫 줄에 사용자에게 한 줄로 알리고, 원인을 확인한 뒤 /orca-team:up 을 실행하도록 안내한다. "
          "10분 동안은 자동으로 다시 띄우지 않는다.")
FAILED_SPAWN = (PREFIX + "관제를 자동으로 다시 띄우지 못했다(orch up 실행 실패). 답변 첫 줄에 사용자에게 한 줄로 알리고 "
                "/orca-team:up 을 안내한다.")
```

```python
def decide(p: Project) -> tuple[str | None, str | None]:
    """(action, context). action 은 "spawn" 또는 None, context 는 메인에게 넘길 문구 또는 None.
    파일은 쓰지 않고 Orca 도 부르지 않는다(관제 pid·heartbeat 로 판정한다)."""
    if not eligible(p):
        return None, None
    try:
        enabled = Config.load(p.config_path).auto_up
    except OrchError:
        return None, None
    if starting(load_run(p)) or spawn_running(p) or drive_alive(p):
        return None, None
    if pid_alive(p.drive_pid):
        return None, HUNG
    if within(check_record(p).get("spawned_at"), RETRY_WINDOW_SECONDS):
        return None, FAILED
    return ("spawn", REVIVED) if enabled else (None, DISABLED)
```

`check` 의 `context = UNKNOWN` 을 `context = FAILED_SPAWN` 으로 바꾼다. `spawn_up` docstring 의 "메인 자신의 터미널(ORCA_TERMINAL_HANDLE)을 코디네이터로 기록하지 않게" 를 "관제로 기록하지 않게" 로 바꾼다.

- [ ] **Step 8: CLI 를 고친다**

`cli.py`:
- `build_parser` 에서 `tell`, `wait` 하위 명령을 지우고, `notify` 의 `--json` 인자와 help 의 "코디네이터 → 메인" 을 "관제 → 메인" 으로 바꾼다. `up` help 를 `"관제 확인·기동(메인)"` 로, `bind` help 를 `"Run 생성·재연결(관제)"` 로 바꾼다. 더한다.

```python
    sub.add_parser("drive", help="관제 루프(관제 탭 셸에서만. 메인은 /orca-team:up)")
```

- 모듈 끝 상수 아래에 더한다.

```python
# 메인에서 실행하면 관제 큐를 거치는 명령(Orca 를 부른다). 관제 터미널에서는 바로 실행한다.
QUEUED = {"start", "start-ready", "fix", "accept", "drop", "design-decide", "scope", "agent"}
CONFIG_COMMANDS.update({"agent", "drive"})


def design_args(args) -> tuple[list[str], dict[str, str]]:
    fixes = {}
    for pair in args.fix:
        task_id, sep, note = pair.partition("=")
        if not sep:
            raise OrchError(f"--fix 는 T-xxx=<요청> 형식이다: {pair}", 2)
        fixes[task_id.strip()] = note.strip()
    return [t.strip() for t in args.approve.split(",") if t.strip()], fixes


def queued_request(p, cfg, args) -> tuple[str, dict]:
    """메인에서 실행한 명령을 관제 큐 요청으로 바꾼다. 형식·대상 검사는 여기서 바로 한다."""
    from . import tasks

    if args.cmd == "start":
        tasks.load_doc(p, cfg, args.id)
        return "start", {"id": args.id, "retry": args.retry, "overrides": overrides(args)}
    if args.cmd == "fix":
        tasks.load_doc(p, cfg, args.id)
        tasks.must_state(p, args.id)
        return "fix", {"id": args.id, "note": args.note, "retry": args.retry, "overrides": overrides(args),
                       "force": args.force, "supplement": args.supplement, "extra_round": args.extra_round}
    if args.cmd == "accept":
        tasks.must_state(p, args.id)
        return "accept", {"id": args.id}
    if args.cmd == "drop":
        tasks.must_state(p, args.id)
        return "drop", {"id": args.id, "reason": args.reason, "force": args.force}
    if args.cmd == "scope":
        tasks.must_state(p, args.id)
        return "scope", {"id": args.id, "body": args.body}
    if args.cmd == "design-decide":
        approve, fixes = design_args(args)
        return "design-decide", {"id": args.id, "approve": approve, "fixes": fixes}
    if args.cmd == "agent":
        if args.name not in cfg.agents:
            raise OrchError(f"고정 에이전트가 아니다: {args.name}. 가능한 이름: {', '.join(cfg.agents) or '없음'}", 2)
        return "agent-reset", {"name": args.name}
    return "start-ready", {}
```

- `run` 에서 `p, cfg = project(args.cmd in CONFIG_COMMANDS)` 바로 다음에 넣는다.

```python
    if args.cmd in QUEUED:
        from . import queue
        from .util import is_drive

        if not is_drive(p):
            command, payload = queued_request(p, cfg, args)
            emit(queue.submit(p, command, payload))
            return 0
    if args.cmd == "drive":
        from . import drive

        return drive.main(p, cfg)
```

- `run_graph` 의 design-decide 분기를 `approve, fixes = design_args(args)` 를 쓰도록 바꾼다.
- `elif args.cmd in ("tell", "watch", "notify", "wait"):` 블록을 `("watch", "notify")` 만 남기고, `tell`·`wait`·`notify --json` 처리와 `require_coordinator` import 를 지운다.

- [ ] **Step 9: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_up test_autoup test_hooks test_comm test_ask test_cli -v 2>&1 | tail -8`
Expected: OK

- [ ] **Step 10: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK. `grep -rn "require_coordinator\|coordinator_handle\|term_coord" orca-team/lib orca-team/hooks orca-team/tests` 가 비어야 한다.

```bash
git add -A orca-team/lib orca-team/hooks orca-team/tests
git commit -m "feat(orca-team): 코디네이터 대신 관제(orch drive)가 Run 을 갖고 메인 명령은 큐를 거친다"
```

---

### Task 11: 관제 이벤트 처리 — 메일, 리뷰, 판정, 재확인

**Files:**
- Modify: `orca-team/lib/orch/drive.py`
- Test: `orca-team/tests/test_drive.py` (새)

**Interfaces:**
- Consumes: `tasks.which`, `tasks.worker_check`, `tasks.recover_unknown_start`, `tasks.DIED_NOTE`, `tasks.fix`·`accept`, `agents.release_task`·`detach`·`reset_owner`·`finish_reset`·`begin_reset`·`forget_dead`, `comm.post_decide`, `plan.apply`·`start_ready`.
- Produces: `drive.Drive(p, cfg, spawn=None, clock=time.monotonic)`; 메서드 `check(wait_ms) -> list[dict]`, `handle_message(msg)`, `start_review(task_id)`, `poll_jobs()`, `review_done(task_id, code, out, err)`, `on_review(task_id, result)`, `request_changes(task_id, result)`, `accepted(task_id, result) -> dict`, `apply_plan(task_id)`, `batch_done(task_id)`(Task 12 가 후속을 붙인다), `design_step()`·`finish_step()`(Task 11 은 빈 함수, Task 12 가 채운다), `dep_fix(task_id, approved)`, `create_task(role, title, target, change, acceptance, owns=None, depends_on=(), front=None) -> str`, `decide(kind, task_id, summary, files=(), commands=(), extra=None) -> dict`, `reconcile()`, `check_worker(st)`, `resets()`, `start_ready()`. 상수 `CHECK_TYPES`, `RECONCILE_SECONDS = 60`, `UNKNOWN_GRACE_SECONDS = 60`, `LOCK_WAIT_TEXT = "테스트 잠금"`, `TEST_CONFIG_TEXTS`. 태스크 상태의 `decide`(out id), `decide_round`(그 decide 를 올린 라운드). `spawn(args)` 는 `poll()`·`communicate()`·`returncode` 를 갖는 프로세스 객체를 돌려준다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_drive.py`:

```python
import json
import os
import unittest
from unittest import mock

from helpers import FAKES
from test_tasks import TaskCase
from orch import agents, comm, drive, plan, tasks
from orch.config import Config
from orch.paths import OrchError, Project


class FakeProc:
    def __init__(self):
        self.returncode, self.out, self.err = None, "", ""

    def finish(self, code, out="", err=""):
        self.returncode, self.out, self.err = code, out, err

    def poll(self):
        return self.returncode

    def communicate(self):
        return self.out, self.err


class DriveCase(TaskCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir),
                                               "ORCA_TERMINAL_HANDLE": "term_drive",
                                               "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)
        self.spawned = []
        self.drive = drive.Drive(self.p, Config.load(self.root / ".orchestration/config.toml"), spawn=self.fake_spawn)

    def fake_spawn(self, args):
        proc = FakeProc()
        self.spawned.append((args, proc))
        return proc

    def started(self, task_id="T-001", role="developer", extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"[{task_id}] spec")
        self.orch("start", task_id)

    def outbox(self, kind=None):
        return [e for e in comm.read_outbox(self.p) if kind is None or e.get("type") == kind]

    def done_msg(self, outcome="succeeded", dispatch="ctx_1"):
        return {"id": "msg_1", "type": "worker_done", "body": "끝냈다",
                "payload": {"taskId": "task_1", "dispatchId": dispatch, "outcome": outcome}}

    def finish_review(self, verdict, task_id="T-001", status=None, **extra):
        """리뷰 자식 프로세스가 끝난 모양을 만든다(리뷰가 쓰는 상태도 맞춘다)."""
        self.set_state(task_id, status=status or ("approved" if verdict == "APPROVE" else "changes"))
        args, proc = self.spawned[-1]
        self.assertEqual(args, ["review", task_id])
        proc.finish(0, json.dumps({"id": task_id, "round": self.state(task_id)["round"], "verdict": verdict,
                                   "source": "codex", "review": f"docs/reviews/{task_id}/r1.md", **extra}))
        self.drive.poll_jobs()


class MessageTest(DriveCase):
    def test_worker_done_starts_review_then_approve_accepts(self):
        self.started()
        self.drive.handle_message(self.done_msg())
        self.finish_review("APPROVE")
        self.assertEqual(self.state("T-001")["status"], "done")

    def test_failed_worker_goes_to_main(self):
        self.started()
        self.drive.handle_message(self.done_msg(outcome="failed"))
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["task"], entry["files"]),
                         ("worker_failed", "T-001", ["docs/tasks/T-001/report.md"]))
        self.assertEqual(self.state("T-001")["decide"], entry["id"])
        self.assertEqual(self.spawned, [])

    def test_question_goes_to_main_with_message_id(self):
        self.started()
        self.drive.handle_message({"id": "msg_q", "type": "question", "body": "토큰 이름?",
                                   "payload": {"taskId": "task_1", "dispatchId": "ctx_1"}})
        [entry] = self.outbox("decide")
        self.assertEqual((entry["kind"], entry["message_id"], entry["question"]), ("worker_question", "msg_q", "토큰 이름?"))

    def test_check_passes_ack(self):
        self.respond_seq("orchestration check",
                         {"ok": True, "result": {"deliveryId": "d1", "messages": [self.done_msg()]}},
                         {"ok": True, "result": {"deliveryId": None, "messages": []}})
        self.assertEqual(len(self.drive.check(0)), 1)
        self.drive.check(0)
        call = self.calls_for("orchestration check")[-1]
        self.assertEqual(call[call.index("--ack") + 1], "d1")


class ReviewResultTest(DriveCase):
    def test_request_changes_fixes(self):
        self.started()
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES", untracked=["src/tmp.log", "other/x"])
        self.assertEqual((self.state("T-001")["status"], self.state("T-001")["round"]), ("working", 2))
        self.assertIn("src/tmp.log", self.state("T-001")["history"][-1]["note"])

    def test_max_rounds_goes_to_main_once(self):
        self.started()
        self.set_state("T-001", round=3)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        self.assertEqual([e["kind"] for e in self.outbox("decide")], ["max_rounds"])
        self.drive.reconcile()
        self.assertEqual(len(self.outbox("decide")), 1)  # 같은 라운드에 다시 묻지 않는다

    def test_repeated_orch_test_failure_goes_to_main(self):
        self.started()
        failed = {"stage": "review", "round": 1, "verdict": "REQUEST_CHANGES", "source": "orch", "tests": {"quick": "failed"}}
        self.set_state("T-001", history=self.state("T-001")["history"] + [failed, {**failed, "round": 2}], round=2)
        self.drive.start_review("T-001")
        self.finish_review("REQUEST_CHANGES")
        self.assertEqual(self.outbox("decide")[-1]["kind"], "repeat_test_failure")

    def test_review_errors(self):
        self.started()
        self.drive.start_review("T-001")
        self.spawned[-1][1].finish(2, "", "orch: 테스트 잠금(exclusive)을 600초 안에 얻지 못했다")
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide"), [])
        self.drive.reconcile()
        self.assertEqual(self.spawned[-1][0], ["review", "T-001"])
        self.spawned[-1][1].finish(2, "", "orch: `[test]` 검증 실패: quick 이 없다")
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide")[-1]["kind"], "test_config")

    def test_pending_dep_fix_after_approve(self):
        self.started("T-001", role="designer")
        self.set_state("T-001", pending_dep_fix="선행 디자인 T-000 가 바뀐다: 색. 바뀐 선행 디자인에 맞춘다")
        self.drive.start_review("T-001")
        self.finish_review("APPROVE")
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 2))
        self.assertIn("선행 디자인 T-000", st["history"][-1]["note"])
        self.assertNotIn("pending_dep_fix", st)


class AcceptFlowTest(DriveCase):
    def test_planner_accept_applies_plan(self):
        self.started("T-001", role="planner")
        with mock.patch.object(plan, "apply", return_value={"tasks": [
                {"id": "T-002", "role": "developer", "title": "폼", "depends_on": ["T-001"]}]}) as apply:
            self.drive.start_review("T-001")
            self.finish_review("APPROVE")
        apply.assert_called_once()
        self.assertFalse(apply.call_args.args[2])
        self.assertIn("T-002 [developer] 폼 ← T-001", self.outbox("progress")[-1]["body"])

    def test_plan_apply_error_makes_planner_task(self):
        self.started("T-001", role="planner", extra="plan: append\n")
        with mock.patch.object(plan, "apply", side_effect=OrchError("tasks.toml: key 중복", 2)):
            self.drive.start_review("T-001")
            self.finish_review("APPROVE")
        [new] = [st for st in tasks.all_states(self.p) if st["status"] == "pending"]
        text = (self.root / f".orchestration/tasks/{new['id']}.md").read_text(encoding="utf-8")
        self.assertIn("key 중복", text)
        self.assertIn("plan: append", text)
        self.assertEqual(new["role"], "planner")


class ReconcileTest(DriveCase):
    def show(self, stage, state):
        self.respond("orchestration worker-show", {"ok": True, "result": {
            "worker": {"agentTerminalHandle": "term_w1", "stage": stage, "state": state},
            "projection": {"liveness": {"verdict": "exited"}}}})

    def test_settled_worker_without_mail_is_reviewed(self):
        self.started()
        self.show("settled", "succeeded")
        self.drive.reconcile()
        self.assertEqual(self.spawned[-1][0], ["review", "T-001"])

    def test_dead_worker_is_sent_back(self):
        self.started()
        self.show("settled", "failed")
        self.drive.reconcile()
        st = self.state("T-001")
        self.assertEqual(st["round"], 2)
        self.assertIn("이전 작업자가 중간에 끝났다", st["history"][-1]["note"])

    def test_tick_reads_mail_and_queue(self):
        self.started()
        self.respond_seq("orchestration check", {"ok": True, "result": {"deliveryId": "d1", "messages": [self.done_msg()]}})
        self.respond("orchestration check", {"ok": True, "result": {"messages": []}})
        self.drive.tick(wait_ms=0)
        self.assertEqual(self.spawned[0][0], ["review", "T-001"])
        self.assertTrue((self.root / ".orchestration/state/drive/heartbeat").is_file())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_drive -v 2>&1 | tail -8`
Expected: FAIL — `Drive` 에 `spawn`·`handle_message`·`poll_jobs` 없음.

- [ ] **Step 3: `drive.py` 에 이벤트 처리를 더한다**

맨 위 import 를 다음으로 바꾼다.

```python
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from . import agents, comm, orca, plan, queue, setup, tasks, testgate
from .config import Config
from .globs import matches_any
from .paths import PLUGIN_ROOT, OrchError, Project
from .util import (acquire_pid, git_commit_paths, now, pid_alive, read_json, release_pid, update_run,
                   write_text_atomic)
```

상수를 더한다.

```python
ORCH_BIN = PLUGIN_ROOT / "bin" / "orch"
CHECK_TYPES = "worker_done,escalation,question,status"
RECONCILE_SECONDS = 60  # 메일 없이 끝난 일(죽은 워커, 끊긴 리뷰)을 찾는 간격
UNKNOWN_GRACE_SECONDS = 60  # start_unknown 이 이만큼 지나도 그대로면 복구한다
LOCK_WAIT_TEXT = "테스트 잠금"
TEST_CONFIG_TEXTS = ("`[test]` 명령을 실행할 수 없다", "`[test]` 검증 실패", "tasks.toml` 읽기 실패")
```

`Drive.__init__`·`tick` 을 바꾸고 메서드를 더한다(`log`, `beat`, `bind`, `run_queue`, `execute`, `run_forever` 는 Task 10 그대로).

```python
class Job:
    def __init__(self, kind: str, task_id: str | None, proc):
        self.kind, self.task_id, self.proc = kind, task_id, proc


class Drive:
    def __init__(self, p: Project, cfg: Config, spawn=None, clock=time.monotonic):
        self.p, self.cfg = p, cfg
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.ack: str | None = None
        self.jobs: dict[str, Job] = {}
        self.retry_reviews: set[str] = set()
        self.last_reconcile: float | None = None

    def _spawn(self, args: list[str]):
        """orch 하위 명령을 자식 프로세스로 띄운다. 환경(ORCA_TERMINAL_HANDLE=관제)을 그대로 물려준다."""
        return subprocess.Popen([sys.executable, str(ORCH_BIN), *args], cwd=self.p.root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    # --- 메일
    def check(self, wait_ms: int) -> list[dict]:
        args = ["orchestration", "check", "--wait", "--types", CHECK_TYPES, "--timeout-ms", str(wait_ms)]
        if self.ack:
            args += ["--ack", self.ack]
        try:
            code, data = orca.call(args, check=False, timeout=wait_ms / 1000 + 30)
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.log(f"메일 확인 실패: {exc}")
            return []
        if code != 0 or not data.get("ok"):
            self.log(f"메일 확인 실패(exit {code}): {json.dumps(data, ensure_ascii=False)[:300]}")
            return []
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        self.ack = result.get("deliveryId") or None
        return [m for m in result.get("messages") or [] if isinstance(m, dict)]

    def task_of(self, *refs) -> str | None:
        for ref in refs:
            if ref:
                try:
                    return tasks.which(self.p, str(ref))["id"]
                except OrchError:
                    continue
        return None

    @staticmethod
    def task_files(task_id: str | None) -> list[str]:
        return [f".orchestration/tasks/{task_id}.md", f"docs/tasks/{task_id}/report.md"] if task_id else []

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        dispatch, orca_task = payload.get("dispatchId"), payload.get("taskId")
        body = str(msg.get("body") or msg.get("subject") or "").strip()
        if kind == "worker_done":
            owner = agents.reset_owner(self.p, dispatch)
            if owner:
                out = agents.finish_reset(self.p, owner, payload.get("outcome") == "succeeded")
                self.log(f"{owner} 리셋: {out['status']}")
                if out["status"] == "handoff_failed":
                    self.decide("escalation", None, f"{owner} 에이전트가 인수인계를 쓰지 못했다: {body[:200]}",
                                commands=[f"orch agent reset {owner}"])
                return
            task_id = self.task_of(dispatch, orca_task)
            if not task_id:
                self.log(f"어느 태스크인지 모르는 worker_done: {dispatch}")
                return
            st = tasks.load_state(self.p, task_id) or {}
            if st.get("agent") in self.cfg.agents:
                agents.release_task(self.p, st["agent"], task_id)
            if payload.get("outcome") == "succeeded":
                self.start_review(task_id)
            else:
                self.decide("worker_failed", task_id, body[:300] or "워커가 실패를 보고했다",
                            files=[f"docs/tasks/{task_id}/report.md"],
                            commands=[f'orch fix {task_id} --note "<원인과 지시>"', f'orch drop {task_id} --reason "<이유>"'])
        elif kind == "question":
            task_id = self.task_of(dispatch, orca_task)
            self.decide("worker_question", task_id, body[:300], files=self.task_files(task_id),
                        commands=['orch answer <이 decide 의 id> "<답>"'],
                        extra={"question": body, "message_id": msg.get("id")})
        elif kind == "escalation":
            task_id = self.task_of(dispatch, orca_task)
            self.decide("escalation", task_id, body[:300] or "워커가 worker_done 없이 멈췄다", files=self.task_files(task_id))

    def decide(self, kind: str, task_id: str | None, summary: str, files=(), commands=(), extra=None) -> dict:
        entry = comm.post_decide(self.p, kind, task_id, summary, files, commands, extra)
        st = tasks.load_state(self.p, task_id) if task_id else None
        if st:
            st.update(decide=entry["id"], decide_round=st.get("round"))
            tasks.save_state(self.p, st)
        self.log(f"decide {entry['id']} {kind} {task_id or '-'}: {summary[:80]}")
        return entry

    def guard(self, fn, task_id: str | None):
        """정해진 절차가 거부되면 메인에 올린다. orca 없음(12)은 올린다."""
        try:
            return fn()
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.decide("escalation", task_id, f"관제 절차 실패: {exc}"[:300], files=self.task_files(task_id))
            return None

    # --- 리뷰
    def start_review(self, task_id: str) -> None:
        key = f"review:{task_id}"
        if key in self.jobs:
            return
        self.jobs[key] = Job("review", task_id, self.spawn(["review", task_id]))
        self.log(f"{task_id} 리뷰 시작")

    def poll_jobs(self) -> None:
        for key, job in list(self.jobs.items()):
            if job.proc.poll() is None:
                continue
            out, err = job.proc.communicate()
            del self.jobs[key]
            if job.kind == "review":
                self.review_done(job.task_id, job.proc.returncode, out or "", err or "")
            elif job.kind == "final":
                self.final_done(job.proc.returncode, out or "", err or "")

    def review_done(self, task_id: str, code: int, out: str, err: str) -> None:
        if code == 0:
            try:
                result = json.loads(out)
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get("verdict"):
                self.on_review(task_id, result)
                return
        if code == 3:  # 이미 도는 리뷰가 있다
            return
        if code == 2 and LOCK_WAIT_TEXT in err:
            self.retry_reviews.add(task_id)
            return
        if code == 2 and any(text in err for text in TEST_CONFIG_TEXTS):
            self.decide("test_config", task_id, err.strip()[-300:], files=["docs/planning/tasks.toml"],
                        commands=['orch task new planner "[test] 고치기"'])
            return
        self.decide("escalation", task_id, f"리뷰 실패(exit {code}): {err.strip()[-300:]}", files=self.task_files(task_id))

    def on_review(self, task_id: str, result: dict) -> None:
        st = tasks.load_state(self.p, task_id) or {}
        self.log(f"{task_id} r{result.get('round')} {result.get('verdict')}")
        if st.get("pending_dep_fix"):
            self.dep_fix(task_id, approved=result.get("verdict") == "APPROVE")
        elif result.get("verdict") == "APPROVE":
            self.guard(lambda: self.accepted(task_id, tasks.accept(self.p, task_id)), task_id)
        else:
            self.request_changes(task_id, result)
        self.start_ready()

    @staticmethod
    def repeated_test_failure(st: dict) -> bool:
        reviews = [h for h in st.get("history") or [] if h.get("stage") == "review"][-2:]
        return len(reviews) == 2 and all(
            h.get("source") == "orch" and "quick" in (h.get("tests") or {}) and h["tests"]["quick"] not in testgate.PASSED
            for h in reviews)

    def request_changes(self, task_id: str, result: dict) -> None:
        st = tasks.load_state(self.p, task_id) or {}
        review = result.get("review") or f"docs/reviews/{task_id}/r{st.get('round')}.md"
        stop = [f'orch drop {task_id} --reason "<이유>"']
        if self.repeated_test_failure(st):
            self.decide("repeat_test_failure", task_id, "같은 quick 테스트 실패로 orch 사전 검사가 두 라운드 연속 반려했다",
                        files=[review], commands=[f'orch fix {task_id} --note "<지시>"', *stop])
            return
        if int(st.get("round") or 1) >= self.cfg.max_rounds:
            self.decide("max_rounds", task_id, f"최대 {self.cfg.max_rounds} 라운드에도 승인되지 않았다", files=[review],
                        commands=[f"orch fix {task_id} --extra-round", *stop])
            return
        owned = [f for f in result.get("untracked") or [] if matches_any(f, st.get("owns") or [])]
        note = ("리뷰 중 소유 경로에 추적 안 된 파일이 생겼다. 커밋하거나 지운다: " + ", ".join(owned)) if owned else None
        self.guard(lambda: tasks.fix(self.p, self.cfg, task_id, note), task_id)

    def dep_fix(self, task_id: str, approved: bool) -> None:
        """디자인 승인 답에서 선행 디자인이 바뀐 태스크. 리뷰 결과를 그대로 받지 않고 선행 변경 note 로 고친다."""
        st = tasks.load_state(self.p, task_id) or {}
        note = st.pop("pending_dep_fix", None)
        tasks.save_state(self.p, st)
        if approved and self.guard(lambda: tasks.accept(self.p, task_id), task_id) is None:
            return
        self.guard(lambda: tasks.fix(self.p, self.cfg, task_id, note), task_id)

    # --- accept 뒤
    def accepted(self, task_id: str, result: dict) -> dict:
        st = tasks.load_state(self.p, task_id) or {}
        role, status = st.get("role"), result.get("status")
        if status == "done" and role == "planner":
            self.apply_plan(task_id)
        elif status == "done" and role == testgate.TESTER_ROLE:
            self.batch_done(task_id)
        elif status == "awaiting_user":
            self.design_step()
        if result.get("released") is False:
            comm.post(self.p, "progress", f"{task_id} 워커 해제 실패: {json.dumps(result.get('release_error'), ensure_ascii=False)[:200]}")
        return result

    def apply_plan(self, task_id: str) -> None:
        doc = tasks.load_doc(self.p, self.cfg, task_id)
        append = doc.front.get("plan") == "append"
        try:
            out = plan.apply(self.p, self.cfg, append)
        except OrchError as exc:
            if exc.code == 12:
                raise
            if "새 key 가 없다" in str(exc):
                comm.post(self.p, "progress", f"기획 적용: {task_id} 가 새 태스크를 더하지 않았다")
                return
            self.create_task("planner", "tasks.toml 고치기", "docs/planning/tasks.toml",
                             f"orch plan apply{' --append' if append else ''} 가 거부했다. 오류 원문:\n{exc}\n\n"
                             "기존 `docs/planning/` 을 읽고 이 오류만 고친다.",
                             "`orch plan check` 가 통과한다", front={"plan": "append"} if append else None)
            return
        lines = [f"{t['id']} [{t['role']}] {t['title']}" + (f" ← {', '.join(t['depends_on'])}" if t["depends_on"] else "")
                 for t in out["tasks"]]
        comm.post(self.p, "progress", "기획 적용: " + "; ".join(lines))

    def batch_done(self, task_id: str) -> None:
        """묶음 테스트(tester)가 끝났다. Task 12 에서 묶음 후속을 먼저 처리한다."""
        names = agents.mark_reset_pending(self.p, self.cfg)
        comm.post(self.p, "progress", f"묶음 완료: {task_id}" + (f". 리셋할 에이전트: {', '.join(names)}" if names else ""))

    def design_step(self) -> None:
        """디자인 승인 흐름(Task 12)."""

    def finish_step(self) -> None:
        """모두 끝났을 때(Task 12)."""

    def final_done(self, code: int, out: str, err: str) -> None:
        """마지막 전체 테스트 결과(Task 12)."""

    def create_task(self, role: str, title: str, target: str, change: str, acceptance: str,
                    owns: list[str] | None = None, depends_on=(), front: dict | None = None) -> str:
        """관제가 정해진 규칙으로 만드는 태스크(회귀 수정, 묶음 정리, 보강, 기획 수정). spec 을 경로 지정 커밋하고 pending 으로 둔다.
        acceptance 의 `{id}` 는 새 태스크 ID 로 바꾼다."""
        task_id = tasks.new_task(self.p, self.cfg, role, title)["id"]
        lines = [f"id: {task_id}", f"role: {role}", f"title: {title}"]
        if owns:
            lines.append("owns: " + ", ".join(owns))
        if depends_on:
            lines.append("depends_on: " + ", ".join(depends_on))
        lines += [f"{key}: {value}" for key, value in (front or {}).items()]
        path = tasks.task_path(self.p, task_id)
        path.write_text("---\n" + "\n".join(lines) + "\n---\n"
                        f"## Target\n{target}\n\n## Change\n{change}\n\n"
                        "## Constraints\n다른 태스크의 소유 경로와 이 태스크 범위 밖은 건드리지 않는다\n\n"
                        f"## Acceptance\n{acceptance.replace('{id}', task_id)}\n", encoding="utf-8")
        doc = tasks.load_doc(self.p, self.cfg, task_id)
        git_commit_paths(self.p.root, [str(path.relative_to(self.p.root))], f"[{task_id}] spec: {title}")
        tasks.save_state(self.p, {"id": task_id, "role": role, "title": title, "status": "pending", "stage": "work",
                                  "round": 1, "base": None, "owns": tasks.owns_of(doc, self.cfg),
                                  "depends_on": list(depends_on), "task_id": None, "dispatch_id": None,
                                  "history": [], "verdicts": {}})
        self.log(f"{task_id} 생성 [{role}] {title}")
        return task_id

    def start_ready(self) -> None:
        try:
            out = plan.start_ready(self.p, self.cfg)
        except OrchError as exc:
            if exc.code == 12:
                raise
            self.log(f"start-ready 실패: {exc}")
            return
        for row in [*out.get("fixes", []), *out["started"]]:
            if row.get("status") in ("launch_failed", "error"):
                self.decide("escalation", row["id"], f"작업자를 띄우지 못했다: {row.get('error')}"[:300],
                            files=self.task_files(row["id"]),
                            commands=["orch status", f'orch drop {row["id"]} --reason "<이유>"'])

    # --- 재확인
    @staticmethod
    def launch_age(st: dict) -> float:
        for entry in reversed(st.get("history") or []):
            if entry.get("dispatch_id") and entry.get("at"):
                try:
                    return (datetime.now().astimezone() - datetime.fromisoformat(entry["at"])).total_seconds()
                except ValueError:
                    break
        return float("inf")

    def check_worker(self, st: dict) -> None:
        task_id = st["id"]
        try:
            busy, desc, info = tasks.worker_check(st)
        except OrchError as exc:
            if exc.code == 12:
                raise
            return
        info = info or {}
        agent = st.get("agent") if st.get("agent") in self.cfg.agents else None
        if info.get("state") == "start_unknown":
            if self.launch_age(st) < UNKNOWN_GRACE_SECONDS:
                return
            out = self.guard(lambda: tasks.recover_unknown_start(self.p, self.cfg, task_id), task_id) or {}
            self.log(f"{task_id} start_unknown: {out.get('status')}")
            if out.get("status") == "escalate":
                self.decide("escalation", task_id, f"작업자 시작을 {out.get('count')}번 확인하지 못했다",
                            files=self.task_files(task_id))
            return
        if busy is not False:
            return
        if info.get("stage") == "settled" and info.get("state") == "succeeded":
            if agent:
                agents.release_task(self.p, agent, task_id)
            self.start_review(task_id)
            return
        if agent:
            agents.detach(self.p, agent, task_id)
        if int(st.get("round") or 1) >= self.cfg.max_rounds:
            self.decide("max_rounds", task_id, f"작업자가 worker_done 없이 끝났고 최대 라운드다({desc})",
                        files=self.task_files(task_id),
                        commands=[f"orch fix {task_id} --extra-round", f'orch drop {task_id} --reason "<이유>"'])
            return
        self.guard(lambda: tasks.fix(self.p, self.cfg, task_id, tasks.DIED_NOTE.format(task_id=task_id)), task_id)

    def reconcile(self) -> None:
        """메일 없이 끝난 일을 찾는다: 끊긴 리뷰, 잠금으로 미룬 리뷰, accept 전에 멈춘 승인, 죽은 워커."""
        self.last_reconcile = self.clock()
        agents.forget_dead(self.p, self.cfg)
        for task_id in sorted(self.retry_reviews):
            self.retry_reviews.discard(task_id)
            self.start_review(task_id)
        for st in tasks.all_states(self.p):
            task_id, status = st["id"], st.get("status")
            if f"review:{task_id}" in self.jobs or (st.get("decide") and st.get("decide_round") == st.get("round")):
                continue  # 리뷰 중이거나 이 라운드는 메인 판단을 기다린다
            if status == "reviewing" and not pid_alive(tasks.review_pid(self.p, task_id)):
                self.start_review(task_id)
            elif status == "approved":
                self.on_review(task_id, {"verdict": "APPROVE", "round": st.get("round")})
            elif status == "changes" and not st.get("queued_fix"):
                self.request_changes(task_id, {})
            elif status == "working":
                self.check_worker(st)
        self.start_ready()
        self.design_step()
        self.finish_step()

    def resets(self) -> None:
        for name in self.cfg.agents:
            out = agents.begin_reset(self.p, self.cfg, name)
            if not out:
                continue
            self.log(f"{name} 리셋 시작: {out['status']}")
            if out["status"] == "failed":
                self.decide("escalation", None, f"{name} 에이전트에 인수인계 태스크를 넣지 못했다",
                            commands=[f"orch agent reset {name}"])

    def tick(self, wait_ms: int = WAIT_MS) -> None:
        self.beat()
        for msg in self.check(wait_ms):
            self.handle_message(msg)
        if self.run_queue():
            self.start_ready()
        self.poll_jobs()
        self.resets()
        if self.last_reconcile is None or self.clock() - self.last_reconcile >= RECONCILE_SECONDS:
            self.reconcile()
```

`main` 은 `drive.run_forever()` 앞에 `drive.reconcile()` 을 한 번 부른다(다시 뜬 관제가 끊긴 일부터 잇는다). `run_forever` 의 `time.sleep(IDLE_SECONDS)` 는 그대로 둔다(메일 확인이 바로 실패할 때 바쁜 반복을 막는다).

- [ ] **Step 4: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_drive test_up -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/drive.py orca-team/tests/test_drive.py
git commit -m "feat(orca-team): 관제가 메일·리뷰 결과·끊긴 일을 처리하고 판단만 메인에 올린다"
```

---

### Task 12: 관제 자동 흐름 — 묶음 후속, 마무리, 디자인 승인

**Files:**
- Modify: `orca-team/lib/orch/drive.py` (`batch_done`, `make_followups`, `finish_step`, `final_done`, `report_done`, `design_step`, `supplement_step`, `collect_proposals`)
- Test: `orca-team/tests/test_drive_flows.py` (새)

**Interfaces:**
- Consumes: `followups.followups(p, task_id=None, rest=False) -> {"tasks", "regressions", "followups", "planning_gaps"}`(각 항목 `{"task", "round", "id", "text"}`), `testgate.load_config(p)`(없으면 `None`), `plan.design_review`, `plan.REVIEW_WAIT_STATUSES`, Task 11 의 `create_task`·`decide`·`guard`·`start_ready`.
- Produces: 관제 상태 파일 `state/drive/finish.json`(`{"rest_done": bool, "reported": [태스크 ID 목록], "final_failed": [태스크 ID 목록]}`), `state/drive/supplement.json`(`{"task": T-yyy, "from": {T-xxx: 라운드}}` 또는 `{"done": {...}}`). spec 첫 줄 형식 `출처: <T-xxx|rest|final> followups` 는 `followups.SOURCE_RE` 와 같다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_drive_flows.py`:

```python
import json
import unittest
from unittest import mock

from helpers import fake_test_commands, write_test_config
from test_drive import DriveCase
from orch import comm, followups, plan, tasks


def item(task, text, item_id=None, rnd=1):
    return {"task": task, "round": rnd, "id": item_id, "text": text}


class BatchFollowupsTest(DriveCase):
    def setUp(self):
        super().setUp()
        self.started("T-001", extra="owns: src/login/**\n")
        self.set_state("T-001", status="done")

    def test_regressions_cleanup_and_gaps(self):
        found = {"tasks": ["T-001", "T-002"],
                 "regressions": [item("T-002", "로그인 실패 테스트가 깨진다. 원인 T-001"), item("T-002", "원인 모름")],
                 "followups": [item("T-001", "변수 이름", "r1-F2")],
                 "planning_gaps": [item("T-001", "비밀번호 찾기 화면이 spec 에 없다")]}
        with mock.patch.object(followups, "followups", return_value=found):
            self.drive.batch_done("T-002")
            self.drive.batch_done("T-002")  # 다시 불러도 만들지 않는다
        created_tasks = [st for st in tasks.all_states(self.p) if st["id"] != "T-001"]  # start_ready 가 하나는 바로 띄운다
        self.assertEqual(len(created_tasks), 2)
        texts = {st["title"]: (self.root / f".orchestration/tasks/{st['id']}.md").read_text(encoding="utf-8") for st in created_tasks}
        regression = next(t for title, t in texts.items() if title.startswith("회귀 수정"))
        self.assertIn("## Change\n출처: T-002 followups\n로그인 실패", regression)
        self.assertIn("owns: src/login/**", regression)
        cleanup = texts["묶음 정리: T-002"]
        self.assertIn("- r1-F2: 변수 이름", cleanup)
        self.assertEqual(sorted(e["kind"] for e in self.outbox("decide")), ["planning_gaps", "unknown_regression"])
        self.assertIn("묶음 완료: T-002", self.outbox("progress")[-1]["body"])


class FinishTest(DriveCase):
    def all_done(self):
        self.started("T-001")
        self.set_state("T-001", status="done")

    def test_done_is_reported_once_without_test_config(self):
        self.all_done()
        with mock.patch.object(followups, "followups", return_value={"tasks": [], "regressions": [], "followups": [],
                                                                     "planning_gaps": []}):
            self.drive.finish_step()
            self.drive.finish_step()
        [done] = self.outbox("done")
        self.assertIn("T-001", done["body"])

    def test_final_test_runs_then_reports(self):
        self.all_done()
        quick, full = fake_test_commands(self)
        write_test_config(self.root, quick, full)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "test config")
        with mock.patch.object(followups, "followups", return_value={"tasks": [], "regressions": [], "followups": [],
                                                                     "planning_gaps": []}):
            self.drive.finish_step()
        args, proc = self.spawned[-1]
        self.assertEqual(args, ["test", "--full"])
        proc.finish(1, json.dumps({"status": "failed", "tail": "FAIL a", "log": "/tmp/x.log"}))
        self.drive.poll_jobs()
        self.assertEqual(self.outbox("decide")[-1]["kind"], "unknown_regression")
        self.drive.finish_step()
        self.assertEqual(len(self.spawned), 1)  # 같은 태스크 묶음으로는 다시 돌리지 않는다


class DesignFlowTest(DriveCase):
    def awaiting_screen(self, task_id, proposal="없음"):
        self.write_spec(task_id, role="designer", extra=f"owns: docs/design/{task_id}/**\n")
        report = self.root / f"docs/tasks/{task_id}/report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(f"## r1\n바꾼 파일\n\n### 시스템 추가 제안\n{proposal}\n", encoding="utf-8")
        (self.root / f".orchestration/state/tasks/{task_id}.json").write_text(json.dumps({
            "id": task_id, "role": "designer", "title": "화면", "status": "awaiting_user", "stage": "work", "round": 1,
            "base": self.git("rev-parse", "HEAD").strip(), "owns": [f"docs/design/{task_id}/**"], "depends_on": [],
            "agent": "temp", "task_id": "t", "dispatch_id": "d", "released": True, "history": [], "verdicts": {}}),
            encoding="utf-8")

    def test_no_proposals_asks_for_approval(self):
        self.awaiting_screen("T-001")
        with mock.patch.object(plan, "design_review", return_value={"id": "out-0001", "tasks": ["T-001"]}) as review:
            self.drive.design_step()
        review.assert_called_once()

    def test_proposals_make_supplement_then_fix_screens(self):
        self.awaiting_screen("T-001", proposal="강조 버튼 토큰이 필요하다(결제 화면)")
        with mock.patch.object(plan, "design_review") as review:
            self.drive.design_step()
        review.assert_not_called()
        [sup] = [st for st in tasks.all_states(self.p) if st.get("role") == "director"]
        spec = (self.root / f".orchestration/tasks/{sup['id']}.md").read_text(encoding="utf-8")
        self.assertIn("- T-001 r1: 강조 버튼 토큰이 필요하다", spec)
        self.assertIn("owns: docs/design/system/**, docs/design/direction.md", spec)
        self.set_state(sup["id"], status="done")
        with mock.patch.object(plan, "design_review") as review:
            self.drive.design_step()
        st = self.state("T-001")
        self.assertEqual((st["status"], st["history"][-1]["supplement"]), ("working", sup["id"]))
        self.assertEqual(self.outbox("decide")[-1]["kind"], "token_update")

    def test_recorded_proposal_is_not_collected_again(self):
        self.awaiting_screen("T-001", proposal="강조 버튼 토큰")
        (self.root / "docs/design").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/design/direction.md").write_text("- 강조 버튼(T-001 r1): 받아들임\n", encoding="utf-8")
        with mock.patch.object(plan, "design_review", return_value={"id": "out-0001", "tasks": ["T-001"]}) as review:
            self.drive.design_step()
        review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_drive_flows -v 2>&1 | tail -8`
Expected: FAIL — 후속 태스크를 만들지 않고 done·승인 질문을 올리지 않는다.

- [ ] **Step 3: `drive.py` 에 흐름을 더한다**

import 에 `import re` 와 `from . import followups` 를, `from .util import ...` 에 `git`, `write_json` 을 더한다. Task 11 의 `batch_done`, `design_step`, `finish_step`, `final_done` 빈 함수를 다음으로 바꾸고 나머지를 더한다.

```python
    # --- 묶음 후속
    def already_made(self, source_line: str) -> bool:
        """이 출처 줄로 만든 태스크가 이미 있는가(다시 뜬 관제가 같은 태스크를 또 만들지 않게)."""
        return any(source_line in path.read_text(encoding="utf-8") for path in self.p.tasks_dir.glob("T-*.md"))

    def batch_done(self, task_id: str) -> None:
        """묶음 테스트(tester)가 끝났다: 후속 태스크를 만들고, 고정 에이전트 리셋을 표시하고, 메인에 알린다."""
        source = f"출처: {task_id} followups"
        if not self.already_made(source):
            found = followups.followups(self.p, task_id)
            self.make_followups(found, source, task_id)
        names = agents.mark_reset_pending(self.p, self.cfg)
        comm.post(self.p, "progress", f"묶음 완료: {task_id}" + (f". 리셋할 에이전트: {', '.join(names)}" if names else ""))

    def make_followups(self, found: dict, source: str, origin: str) -> None:
        """회귀는 원인 태스크마다 회귀 수정 태스크, P2·P3 는 정리 태스크 하나, 기획 누락과 원인 모를 회귀는 메인에 올린다."""
        members = set(found.get("tasks") or [])
        for entry in found.get("regressions") or []:
            causes = sorted({t for t in re.findall(r"T-\d{3,}", entry["text"]) if t in members and t != entry["task"]})
            if len(causes) != 1:
                self.decide("unknown_regression", origin, entry["text"][:300],
                            files=[f"docs/reviews/{entry['task']}/r{entry['round']}.md"],
                            commands=['orch task new developer "회귀 수정: <요약>"'],
                            extra={"source": source})
                continue
            owns = (tasks.load_state(self.p, causes[0]) or {}).get("owns") or None
            self.create_task("developer", f"회귀 수정: {entry['text'][:40]}", ", ".join(owns or []) or "원인 태스크의 소유 경로",
                             f"{source}\n{entry['text']}",
                             f"이 항목의 실패 테스트가 `orch test {{id}}` 에서 통과한다: {entry['text']}", owns=owns)
        items = found.get("followups") or []
        if items:
            owns = sorted({o for e in items for o in (tasks.load_state(self.p, e["task"]) or {}).get("owns") or []})
            lines = "\n".join(f"- {e['id'] or '-'}: {e['text']}" for e in items)
            self.create_task("developer", f"묶음 정리: {origin}", ", ".join(owns) or "항목이 가리키는 파일",
                             f"{source}\n{lines}", "항목마다 고쳤거나 고치지 않은 이유를 리포트에 적었다", owns=owns or None)
        gaps = found.get("planning_gaps") or []
        if gaps:
            self.decide("planning_gaps", origin, f"기획 누락 {len(gaps)}건: " + "; ".join(g["text"][:60] for g in gaps),
                        files=sorted({f"docs/reviews/{g['task']}/r{g['round']}.md" for g in gaps}),
                        commands=['orch scope T-xxx "<추가 범위>"', 'orch task new planner "<증분 기획>"'],
                        extra={"items": gaps, "source": source})
        if any(st.get("status") == "pending" for st in tasks.all_states(self.p)):
            self.start_ready()

    # --- 마무리
    def finish_state(self) -> dict:
        data = read_json(self.p.drive_dir / "finish.json", {})
        return data if isinstance(data, dict) else {}

    def save_finish(self, **fields) -> None:
        write_json(self.p.drive_dir / "finish.json", {**self.finish_state(), **fields})

    def finish_step(self) -> None:
        """모든 태스크가 done·dropped 면: 묶음 밖 후속(한 번만) → [test] 가 있으면 전체 테스트 → done 보고(태스크 묶음마다 한 번)."""
        states = tasks.all_states(self.p)
        if not states or any(st.get("status") not in tasks.FINISHED for st in states) or "final" in self.jobs:
            return
        ids = sorted(st["id"] for st in states)
        record = self.finish_state()
        if record.get("reported") == ids or record.get("final_failed") == ids:
            return
        if not record.get("rest_done") and not self.already_made("출처: rest followups"):
            self.save_finish(rest_done=True)
            self.make_followups(followups.followups(self.p, rest=True), "출처: rest followups", "rest")
            if any(st.get("status") == "pending" for st in tasks.all_states(self.p)):
                return
        try:
            has_test = testgate.load_config(self.p) is not None
        except OrchError:
            has_test = True  # 형식 오류는 전체 테스트가 알린다
        if has_test:
            self.jobs["final"] = Job("final", None, self.spawn(["test", "--full"]))
            self.log("마지막 전체 테스트 시작")
            return
        self.report_done(ids)

    def final_done(self, code: int, out: str, err: str) -> None:
        ids = sorted(st["id"] for st in tasks.all_states(self.p))
        try:
            data = json.loads(out) if out.strip() else {}
        except ValueError:
            data = {}
        if code == 0:
            self.report_done(ids, data)
        elif code == 1:
            self.save_finish(final_failed=ids)
            self.decide("unknown_regression", None, "마지막 전체 테스트가 실패했다. 원인 태스크를 정해 회귀 수정 태스크를 만든다",
                        files=[data["log"]] if data.get("log") else [],
                        commands=['orch task new developer "회귀 수정: <요약>"'],
                        extra={"tail": data.get("tail"), "source": "출처: final followups"})
        elif code == 2 and LOCK_WAIT_TEXT in err:
            self.log("전체 테스트 잠금 대기 초과. 다음 확인 때 다시 한다")
        else:
            self.save_finish(final_failed=ids)
            self.decide("test_config", None, f"전체 테스트를 돌리지 못했다(exit {code}): {err.strip()[-300:]}",
                        files=["docs/planning/tasks.toml"], commands=['orch task new planner "[test] 고치기"'])

    def report_done(self, ids: list[str], final: dict | None = None) -> None:
        states = tasks.all_states(self.p)
        rows = [f"- {st['id']} [{st.get('role')}] {st.get('title')}: {st.get('status')}, "
                f"r{st.get('round')} {(st.get('verdicts') or {}).get(str(st.get('round')), '-')}" for st in states]
        bases = [st["base"] for st in states if st.get("base")]
        changed = git(self.p.root, "diff", "--name-only", f"{bases[0]}..HEAD", check=False).split() if bases else []
        body = ["모든 태스크가 끝났다.", *rows]
        if changed:
            body.append("바뀐 주요 파일: " + ", ".join(changed[:15]) + (" …" if len(changed) > 15 else ""))
        if final:
            body.append(f"마지막 전체 테스트: {final.get('status')} ({final.get('seconds')}초)")
        comm.post(self.p, "done", "\n".join(body))
        self.save_finish(reported=ids)

    # --- 디자인 승인
    def design_step(self) -> None:
        """승인 대상 태스크가 모두 리뷰를 지나 awaiting_user 면: 시스템 추가 제안이 있으면 보강부터, 없으면 승인 질문."""
        roles = self.cfg.user_approval_roles
        gated = [st for st in tasks.all_states(self.p) if st.get("role") in roles]
        if not any(st.get("status") == "awaiting_user" for st in gated):
            return
        if any(st.get("status") in plan.REVIEW_WAIT_STATUSES for st in gated):
            return
        if self.supplement_step(gated):
            return
        try:
            out = plan.design_review(self.p, self.cfg)
        except OrchError as exc:
            if exc.code in (2, 3):  # 아직 기다리거나 이미 물었다
                return
            raise
        self.log(f"디자인 승인 질문 {out['id']}: {', '.join(out['tasks'])}")

    def collect_proposals(self, gated: list[dict]) -> dict[str, tuple[int, str]]:
        """awaiting_user 화면 디자이너 리포트의 마지막 `## r{n}` 절에서 `### 시스템 추가 제안`. "없음" 과 이미
        docs/design/direction.md 에 출처(T-xxx rN)가 기록된 것은 뺀다."""
        direction = self.p.root / "docs/design/direction.md"
        recorded = direction.read_text(encoding="utf-8") if direction.is_file() else ""
        found = {}
        for st in gated:
            if st.get("status") != "awaiting_user" or st.get("role") != "designer":
                continue
            report = self.p.root / f"docs/tasks/{st['id']}/report.md"
            text = report.read_text(encoding="utf-8") if report.is_file() else ""
            rounds = list(re.finditer(r"^## r(\d+)\s*$", text, re.M))
            if not rounds:
                continue
            last = rounds[-1]
            section = text[last.end():]
            match = re.search(r"^### 시스템 추가 제안[ \t]*\n(.*?)(?=^##+ |\Z)", section, re.M | re.S)
            proposal = match.group(1).strip() if match else ""
            rnd = int(last.group(1))
            if not proposal or followups.is_empty(proposal) or f"{st['id']} r{rnd}" in recorded:
                continue
            found[st["id"]] = (rnd, proposal)
        return found

    def supplement_step(self, gated: list[dict]) -> bool:
        """보강 태스크를 만들거나 기다리거나 마무리했으면 True(승인 질문을 아직 하지 않는다)."""
        path = self.p.drive_dir / "supplement.json"
        current = read_json(path, {})
        current = current if isinstance(current, dict) else {}
        if current.get("task"):
            sup = tasks.load_state(self.p, current["task"]) or {}
            if sup.get("status") == "done":
                for task_id in current.get("from") or {}:
                    self.guard(lambda t=task_id: tasks.fix(self.p, self.cfg, t, f"디자인 시스템 보강({current['task']})을 반영한다",
                                                           supplement=current["task"]), task_id)
                self.decide("token_update", current["task"],
                            f"디자인 시스템 보강 {current['task']} 이 끝났다. UI 기반 개발 태스크에 바뀐 토큰을 반영할지 정한다",
                            files=["docs/design/direction.md", f"docs/tasks/{current['task']}/report.md"],
                            commands=['orch task new developer "토큰 갱신 반영: <요약>"', 'orch scope T-xxx "<바뀐 토큰>"'])
                write_json(path, {"done": current})
                self.start_ready()
                return True
            if sup.get("status") not in tasks.FINISHED:
                return True  # 보강 진행 중
            write_json(path, {"dropped": current})
        proposals = self.collect_proposals(gated)
        if not proposals:
            return False
        lines = "\n".join(f"- {t} r{rnd}: {text}" for t, (rnd, text) in proposals.items())
        new = self.create_task("director", f"디자인 시스템 보강: {', '.join(proposals)} 제안",
                               "docs/design/system/, docs/design/direction.md",
                               "화면 디자이너의 시스템 추가 제안이다. 제안마다 받아들일지 정해 디자인 시스템에 더하고, 결정과 "
                               "이유를 출처(태스크 ID·라운드)와 함께 `docs/design/direction.md` 에 적는다.\n" + lines,
                               "제안마다 반영 여부와 이유가 출처와 함께 `docs/design/direction.md` 에 있다",
                               owns=["docs/design/system/**", "docs/design/direction.md"])
        write_json(path, {"task": new, "from": {t: rnd for t, (rnd, _) in proposals.items()}})
        self.start_ready()
        return True
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_drive_flows test_drive -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/drive.py orca-team/tests/test_drive_flows.py
git commit -m "feat(orca-team): 관제가 묶음 후속·마무리·디자인 보강과 승인 질문을 자동으로 이어 간다"
```

---

### Task 13: 리뷰 보기 pane `orch review-view`

**Files:**
- Create: `orca-team/lib/orch/reviewview.py`
- Modify: `orca-team/lib/orch/cli.py`
- Test: `orca-team/tests/test_reviewview.py` (새)

**Interfaces:**
- Consumes: 태스크 상태의 `agent`·`status`·`round`·`history`(리뷰 entry 의 `verdict`), 리뷰 run-dir(`review.run_dir_for` 가 만드는 `<캐시>/orca-team/runs/<프로젝트>/<T-id>-r<N>-<시각>-a<시도>/progress.log`).
- Produces: `reviewview.Viewer(p, cfg, name, out=sys.stdout)` 의 `step()`, `loop()`; `reviewview.main(p, cfg, name) -> int`; `orch review-view <에이전트>`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_reviewview.py`:

```python
import io
import json
import os
import unittest
from unittest import mock

from helpers import ProjectCase
from orch import reviewview
from orch.config import Config
from orch.paths import OrchError, Project


class ViewerTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold(agents=True)
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)
        self.cfg = Config.load(self.root / ".orchestration/config.toml")
        self.out = io.StringIO()
        self.viewer = reviewview.Viewer(self.p, self.cfg, "production", out=self.out)

    def state(self, **fields):
        path = self.root / ".orchestration/state/tasks/T-001.json"
        base = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "id": "T-001", "role": "developer", "title": "로그인", "round": 2, "agent": "production", "history": []}
        base.update(fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(base), encoding="utf-8")

    def run_dir(self):
        path = self.tmp / "cache/orca-team/runs" / self.root.name / "T-001-r2-20261109-120000-a1"
        path.mkdir(parents=True)
        return path

    def test_shows_running_review_then_verdict(self):
        self.state(status="reviewing")
        run = self.run_dir()
        (run / "progress.log").write_text("파일 읽는 중\n", encoding="utf-8")
        self.viewer.step()
        text = self.out.getvalue()
        self.assertIn("T-001 r2 리뷰 중 (gpt-6.1-sol, xhigh)", text)
        self.assertIn("파일 읽는 중", text)
        with (run / "progress.log").open("a", encoding="utf-8") as fh:
            fh.write("판정 쓰는 중\n")
        self.viewer.step()
        self.assertEqual(self.out.getvalue().count("파일 읽는 중"), 1)
        self.assertIn("판정 쓰는 중", self.out.getvalue())
        self.state(status="approved", history=[{"stage": "review", "round": 2, "verdict": "APPROVE"}])
        self.viewer.step()
        self.assertIn("T-001 r2 APPROVE — docs/reviews/T-001/r2.md", self.out.getvalue())

    def test_ignores_other_agents_and_unknown_name(self):
        self.state(status="reviewing", agent="temp")
        self.viewer.step()
        self.assertEqual(self.out.getvalue(), "")
        with self.assertRaises(OrchError):
            reviewview.main(self.p, self.cfg, "nobody")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_reviewview -v 2>&1 | tail -5`
Expected: FAIL — `No module named 'orch.reviewview'`.

- [ ] **Step 3: `reviewview.py` 를 만든다**

```python
"""orch review-view <에이전트>: 리뷰 보기 pane. 그 고정 에이전트가 맡은 태스크의 Codex 리뷰 진행을 보여 주는 읽기 전용 창.

리뷰 실행은 관제의 orch review 자식 프로세스가 한다. 이 창이 죽어도 리뷰에는 영향이 없다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .config import Config
from .paths import OrchError, Project
from .tasks import all_states, load_state

POLL_SECONDS = 2.0


def runs_dir(p: Project) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "orca-team" / "runs" / p.root.name


def latest_run(p: Project, task_id: str, rnd: int) -> Path | None:
    found = sorted(runs_dir(p).glob(f"{task_id}-r{rnd}-*")) if runs_dir(p).is_dir() else []
    return found[-1] if found else None


class Viewer:
    def __init__(self, p: Project, cfg: Config, name: str, out=sys.stdout):
        self.p, self.cfg, self.name, self.out = p, cfg, name, out
        self.seen: tuple[str, int] | None = None
        self.run: Path | None = None
        self.offset = 0

    def write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def current(self) -> dict | None:
        for st in all_states(self.p):
            if st.get("agent") == self.name and st.get("status") == "reviewing":
                return st
        return None

    def step(self) -> None:
        st = self.current()
        if self.seen and (st is None or (st["id"], int(st.get("round") or 1)) != self.seen):
            self.finish()
        if st is None:
            return
        key = (st["id"], int(st.get("round") or 1))
        if key != self.seen:
            launch = self.cfg.codex(st["role"])
            self.write(f"\n{key[0]} r{key[1]} 리뷰 중 ({launch.model}, {launch.effort})\n")
            self.seen, self.run, self.offset = key, None, 0
        run = latest_run(self.p, *key)
        if run != self.run:
            self.run, self.offset = run, 0
        log = run / "progress.log" if run else None
        if log and log.is_file():
            with log.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
            if chunk:
                self.write(chunk)

    def finish(self) -> None:
        task_id, rnd = self.seen
        st = load_state(self.p, task_id) or {}
        verdict = next((h.get("verdict") for h in reversed(st.get("history") or [])
                        if h.get("stage") == "review" and h.get("round") == rnd), None)
        self.write(f"{task_id} r{rnd} {verdict or '리뷰 끝(판정 없음)'} — docs/reviews/{task_id}/r{rnd}.md\n")
        self.seen, self.run, self.offset = None, None, 0

    def loop(self) -> None:
        agent = self.cfg.agents[self.name]
        self.write(f"리뷰 보기: {agent.title} 에이전트. 리뷰가 시작되면 Codex 진행 기록을 보여 준다.\n")
        while True:
            self.step()
            time.sleep(POLL_SECONDS)


def main(p: Project, cfg: Config, name: str) -> int:
    if name not in cfg.agents:
        raise OrchError(f"고정 에이전트가 아니다: {name}. 가능한 이름: {', '.join(cfg.agents) or '없음'}", 2)
    Viewer(p, cfg, name).loop()
    return 0
```

- [ ] **Step 4: CLI 를 더한다**

`cli.py` `build_parser` 의 `drive` 줄 다음에 `sub.add_parser("review-view", help="리뷰 보기 pane(고정 에이전트 탭 오른쪽)").add_argument("name")` 를 더하고, `CONFIG_COMMANDS.update(...)` 에 `"review-view"` 를 더하고, `run` 의 `drive` 분기 다음에 더한다.

```python
    if args.cmd == "review-view":
        from . import reviewview

        return reviewview.main(p, cfg, args.name)
```

- [ ] **Step 5: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_reviewview -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 6: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/reviewview.py orca-team/lib/orch/cli.py orca-team/tests/test_reviewview.py
git commit -m "feat(orca-team): 고정 에이전트 탭 오른쪽에 리뷰 진행을 보여 주는 review-view 를 더한다"
```

---

### Task 14: 메인 규칙·직무 문서·스킬

**Files:**
- Modify: `orca-team/templates/CLAUDE.section.md`
- Delete: `orca-team/templates/coordinator.md`
- Modify: `orca-team/lib/orch/setup.py` (`SCAFFOLD_FILES`)
- Modify: `orca-team/templates/worker-spec.md`, `templates/roles/{planner,director,designer,developer,tester}.md`
- Modify: `orca-team/lib/orch/tasks.py` (`FIX_BLOCK`, `REVIEW_LINE`, `TESTER_TEST_BLOCK`), `lib/orch/review.py`, `lib/orch/followups.py`, `lib/orch/ask.py` (문구)
- Modify: `orca-team/skills/up/SKILL.md`, `orca-team/skills/init/SKILL.md`
- Test: `orca-team/tests/test_templates.py`, `orca-team/tests/test_skills.py`

**Interfaces:**
- Consumes: decide kind 와 큐 명령 이름(Global Constraints).
- Produces: 메인 규칙의 "판단 기준" 표. 템플릿·스킬·`lib/orch` 문구에 "코디네이터" 가 없다.

- [ ] **Step 1: 테스트를 고친다**

`test_templates.py`:
- `coordinator.md` 를 읽는 테스트를 모두 지운다(`grep -n "coordinator.md" orca-team/tests/test_templates.py` 로 찾는다: `test_coordinator_rules`, `test_coordinator_relays_untracked_and_release_errors`, `test_coordinator_restart_follows_status_next`, `test_coordinator_questions_use_structured_json` 과 다른 테스트 안의 `coordinator = (T / "coordinator.md")…` 줄과 그 줄을 쓰는 검사).
- 더한다.

```python
    def test_main_rules_cover_decide_and_queue(self):
        text = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for kind in ("worker_failed", "worker_question", "escalation", "max_rounds", "repeat_test_failure",
                     "test_config", "planning_gaps", "unknown_regression", "token_update"):
            self.assertIn(f"| {kind} |", text)
        for key in ("orch design-decide", "orch scope", "--extra-round", "plan: append", "관제가 꺼져 있다", "result"):
            self.assertIn(key, text)
        self.assertNotIn("orch tell", text)
        self.assertNotIn("coordinator", text)

    def test_no_coordinator_left_in_templates_and_code(self):
        self.assertFalse((T / "coordinator.md").exists())
        roots = [T, T.parent / "skills", T.parent / "lib" / "orch"]
        legacy = {"config.py", "setup.py"}  # 옛 [coordinator] 설정을 옮기라는 안내와 upgrade 거부 문구만 남긴다
        hits = [str(f) for root in roots for f in root.rglob("*") if f.is_file() and f.suffix in (".md", ".py")
                and f.name not in legacy and "코디네이터" in f.read_text(encoding="utf-8")]
        self.assertEqual(hits, [])
```

`test_skills.py` 의 파일 목록에서 `PLUGIN / "templates/coordinator.md",` 를 지운다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_skills -v 2>&1 | tail -8`
Expected: FAIL — CLAUDE.section 에 decide 표가 없고, 템플릿에 "코디네이터" 가 남아 있다.

- [ ] **Step 3: `CLAUDE.section.md` 를 바꾼다**

맨 위부터 `### 질문 표시 규칙` 바로 앞까지를 다음으로 바꾼다. `### 질문 표시 규칙` 절은 그대로 둔다.

```markdown
<!-- orca-team -->
## orca-team 오케스트레이션

이 저장소는 orca-team 플러그인으로 운영한다. 세션을 시작하면 역할부터 판별한다.

| 첫 프롬프트 | 역할 | 따를 문서 |
|---|---|---|
| Orca 디스패치 프리앰블(Task ID·Dispatch ID) | 워커(고정 에이전트 또는 임시 워커) | 프리앰블의 spec |
| 그 외 | 메인 | 아래 "메인 규칙" |

관제(`orch drive`)는 관제 탭의 셸에서 도는 프로그램이다. 완료 대기, 리뷰, accept·fix, 다음 태스크 시작, 묶음 후속, 세션 리셋, 복구를 정해진 절차대로 하고 판단이 필요한 일만 decide 로 메인에 올린다.

### 메인 규칙
- 사용자와 대화하고 판단한다. 기획·디자인·코드·테스트 산출물을 직접 만들지 않는다(훅이 막는다). 서브에이전트·Workflow 로 대신 시키지도 않는다.
- 사용자 요청은 기획 태스크로 맡긴다.
  1. `.orchestration/bin/orch task new planner "<요청 요약>"`.
  2. `.orchestration/tasks/T-xxx.md` 의 Target·Change·Constraints·Acceptance 를 채운다. Change 에 사용자 요청 원문을 넣는다. `docs/planning/tasks.toml` 이 이미 있고 사용자가 추가 요청만 했다면 front matter 에 `plan: append` 를 넣고 Change 에 "기존 `docs/planning/` 을 읽고 추가 요청만 인터뷰한다. tasks.toml 의 기존 태스크는 그대로 두고 새 `[[task]]` 만 덧붙인다"를 적는다. `<...>` 자리표시가 남으면 start 가 거부한다.
  3. `git add -- .orchestration/tasks/T-xxx.md && git commit -m "[T-xxx] spec: <제목>" -- .orchestration/tasks/T-xxx.md`. 워커와 인덱스를 같이 쓰므로 경로 없는 `git commit` 은 쓰지 않는다.
  4. `.orchestration/bin/orch start T-xxx` 하고 넘겼다고 한 줄로 알린다. 기획 워커는 `orch ask-user` 로 메인을 거쳐 사용자를 인터뷰한다.
- 진행 중 태스크에 범위를 더할 때: spec 에 "추가 범위"를 적어 위와 같이 경로 지정 커밋한 뒤, 태스크가 working 이면 `.orchestration/bin/orch scope T-xxx "<내용>"`. 시작 전(pending)이면 커밋으로 끝이다.
- Orca 를 부르는 `orch` 명령(`start`, `start-ready`, `fix`, `accept`, `drop`, `design-decide`, `scope`, `agent reset`)은 관제 큐를 거친다. "관제가 꺼져 있다" 면 `/orca-team:up` 뒤에 다시 한다. "관제가 처리 중" 이면 결과는 watch 의 `result` 메시지로 온다.
- `orca orchestration` 을 직접 호출하지 않고 `orch drive` 를 메인에서 실행하지 않는다. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 들어온다.
- 관제·기획·디자인 디렉터 워커 메시지는 `.orchestration/bin/orch watch` 로 받는다. 항상 Bash `run_in_background: true` 로 하나 띄워 둔다. 끝나면(메시지 도착) 출력의 메시지를 처리하고 바로 다시 띄운다.
  - progress: 한두 줄로 전한다.
  - ask(기획·디자인 디렉터 워커의 질문): (1) `render.context` 가 있으면 그 내용을 평문으로 그대로 출력한다. (2) `render` 로 AskUserQuestion 을 부른다(아래 "질문 표시 규칙"). (3) 답을 `.orchestration/bin/orch answer <id> "<답>"` 으로 돌려준다. `-` 로 시작하는 답은 `.orchestration/bin/orch answer <id> -- "<답>"`.
  - approval(디자인 일괄 승인): ask 와 같이 보여 주고 묻는다. 답을 화면별 결정으로 정리해 `.orchestration/bin/orch design-decide <id> --approve T-001,T-002 --fix 'T-003=<사용자 요청 원문>'` 으로 넘긴다. 물은 화면마다 승인이나 수정이 하나씩 있어야 한다. 어느 화면인지 알 수 없는 답은 다시 묻는다.
  - decide: 아래 "판단 기준" 대로 처리하고 `.orchestration/bin/orch answer <id> "<처리 내용>"` 으로 닫는다. worker_question 은 이 답이 그 워커에게 간다.
  - result: 관제가 늦게 끝낸 요청의 결과. 실패면 다시 판단한다.
  - done: 결과, 바뀐 주요 파일, 리뷰 판정을 요약해 전한다.
- 관제는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다(config.toml `[drive] auto_up`). 훅 안내가 없는데 관제 상태가 의심되면 `/orca-team:up` 을 실행한다.

### 판단 기준 (decide)
decide 에 적힌 `files` 만 읽고 `commands` 중 하나로 처리한다. 사용자에게 물을 때는 AskUserQuestion 을 직접 쓴다(질문 하나, 선택지 2–4개, 추천 하나).

| kind | 기준 |
|---|---|
| worker_failed | 리포트를 읽고 지시로 풀 수 있으면 `orch fix T-xxx --note "<원인과 지시>"`. 아니면 사용자에게 묻고 결과대로 fix 하거나 `orch drop T-xxx --reason "<이유>"` |
| worker_question | `docs/planning/spec.md`, `contracts.md`, `interview.md`, `voice.md`, 태스크 spec, 직무 정의, `.orchestration/project.md` 로 먼저 답한다. 문서가 다루지 않는 제품 결정이거나 문서끼리 모순될 때만 사용자에게 묻는다. 코드에 아직 없는 디자인 토큰을 물으면 토큰을 반영한 태스크가 done 이 된 뒤 그 토큰 이름으로 답한다 |
| escalation | 내용을 정리해 지시로 풀거나 사용자에게 묻는다 |
| max_rounds | 리뷰 요약과 함께 "한 번 더 / 중단"을 묻는다. 한 번 더면 `orch fix T-xxx --extra-round`, 중단이면 `orch drop` |
| repeat_test_failure | 실패가 그 태스크 탓이 아닐 수 있다(소유 경로 밖 코드, 다른 워커의 커밋 안 된 파일). 리뷰 파일의 quick 출력 끝을 보고 사용자에게 묻는다 |
| test_config | 사용자가 직접 넣은 `[test]` 면 사용자에게 묻는다. 아니면 `[test]` 를 고치는 기획 태스크를 만든다 |
| planning_gaps | 문서로 답할 수 있으면 묶음 정리 태스크 spec 에 넣는다(시작 전이면 spec 커밋, working 이면 `orch scope`). 제품 결정이 필요한 항목은 모아 한 번에 묻고, 넣기로 하면 증분 기획 태스크(`plan: append`)를 만든다 |
| unknown_regression | 원인 태스크를 정해(모르면 사용자에게 묻는다) 회귀 수정 developer 태스크를 만든다. front matter `owns:` 는 원인 태스크와 같게, Change 첫 줄은 decide 의 `source`(예: `출처: T-xxx followups`), Acceptance 는 "실패한 테스트 <이름>이 `orch test T-yyy` 에서 통과한다" |
| token_update | 보강한 디자인 시스템의 토큰을 UI 기반 developer 태스크에 반영할지 정한다. 그 태스크가 done 이면 `orch task new developer "토큰 갱신 반영: <요약>"`(owns 는 UI 기반 태스크와 같게, `depends_on:` 에 보강 태스크), working 이면 `orch scope`, 리뷰 중이면 결과를 기다린 뒤 정한다 |

```

`### 위치` 절 첫 줄을 다음으로 바꾸고 끝에 한 줄을 더한다.

```markdown
- 설정: `.orchestration/config.toml`(고정 에이전트 `[agents]`, 직무별 모델·effort·소유 경로, `max_parallel`, `[overflow]`, `user_approval_roles`)
```

```markdown
- 관제 기록: `.orchestration/state/drive/drive.log`, 고정 에이전트 상태: `.orchestration/state/agents.json`, 인수인계: `docs/handoff/<에이전트>.md`
```

- [ ] **Step 4: 코디네이터 문서를 지우고 문구를 바꾼다**

```bash
git rm -q orca-team/templates/coordinator.md
```

`setup.py` `SCAFFOLD_FILES` 에서 `("coordinator.md", ".orchestration/coordinator.md"),` 줄을 지운다.

문구를 바꾼다(정확히 이 문장들이다).

```bash
cd orca-team
sed -i '' \
  -e 's/프리앰블의 ask 명령으로 코디네이터에게 알린다\. 코디네이터가 메인(사용자)에게 묻는다\./프리앰블의 ask 명령으로 알린다. 메인이 사용자에게 묻는다./' \
  -e 's/질문은 코디네이터를 거치지 않고 메인을 통해 사용자에게 바로 간다/질문은 메인을 통해 사용자에게 바로 간다/' \
  -e 's/프리앰블의 ask 명령으로 코디네이터에게 묻는다/프리앰블의 ask 명령으로 묻는다(메인이 답한다)/' \
  -e 's/코디네이터가 화면 디자이너들의/관제가 화면 디자이너들의/' \
  -e 's/코디네이터가 이 기록으로/관제가 이 기록으로/' \
  -e 's/코디네이터가 제안을 모아/관제가 제안을 모아/' \
  -e 's/코디네이터가 워커 질문에 답할 때/메인이 워커 질문에 답할 때/' \
  -e 's/코디네이터가 developer 태스크로 만든다/관제가 developer 태스크로 만든다/' \
  templates/worker-spec.md templates/roles/*.md
sed -i '' \
  -e 's/{review_line}코디네이터 지시: {note}/{review_line}지시: {note}/' \
  -e 's/(코디네이터가 따로 태스크로 만든다)/(관제가 따로 태스크로 만든다)/' \
  -e 's/코디네이터가 회귀 수정 태스크로/관제가 회귀 수정 태스크로/' \
  -e 's/재시작한 코디네이터가 따를 다음 행동/다시 뜬 관제가 따를 다음 행동/' \
  lib/orch/tasks.py
sed -i '' -e 's/프리앰블의 ask 명령으로 코디네이터에게 묻는다/프리앰블의 ask 명령으로 묻는다/' lib/orch/review.py
sed -i '' -e 's/코디네이터가 이것으로/관제가 이것으로/' lib/orch/followups.py
grep -rn "코디네이터" templates skills lib/orch
```

마지막 grep 에 남은 줄(`ask.py` 의 docstring 등)은 문맥에 맞게 "관제" 나 "메인" 으로 고친다. `lib/orch/config.py`·`lib/orch/setup.py` 의 옛 `[coordinator]`·upgrade 거부 문구는 남긴다(위 테스트도 이 둘을 뺀다). `tasks.next_action` 의 안내 문구 중 `orch notify --type question 으로 메인에 묻고` 는 `메인에 decide 로 올리고` 로 바꾼다(관제가 같은 판단을 decide 로 올린다).

- [ ] **Step 5: 스킬을 바꾼다**

`orca-team/skills/up/SKILL.md` 전체:

```markdown
---
name: up
description: >
  orca-team 프로젝트의 관제(orch drive)와 메시지 watcher 를 되살리는 수동 복구. 관제는 보통 메인 세션 훅이 자동으로 살린다(config.toml [drive] auto_up). 자동 부활이 꺼져 있거나 훅이 안내했을 때 쓴다.
  트리거: "/orca-team:up", SessionStart 안내.
---

# /orca-team:up

이 세션은 메인이다.

1. `.orchestration/bin/orch status` 를 실행한다.
2. `drive.alive` 가 true 가 아니면 `.orchestration/bin/orch up --wait-run 180` 을 Bash `timeout: 240000` 으로 실행한다(Run 생성을 최대 180초 기다리므로 기본 2분 제한에 걸리지 않게 한다).
3. `watch_alive` 가 false 면 Bash `run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. `unread_outbox` 가 0 보다 크면 watch 가 바로 끝나며 밀린 메시지를 준다.
4. `open_questions`(답하지 않은 질문과 decide)가 비어 있지 않으면 `.orchestration/bin/orch reask` 를 실행한다. 출력은 watch 와 같은 형식의 메시지 배열이다. CLAUDE.md 의 "메인 규칙" 대로 하나씩 처리한다.
5. 보고한다: 관제 상태, 고정 에이전트 표(이름, 맡은 태스크, 대기열, 리셋 예정), 진행 중 태스크(id, 직무, 상태, 라운드) 표.
```

`orca-team/skills/init/SKILL.md`:
- description 의 `(메인/코디네이터/직무 워커, Codex 리뷰)` 를 `(메인/관제/고정 에이전트, Codex 리뷰)` 로 바꾼다.
- `## 5. 코디네이터 기동` 을 `## 5. 관제 기동` 으로 바꾼다(명령은 그대로).
- 6단계 2번의 "코디네이터와 인덱스를 같이 쓰므로" 를 "관제·워커와 인덱스를 같이 쓰므로" 로, 3번의 "코디네이터 터미널 handle" 을 "관제 탭(orca-team 관제) handle" 로 바꾼다.

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_skills test_setup test_tasks -v 2>&1 | tail -5`
Expected: OK. `test_setup` 에서 scaffold 가 `coordinator.md` 를 만든다고 기대한 줄이 있으면 지운다.

- [ ] **Step 7: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add -A orca-team/templates orca-team/skills orca-team/lib/orch orca-team/tests
git commit -m "docs(orca-team): 메인 규칙에 판단 기준을 넣고 코디네이터 문서와 문구를 지운다"
```

---

### Task 15: 기존 프로젝트를 0.7.0 으로 (`orch upgrade`)

**Files:**
- Modify: `orca-team/lib/orch/setup.py` (`upgrade` 와 도우미)
- Test: `orca-team/tests/test_setup.py`

**Interfaces:**
- Consumes: `SCAFFOLD_FILES`(Task 14 이후 `coordinator.md` 없음), `orca.terminal_state`, `tasks.all_states`, `TEMPLATES/config.toml`·`CLAUDE.section.md`.
- Produces: `setup.upgrade(p) -> dict` 결과에 `removed`, `config`(`{"changed": [...]}` 또는 `{"skipped": 이유}`), `claude_md`(`{"updated": bool}` 또는 `{"skipped": 이유}`)가 더해진다. `setup.OLD_CODEX_EFFORT`, `setup.editable_reason(p, rel) -> str | None`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`test_setup.py` 끝에 더한다(위 import 에 `import tomllib` 이 없으면 더한다).

```python
OLD_CONFIG = """max_parallel = 4
user_approval_roles = ["designer"]

[coordinator]
model = "opus"
effort = "xhigh"
auto_up = false

[review]
max_rounds = 3

[defaults]
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "high"

[roles.planner]
title = "기획"
codex_effort = "high"
owns = ["docs/planning/**"]

[roles.designer]
title = "디자인"
codex_effort = "medium"
owns = ["docs/design/**"]

[roles.developer]
title = "개발"
codex_effort = "low"
owns = ["src/**"]
"""


class Upgrade070Test(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        (self.root / ".orchestration/config.toml").write_text(OLD_CONFIG, encoding="utf-8")
        (self.root / ".orchestration/coordinator.md").write_text("# 옛 코디네이터 절차\n", encoding="utf-8")
        claude = self.root / "CLAUDE.md"
        claude.write_text("# 내 프로젝트\n\n<!-- orca-team -->\n## orca-team 오케스트레이션\n옛 메인 규칙(orch tell)\n",
                          encoding="utf-8")
        (self.root / ".orchestration/state/run.json").write_text(
            json.dumps({"run_id": "run_old", "coordinator_handle": "term_old"}), encoding="utf-8")
        self.respond("terminal show", {"ok": False, "error": {"code": "terminal_handle_stale"}}, code=1)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "0.6.0 project")

    def upgrade(self, check=True):
        return self.orch("upgrade", check=check, env={"ORCA_TERMINAL_HANDLE": "term_main"})

    def test_migrates_coordinator_project(self):
        out = json.loads(self.upgrade().stdout)
        self.assertIn(".orchestration/coordinator.md", out["removed"])
        self.assertFalse((self.root / ".orchestration/coordinator.md").exists())
        cfg = tomllib.loads((self.root / ".orchestration/config.toml").read_text(encoding="utf-8"))
        self.assertNotIn("coordinator", cfg)
        self.assertIs(cfg["drive"]["auto_up"], False)
        self.assertIs(cfg["overflow"]["enabled"], True)
        self.assertEqual(cfg["agents"]["production"]["roles"], ["planner", "designer", "developer"])
        self.assertNotIn("research", cfg["agents"])  # director 직무가 없는 프로젝트
        self.assertEqual((cfg["defaults"]["codex_effort"], cfg["roles"]["planner"]["codex_effort"],
                          cfg["roles"]["designer"]["codex_effort"], cfg["roles"]["developer"]["codex_effort"]),
                         ("xhigh", "xhigh", "xhigh", "low"))  # 사용자가 바꾼 low 는 둔다
        claude = (self.root / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(claude.startswith("# 내 프로젝트\n"))
        self.assertIn("### 판단 기준 (decide)", claude)
        self.assertNotIn("orch tell", claude)
        self.assertNotIn("coordinator_handle", json.loads((self.root / ".orchestration/state/run.json").read_text()))
        self.assertIn("/orca-team:up", out["next"])

    def test_refuses_while_tasks_run_or_coordinator_lives(self):
        state = self.root / ".orchestration/state/tasks/T-001.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"id": "T-001", "status": "working"}), encoding="utf-8")
        proc = self.upgrade(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("T-001(working)", proc.stderr)
        state.unlink()
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}})
        proc = self.upgrade(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("코디네이터 탭을 닫은 뒤", proc.stderr)

    def test_dirty_config_is_skipped(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(OLD_CONFIG + "\n# 고치는 중\n", encoding="utf-8")
        out = json.loads(self.upgrade().stdout)
        self.assertIn("커밋 안 된 변경", out["config"]["skipped"])
        self.assertIn("[coordinator]", path.read_text(encoding="utf-8"))
```

기존 `upgrade` 테스트 중 `coordinator.md` 를 바꾸거나 만든다고 기대하는 검사가 있으면 지운다.

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_setup.Upgrade070Test -v 2>&1 | tail -8`
Expected: FAIL — `removed`·`config`·`claude_md` 가 없다.

- [ ] **Step 3: `setup.py` 의 `upgrade` 를 바꾼다**

import 에 `import json`, `import re`, `import tomllib` 와 `from .tasks import all_states, launch_in_progress, next_action`(이미 있음)을 확인한다. `UPGRADE_NEXT` 를 바꾸고 상수·도우미를 더한 뒤 `upgrade` 를 바꾼다.

```python
UPGRADE_NEXT = ("바뀐 파일을 확인하고(git diff -- .orchestration CLAUDE.md) 커밋한다. 그다음 메인에서 /orca-team:up 으로 "
                "관제를 띄운다. 테스트 관문을 쓰려면 docs/planning/tasks.toml 에 [test] 를 넣는다(README \"묶음 검증\").")
# 0.6.0 템플릿의 codex_effort. 이 값 그대로인 표만 0.7.0 값(xhigh)으로 올린다(사용자가 바꾼 값은 둔다).
OLD_CODEX_EFFORT = {"defaults": "high", "roles.planner": "high", "roles.director": "high", "roles.designer": "medium",
                    "roles.developer": "xhigh", "roles.tester": "high"}


def editable_reason(p: Project, rel: str) -> str | None:
    """git 에 추적되고 커밋 안 된 변경이 없으면 None(고쳐도 된다), 아니면 건너뛸 이유."""
    if not git_ok(p.root, "ls-files", "--error-unmatch", "--", rel):
        return "git 에 추적되지 않는다. 템플릿과 직접 비교해 바꾼다"
    if git(p.root, "status", "--porcelain", "--", rel).strip():
        return "커밋 안 된 변경이 있다. 커밋하거나 되돌린 뒤 다시 한다"
    return None


def table_re(header: str) -> re.Pattern:
    return re.compile(rf"^\[{re.escape(header)}\][ \t]*\n.*?(?=^\[|\Z)", re.M | re.S)


def refuse_busy(p: Project) -> None:
    busy = [f"{st['id']}({st.get('status')})" for st in all_states(p) if st.get("status") in ("working", "reviewing")]
    if busy:
        raise OrchError("진행 중인 태스크가 있다: " + ", ".join(busy) + ". 끝나기를 기다린 뒤 다시 한다"
                        "(옛 Run 의 메일은 새 관제로 넘어가지 않는다)", 2)
    handle = load_run(p).get("coordinator_handle")
    if handle and orca.terminal_state(handle) == "alive":
        raise OrchError(f"코디네이터가 아직 떠 있다({handle}). 코디네이터 탭을 닫은 뒤 다시 한다", 2)


def migrate_config(p: Project) -> dict:
    """[coordinator] → [drive], [overflow]·[agents] 추가, 0.6.0 기본 codex_effort → xhigh. 글자 단위로 고쳐 주석을 지킨다."""
    rel = ".orchestration/config.toml"
    reason = editable_reason(p, rel)
    if reason:
        return {"skipped": reason}
    path = p.root / rel
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    changed = []
    auto_up = True
    if "coordinator" in data:
        auto_up = data["coordinator"].get("auto_up", True) is not False
        text = table_re("coordinator").sub("", text)
        changed.append("[coordinator] 지움")
    for header, old in OLD_CODEX_EFFORT.items():
        table = data.get("defaults", {}) if header == "defaults" else data.get("roles", {}).get(header[6:], {})
        if table.get("codex_effort") != old:
            continue
        text = table_re(header).sub(lambda m: re.sub(r'(?m)^codex_effort\s*=\s*"[^"]*"', 'codex_effort = "xhigh"',
                                                      m.group(0)), text)
        changed.append(f"[{header}].codex_effort {old} → xhigh")
    text = text.rstrip() + "\n"
    if "drive" not in data:
        text += f"\n[drive]\nauto_up = {'true' if auto_up else 'false'}\n"
        changed.append("[drive]")
    if "overflow" not in data:
        text += "\n[overflow]\nenabled = true\n"
        changed.append("[overflow]")
    if "agents" not in data:
        roles = set(data.get("roles", {}))
        for name, agent in tomllib.loads((TEMPLATES / "config.toml").read_text(encoding="utf-8"))["agents"].items():
            keep = [r for r in agent["roles"] if r in roles]
            if not keep:
                continue
            text += (f'\n[agents.{name}]\ntitle = "{agent["title"]}"\nroles = {json.dumps(keep, ensure_ascii=False)}\n'
                     f'claude_model = "{agent["claude_model"]}"\nclaude_effort = "{agent["claude_effort"]}"\n')
            changed.append(f"[agents.{name}]")
    tomllib.loads(text)  # 고친 결과도 TOML 이어야 한다
    if changed:
        path.write_text(text, encoding="utf-8")
    return {"changed": changed}


def replace_claude_section(p: Project) -> dict:
    """CLAUDE.md 의 orca-team 절(<!-- orca-team --> 부터 끝까지, scaffold 가 끝에 붙인다)을 새 템플릿으로 바꾼다."""
    path = p.root / "CLAUDE.md"
    if not path.exists():
        return {"skipped": "CLAUDE.md 가 없다"}
    reason = editable_reason(p, "CLAUDE.md")
    if reason:
        return {"skipped": reason}
    text = path.read_text(encoding="utf-8")
    start = text.find(MARK)
    if start < 0:
        return {"skipped": f"orca-team 절 표시({MARK})가 없다"}
    new = text[:start] + (TEMPLATES / "CLAUDE.section.md").read_text(encoding="utf-8")
    if new == text:
        return {"updated": False}
    path.write_text(new, encoding="utf-8")
    return {"updated": True}
```

`upgrade` 를 바꾼다.

```python
def upgrade(p: Project) -> dict:
    """프로젝트를 이 플러그인 버전으로 맞춘다. 복사된 규칙 파일(config.toml 제외)을 템플릿으로 바꾸고, 0.7.0 이전 프로젝트면
    코디네이터 문서를 지우고 config.toml·CLAUDE.md 를 옮긴다. git 에 커밋돼 있고 커밋 안 된 변경이 없는 파일만 고친다.
    커밋하지 않는다. 진행 중인 태스크가 있거나 코디네이터가 떠 있으면 거부한다."""
    refuse_busy(p)
    updated, created, skipped, removed = [], [], [], []
    for src, dst in [(s, d) for s, d in SCAFFOLD_FILES if d not in UPGRADE_KEEP]:
        target = p.root / dst
        new = (TEMPLATES / src).read_bytes()
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new)
            created.append(dst)
            continue
        reason = editable_reason(p, dst)
        if reason:
            skipped.append({"path": dst, "reason": reason})
        elif target.read_bytes() != new:
            target.write_bytes(new)
            updated.append(dst)
    legacy = ".orchestration/coordinator.md"
    if (p.root / legacy).exists():
        reason = editable_reason(p, legacy)
        if reason:
            skipped.append({"path": legacy, "reason": reason})
        else:
            (p.root / legacy).unlink()
            removed.append(legacy)
    config = migrate_config(p)
    claude_md = replace_claude_section(p)
    update_run(p, lambda r: (r.pop("coordinator_handle", None), r.pop("launched_at", None)))
    shim = p.root / ".orchestration/bin/orch"
    if shim.exists():
        shim.chmod(0o755)
    changed = [*updated, *created, *removed, *([".orchestration/config.toml"] if config.get("changed") else []),
               *(["CLAUDE.md"] if claude_md.get("updated") else [])]
    stat = git(p.root, "diff", "--stat", "--", *changed, check=False) if changed else ""
    return {"updated": updated, "created": created, "removed": removed, "skipped": skipped, "config": config,
            "claude_md": claude_md, "diff_stat": stat.strip(), "next": UPGRADE_NEXT}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_setup -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: 전체 테스트 후 커밋한다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: OK

```bash
git add orca-team/lib/orch/setup.py orca-team/tests/test_setup.py
git commit -m "feat(orca-team): orch upgrade 가 코디네이터 프로젝트를 관제·고정 에이전트 설정으로 옮긴다"
```

---

### Task 16: 문서와 0.7.0

**Files:**
- Modify: `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (버전)
- Modify: `README.md`
- Modify: `docs/superpowers/2026-11-05-orca-team-status.md`
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md` (§13 끝에 한 줄)

- [ ] **Step 1: 버전을 올린다**

세 곳의 `0.6.0` 을 `0.7.0` 으로 바꾼다(`paths.py` 의 `VERSION`, 두 JSON 의 `"version"`).

Run: `cd orca-team/tests && python3 -m unittest test_cli -v 2>&1 | tail -3`
Expected: OK(세 곳이 같은지 검사한다)

- [ ] **Step 2: README 를 고친다**

`grep -n "코디네이터" README.md` 로 찾은 구조 설명을 다음 뜻으로 바꾼다: 메인(사용자와 대화·판단) / 관제(`orch drive`, 관제 탭 셸, 절차) / 고정 에이전트(생산·조사·테스트, 탭 하나에 작업 pane 과 리뷰 보기 pane) / 임시 워커(바쁠 때) / Codex 리뷰(리뷰마다 새 실행). "묶음 검증" 절 뒤에 절을 더한다.

```markdown
## 고정 에이전트 (0.7.0)

- 생산(planner·designer·developer), 조사(director), 테스트(tester) 에이전트가 각자 Orca 탭 하나에 뜬다. 왼쪽은 작업 세션, 오른쪽은 그 에이전트 태스크의 Codex 리뷰 진행(`orch review-view`)이다.
- 같은 세션이 여러 태스크와 수정 라운드를 이어 맡는다. 묶음(tester 태스크)이 끝나면 `docs/handoff/<에이전트>.md` 에 인수인계를 쓰고 세션을 비운다. 직접 비우려면 `orch agent reset <에이전트>`.
- 에이전트가 바쁠 때 준비된 독립 태스크는 임시 워커로 돈다(`[overflow] enabled`).
- 코디네이터는 없다. 관제(`orch drive`, "orca-team 관제" 탭)가 절차를 돌리고 판단할 일만 메인에 decide 로 올린다.

### 0.6.0 이하 프로젝트를 올리기
1. 진행 중인 태스크가 끝나기를 기다리고 코디네이터 탭을 닫는다.
2. 메인에서 `.orchestration/bin/orch upgrade` 를 실행하고 출력의 바뀐 파일을 확인한다.
3. `.orchestration/` 과 `CLAUDE.md` 를 경로 지정 커밋한다.
4. `/orca-team:up` 으로 관제를 띄운다.
```

- [ ] **Step 3: 상태 문서와 기본 설계를 고친다**

`docs/superpowers/2026-11-05-orca-team-status.md`:
- 제목 줄의 날짜·버전을 `(2026-11-09, 0.7.0)` 으로 바꾼다.
- 버전 표에 행을 더한다: `| 0.7.0 | 고정 에이전트: 코디네이터 LLM 대신 관제(orch drive)가 절차를, 메인이 판단(decide)을. 생산·조사·테스트 고정 에이전트(작업·리뷰 보기 좌우 pane), 묶음 끝 인수인계·리셋, 복구, 임시 워커, 메인 → 관제 요청 큐, design-decide, --extra-round, 리뷰 effort xhigh | specs/2026-11-09-orca-team-persistent-agents-design.md, plans/2026-11-09-orca-team-persistent-agents.md |`
- "검증" 의 테스트 수를 이번 전체 실행 결과로 바꾼다.
- "실환경에서 확인하지 않은 흐름" 에 더한다: `0.7.0 전체(설계 §15): 관제 탭과 고정 에이전트 탭 배치, 같은 세션 수정 라운드, 묶음 끝 리셋, 임시 워커 분기, 고정 에이전트 터미널 닫기·Orca 재시작 복구, 리뷰 보기 pane.`
- "다음 작업 후보" 맨 위에 `0.6.0 실환경 확인(사용자가 0.7.0 구현 전에 하기로 했다)` 다음 줄로 `0.7.0 실환경 확인(설계 §15)` 와 `0.7.1: 테스트 에이전트를 Codex 로, 그 리뷰를 Claude 로(설계 §16)` 를 넣는다.

`docs/superpowers/specs/2026-10-30-orca-team-design.md` §13 끝에 한 줄을 더한다: `- 0.7.0 에서 코디네이터를 관제(orch drive)와 메인 판단으로 바꿨다. 설계: specs/2026-11-09-orca-team-persistent-agents-design.md.`

- [ ] **Step 4: 검사하고 커밋한다**

Run:
```bash
PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3
git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'
grep -rn "코디네이터" orca-team/templates orca-team/skills orca-team/lib orca-team/hooks | grep -v -E 'lib/orch/(config|setup)\.py'
```
Expected: 테스트 OK, 두 grep 은 비어 있다(설계·계획 문서의 옛 이야기는 검사 대상이 아니다. `config.py`·`setup.py` 는 옛 `[coordinator]` 를 옮기라는 안내와 upgrade 거부 문구라 남긴다). 훅의 `auto_up_context` docstring 처럼 남은 줄이 나오면 "관제" 로 고친 뒤 다시 본다.

```bash
git add orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json README.md docs/superpowers/2026-11-05-orca-team-status.md docs/superpowers/specs/2026-10-30-orca-team-design.md
git commit -m "docs(orca-team): 0.7.0 고정 에이전트 구조를 문서에 적고 버전을 올린다"
```

---

## 실환경 확인 (구현 뒤, 사용자와 함께)

설계 §15 를 따른다. 0.6.0 실환경 확인을 먼저 끝낸다. Orca 재시작, 터미널·탭 닫기, 시험 프로젝트 정리는 사용자 승인을 받고 한다. 결과는 설계 §17 에 적는다. 실환경 확인이 끝나기 전에는 main 에 병합하지 않는다.
