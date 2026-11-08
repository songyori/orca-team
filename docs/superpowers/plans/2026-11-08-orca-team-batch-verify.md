# orca-team 묶음 검증 구현 계획 (0.6.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 테스트는 태스크마다 관련된 것만, 전체 테스트는 묶음마다 한 번 orch 가 돌리고, Codex 리뷰는 라운드마다 좁혀져 P1 만 승인을 막게 한다.

**Architecture:** 새 모듈 `lib/orch/testgate.py` 가 `docs/planning/tasks.toml` 의 `[test]` 명령을 실행·기록하고(`orch test`), `review.py` 가 Codex 전에 그 결과로 관문을 세운다. 리뷰 프롬프트는 등급·라운드 범위·묶음 절을 받고, orch 는 리뷰 파일의 P1 수로 판정을 맞춘다. 새 모듈 `lib/orch/followups.py` 가 리뷰 파일의 후속·기획 누락·회귀를 모으고(`orch followups`), `setup.upgrade` 가 기존 프로젝트의 규칙 파일을 바꾼다(`orch upgrade`).

**Tech Stack:** Python 3.11 표준 라이브러리(`subprocess`, `fcntl`, `shlex`, `tomllib`), `/bin/sh`, unittest, 가짜 orca·Codex 래퍼(`tests/fakes/`), 가짜 테스트 명령(`tests/helpers.py` 의 `FAKE_TEST`).

**설계:** `docs/superpowers/specs/2026-11-08-orca-team-batch-verify-design.md`

> 구현 뒤 메모(2026-11-08): 이 계획은 구현 전 기록이다. 최종 리뷰에서 바뀐 점(tester 관문은 full 만, `[test]` 는 커밋된 HEAD 에서 읽기, 커밋 전 quick 거부, 비대칭 판정 맞추기 등)은 설계 문서 §2 의 9·10번과 §2.1 에 있다.

## Global Constraints

- 표준 라이브러리만 쓴다. Python 3.11 이상.
- 테스트 전체: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` 이 경고 없이 통과해야 한다(기존 576개 + 추가분). 한 파일만: `cd orca-team/tests && python3 -m unittest test_testgate -v`.
- **테스트가 실제 Orca 를 부르면 안 된다.** 같은 프로세스 안에서 orch 함수를 부르는 테스트는 `patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})` 로 가짜 orca 를 쓴다.
- **테스트가 사용자 홈 캐시에 쓰면 안 된다.** 같은 프로세스 안에서 `testgate.attempt`·`run_task` 처럼 로그를 쓰는 함수를 부르는 테스트는 `patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})` 를 쓴다. `orch` 하위 프로세스는 `ProjectCase.env` 가 이미 정한다.
- 버전은 0.6.0 이고 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다(Task 9).
- 공개 저장소다. 커밋 전에 `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'` 결과가 비어야 한다.
- Markdown 에서 숫자 범위를 `~` 로 쓰지 않는다(`3–6`).
- 템플릿·문서 문체: 한다체, 코드 표기 뒤 조사는 띄운다("`x` 를").
- 작업 브랜치는 `feat/0.6.0-batch-verify` 다. main 에 직접 커밋하지 않는다.
- 커밋 메시지 끝에 실제 작성 모델의 `Co-Authored-By` 트레일러를 붙인다.
- 서브에이전트는 `~/.claude/plugins` 와 Orca 를 건드리지 않는다. `.superpowers/` 를 커밋하지 않는다.
- 리뷰 프롬프트에서 `{design_section}{text_section}## REVIEW_FILE 형식` 은 붙어 있어야 한다(`test_templates.py` 가 검사한다). 새 절은 그 앞에 둔다. 절 문자열은 끝에 빈 줄 하나를 갖고(`render(...) + "\n"`), 빈 절은 빈 문자열이다. 프롬프트에 `\n\n\n` 이 생기면 안 된다.

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `orca-team/lib/orch/testgate.py` (새) | `[test]` 형식 검사, 명령 실행(시간 초과, 잠금), quick·full·final, 기록, 리뷰 관문 결과 | 1, 3 |
| `orca-team/lib/orch/cli.py` | `orch test`, `orch followups`, `orch upgrade` | 1, 5, 7 |
| `orca-team/tests/helpers.py` | 가짜 테스트 명령과 `[test]` 쓰기 도우미 | 1 |
| `orca-team/tests/test_testgate.py` (새) | testgate 단위·CLI 테스트 | 1 |
| `orca-team/lib/orch/plan.py` | `[test]` 형식과 묶음 규칙 검사 | 2 |
| `orca-team/tests/test_plan.py` | 묶음 규칙 테스트 | 2 |
| `orca-team/lib/orch/review.py` | 테스트 관문, 테스트·묶음·라운드 절, 리뷰 기록 `head`, 판정 맞추기 | 3, 4 |
| `orca-team/templates/review-prompt.md` | `{verify_rule}`, 등급과 판정, 새 절 자리, 새 리뷰 파일 형식 | 3, 4 |
| `orca-team/templates/review-tests.md`, `review-group.md`, `review-round.md` (새) | 리뷰 프롬프트 절 | 3, 4 |
| `orca-team/tests/fakes/codex-run.sh` | `body` 모드(리뷰 본문을 파일에서 가져온다) | 4 |
| `orca-team/tests/test_review.py` | 관문·묶음·라운드·판정 맞추기 테스트 | 3, 4 |
| `orca-team/lib/orch/tasks.py` | `REVIEW_LINE` 문구, 워커 spec `{test_block}` | 4, 6 |
| `orca-team/templates/worker-spec.md` | `{test_block}` 자리 | 6 |
| `orca-team/tests/test_tasks.py` | 테스트 절 테스트 | 6 |
| `orca-team/lib/orch/followups.py` (새), `orca-team/tests/test_followups.py` (새) | 후속 모으기 | 5 |
| `orca-team/lib/orch/setup.py`, `orca-team/tests/test_setup.py` | `upgrade` | 7 |
| `orca-team/templates/coordinator.md`, `roles/planner.md`, `roles/tester.md`, `roles/developer.md`, `orca-team/tests/test_templates.py` | 흐름·직무 문서 | 8 |
| `README.md`, 상태 문서, 기본 설계 §13, 버전 세 곳, `orca-team/tests/test_cli.py` | 문서와 0.6.0 | 9 |

---

### Task 1: 테스트 명령 실행 `testgate.py` 와 `orch test`

**Files:**
- Create: `orca-team/lib/orch/testgate.py`
- Modify: `orca-team/lib/orch/cli.py` (`build_parser`, `run`)
- Modify: `orca-team/tests/helpers.py` (끝에 도우미 추가)
- Test: `orca-team/tests/test_testgate.py` (새)

**Interfaces:**
- Produces (testgate):
  - 상수 `PLAN_FILE = "docs/planning/tasks.toml"`, `GATED_ROLES = ("developer", "tester")`, `DEVELOPER_ROLE = "developer"`, `TESTER_ROLE = "tester"`, `PASSED = ("passed", "flaky", "skipped")`.
  - `GateConfig(quick: str, full: str, timeout_min: int = 30, ok_exit: tuple[int, ...] = (0,), exclusive: bool = False)` (frozen dataclass).
  - `config_problems(raw) -> list[str]`, `parse_config(raw) -> GateConfig`(오류면 `OrchError` exit 2), `load_config(p) -> GateConfig | None`.
  - `execute(root: Path, command: str, log: Path, timeout_sec: float, ok_exit: tuple[int, ...]) -> dict` — `{"status": passed|failed|timeout|unrunnable, "exit": int|None, "seconds": float, "tail": str}`.
  - `attempt(p, cfg, command, name, timeout_sec=None, lock_wait=None) -> dict` — execute 결과에 `command`, `log` 를 더한다.
  - `code_head(p, task_id, commits) -> str | None` — 리포트만 바꾼 커밋을 뺀 마지막 작업 커밋.
  - `load_records(p, name) -> dict` (`name` 은 태스크 ID 또는 `"final"`).
  - `run_task(p, task_id, kind) -> dict` (`kind`: `"quick"`|`"full"`), `run_final(p) -> dict`, `exit_code(result) -> int`.
- 기록 `.orchestration/state/tests/T-xxx.json`: `{"quick": {...}, "full": {...}}`. 항목 키: `round`, `head`, `status`, `exit`, `seconds`, `command`, `log`, `tail`, `dirty`, `at`, quick 은 `files`, flaky 는 `first`(`exit`, `log`).
- Produces (helpers): `FAKE_TEST`, `fake_test_commands(case) -> (quick, full)`, `write_test_config(root, quick, full, extra="")`, `set_test_modes(case, *modes)`, `read_test_args(case) -> list[list[str]]`.

- [ ] **Step 1: 테스트 도우미 추가** — `orca-team/tests/helpers.py` 맨 위 import 에 `import shlex` 를 더하고 파일 끝에 붙인다.

```python
FAKE_TEST = """import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
with (here / "test-args.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
modes_file = here / "test_modes.txt"
modes = modes_file.read_text(encoding="utf-8").split() if modes_file.exists() else []
mode = modes.pop(0) if modes else "pass"
modes_file.write_text("".join(m + "\\n" for m in modes), encoding="utf-8")
print("fake test", mode)
print()
print("done")
if mode == "fail":
    print("FAIL tests/a.test.ts > 더하기")
    sys.exit(1)
sys.exit({"five": 5, "missing": 127}.get(mode, 0))
"""


def fake_test_commands(case) -> tuple[str, str]:
    """가짜 테스트 명령 (quick, full). 인자는 $FAKE_DIR/test-args.jsonl 에 한 줄씩 남고,
    행동은 $FAKE_DIR/test_modes.txt 의 첫 단어(pass|fail|five|missing, 없으면 pass)다."""
    script = case.fake_dir / "fake_test.py"
    script.write_text(FAKE_TEST, encoding="utf-8")
    run = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    return f"{run} {{files}}", f"{run} --all"


def write_test_config(root, quick: str, full: str, extra: str = "") -> None:
    """docs/planning/tasks.toml 을 [test] 표 하나로 덮어쓴다. extra 는 [test] 안에 더할 줄(끝 줄바꿈 포함)."""
    path = Path(root) / "docs/planning/tasks.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[test]\nquick = {json.dumps(quick)}\nfull = {json.dumps(full)}\n{extra}", encoding="utf-8")


def set_test_modes(case, *modes: str) -> None:
    (case.fake_dir / "test_modes.txt").write_text("".join(m + "\n" for m in modes), encoding="utf-8")


def read_test_args(case) -> list[list[str]]:
    log = case.fake_dir / "test-args.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
```

- [ ] **Step 2: 실패하는 테스트 작성** — `orca-team/tests/test_testgate.py` 를 만든다.

```python
import fcntl
import json
import os
import time
import unittest
from unittest import mock

from helpers import fake_test_commands, read_test_args, set_test_modes, write_test_config
from test_tasks import TaskCase
from orch import plan, testgate
from orch.paths import OrchError, Project


class ConfigTest(unittest.TestCase):
    def test_plan_file_matches_plan_module(self):
        self.assertEqual(testgate.PLAN_FILE, plan.PLAN_FILE)

    def test_defaults(self):
        cfg = testgate.parse_config({"quick": "t {files}", "full": "t"})
        self.assertEqual((cfg.quick, cfg.full, cfg.timeout_min, cfg.ok_exit, cfg.exclusive),
                         ("t {files}", "t", 30, (0,), False))

    def test_problems(self):
        base = {"quick": "t {files}", "full": "t"}
        cases = [
            ("모르는 키 extra", {**base, "extra": 1}),
            ("quick 은 {files} 를 정확히 한 번", {**base, "quick": "t"}),
            ("quick 은 {files} 를 정확히 한 번", {**base, "quick": "t {files} {files}"}),
            ("full 은 {files} 를 담지 않는다", {**base, "full": "t {files}"}),
            ("quick 는 비지 않은 명령 문자열", {**base, "quick": " "}),
            ("full 는 비지 않은 명령 문자열", {"quick": "t {files}"}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": 0}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": 241}),
            ("timeout_min 은 1–240 정수", {**base, "timeout_min": True}),
            ("ok_exit 는 정수 목록", {**base, "ok_exit": []}),
            ("ok_exit 는 정수 목록", {**base, "ok_exit": ["0"]}),
            ("exclusive 는 true 또는 false", {**base, "exclusive": "yes"}),
        ]
        for needle, raw in cases:
            with self.subTest(needle=needle, raw=raw):
                self.assertTrue(any(needle in item for item in testgate.config_problems(raw)),
                                testgate.config_problems(raw))
        self.assertEqual(testgate.config_problems("x"), ["[test] 는 표여야 한다"])
        self.assertEqual(testgate.config_problems({**base, "timeout_min": 240, "ok_exit": [0, 5], "exclusive": True}), [])
        with self.assertRaises(OrchError) as ctx:
            testgate.parse_config({**base, "quick": "t"})
        self.assertEqual(ctx.exception.code, 2)


class NoConfigTest(TaskCase):
    def test_without_test_table_exit_2(self):
        self.write_spec("T-001")
        self.orch("start", "T-001")
        proc = self.orch("test", "T-001", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test] 가 없다", proc.stderr)

    def test_needs_id_or_full(self):
        proc = self.orch("test", check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("태스크 ID 또는 --full", proc.stderr)


class GateCase(TaskCase):
    """T-001(developer)을 시작하고 [test] 를 가짜 테스트 명령으로 둔 프로젝트."""

    def setUp(self):
        super().setUp()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: test 설정")
        self.write_spec("T-001")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "[T-001] spec: 로그인")
        self.orch("start", "T-001")

    def commit(self, path, text="x\n", message=None):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", message or f"[T-001] feat: {path}")

    def run_test(self, *args):
        return self.orch("test", *args, check=False)

    def record(self, name="T-001"):
        return json.loads((self.root / f".orchestration/state/tests/{name}.json").read_text(encoding="utf-8"))


class RunTaskTest(GateCase):
    def test_quick_passes_existing_changed_files_quoted(self):
        self.commit("src/a b.ts")
        self.commit("src/c.ts")
        self.commit("src/gone.ts")
        self.git("rm", "-q", "--", "src/gone.ts")
        self.git("commit", "-q", "-m", "[T-001] chore: 지움")
        code_sha = self.git("rev-parse", "HEAD").strip()
        self.commit("docs/tasks/T-001/report.md", "# 리포트\n", "[T-001] docs: 리포트")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["task"], out["kind"], out["status"]), ("T-001", "quick", "passed"))
        self.assertEqual(read_test_args(self), [["src/a b.ts", "src/c.ts"]])
        rec = self.record()["quick"]
        self.assertEqual((rec["round"], rec["head"], rec["status"], rec["exit"]), (1, code_sha, "passed", 0))
        self.assertEqual(rec["files"], ["src/a b.ts", "src/c.ts"])
        self.assertTrue(os.path.isfile(rec["log"]))
        self.assertIn(str(self.tmp / "cache/orca-team/runs"), rec["log"])

    def test_no_files_is_skipped(self):
        self.commit("docs/tasks/T-001/report.md", "# 리포트\n", "[T-001] docs: 리포트")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual((out["status"], out["head"], out["files"]), ("skipped", None, []))
        self.assertEqual(read_test_args(self), [])

    def test_fail_twice_is_failed_exit_1(self):
        self.commit("src/a.ts")
        set_test_modes(self, "fail", "fail")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertEqual((out["status"], out["exit"]), ("failed", 1))
        self.assertEqual(len(read_test_args(self)), 2)
        self.assertIn("FAIL tests/a.test.ts", out["tail"])
        self.assertNotIn("\n\n", out["tail"])
        self.assertIn("-quick-a2-", out["log"])

    def test_fail_then_pass_is_flaky(self):
        self.commit("src/a.ts")
        set_test_modes(self, "fail", "pass")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "flaky")
        self.assertEqual(out["first"]["exit"], 1)
        self.assertTrue(os.path.isfile(out["first"]["log"]))

    def test_ok_exit(self):
        write_test_config(self.root, self.quick, self.full, "ok_exit = [0, 5]\n")
        self.commit("src/a.ts")
        set_test_modes(self, "five")
        out = json.loads(self.run_test("T-001").stdout)
        self.assertEqual((out["status"], out["exit"]), ("passed", 5))

    def test_unrunnable_exit_2_without_record(self):
        self.commit("src/a.ts")
        set_test_modes(self, "missing")
        proc = self.run_test("T-001")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test] 명령을 실행할 수 없다(exit 127", proc.stderr)
        self.assertFalse((self.root / ".orchestration/state/tests/T-001.json").exists())

    def test_dirty_lists_uncommitted_files_outside_task(self):
        self.commit("src/a.ts")
        with (self.root / ".orchestration/config.toml").open("a", encoding="utf-8") as fh:
            fh.write("# 작업 중\n")
        (self.root / "src/a.ts").write_text("y\n", encoding="utf-8")
        out = json.loads(self.run_test("T-001").stdout)
        self.assertEqual(out["dirty"], [".orchestration/config.toml"])

    def test_full_for_task_and_final(self):
        self.commit("src/a.ts")
        out = json.loads(self.run_test("T-001", "--full").stdout)
        self.assertEqual((out["kind"], out["status"]), ("full", "passed"))
        self.assertEqual(read_test_args(self), [["--all"]])
        self.assertEqual(self.record()["full"]["round"], 1)
        out = json.loads(self.run_test("--full").stdout)
        self.assertEqual((out["task"], out["kind"]), (None, "full"))
        self.assertEqual(self.record("final")["full"]["head"], self.git("rev-parse", "HEAD").strip())

    def test_bad_task_id_exit_2(self):
        proc = self.run_test("../x")
        self.assertEqual(proc.returncode, 2)


class ExecuteTest(GateCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)

    def test_timeout_kills_process_group(self):
        started = time.monotonic()
        out = testgate.execute(self.root, "sleep 30 & sleep 30", self.tmp / "t.log", 0.5, (0,))
        self.assertEqual((out["status"], out["exit"]), ("timeout", None))
        self.assertLess(time.monotonic() - started, 10)

    def test_exclusive_lock_waits_then_fails(self):
        cfg = testgate.GateConfig("t {files}", "true", exclusive=True)
        lock = self.p.state / "tests" / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            with self.assertRaises(OrchError) as ctx:
                testgate.attempt(self.p, cfg, "true", "lock-test", lock_wait=0.3)
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("exclusive", str(ctx.exception))
            free = testgate.attempt(self.p, testgate.GateConfig("t {files}", "true"), "true", "lock-test", lock_wait=0.3)
            self.assertEqual(free["status"], "passed")
        self.assertEqual(testgate.attempt(self.p, cfg, "true", "lock-test", lock_wait=0.3)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_testgate -v 2>&1 | tail -5`
Expected: FAIL — `ImportError: cannot import name 'testgate'`.

- [ ] **Step 4: `orca-team/lib/orch/testgate.py` 작성**

```python
"""테스트 관문. docs/planning/tasks.toml 의 [test] 명령을 orch 가 직접 돌리고 결과를 남긴다.

quick 은 태스크가 바꾼 파일과 관련된 테스트, full 은 전체 테스트다. 결과는 state/tests/ 에, 출력 전체는
~/.cache/orca-team/runs/<프로젝트>/ 의 로그에 남긴다. [test] 가 없으면 관문이 꺼진다."""
from __future__ import annotations

import fcntl
import os
import shlex
import signal
import subprocess
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .globs import matches_any
from .paths import OrchError, Project
from .util import git, now, read_json, write_json

PLAN_FILE = "docs/planning/tasks.toml"  # plan.PLAN_FILE 과 같다(plan 이 이 모듈을 import 한다)
KEYS = ("quick", "full", "timeout_min", "ok_exit", "exclusive")
DEFAULT_TIMEOUT_MIN = 30
DEVELOPER_ROLE = "developer"
TESTER_ROLE = "tester"
GATED_ROLES = (DEVELOPER_ROLE, TESTER_ROLE)
PASSED = ("passed", "flaky", "skipped")
TAIL_LINES = 60
TAIL_WIDTH = 300
TAIL_BYTES = 65536


@dataclass(frozen=True)
class GateConfig:
    quick: str
    full: str
    timeout_min: int = DEFAULT_TIMEOUT_MIN
    ok_exit: tuple[int, ...] = (0,)
    exclusive: bool = False


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def config_problems(raw) -> list[str]:
    """[test] 표의 형식 오류. 없으면 빈 목록."""
    if not isinstance(raw, dict):
        return ["[test] 는 표여야 한다"]
    problems = []
    unknown = sorted(set(raw) - set(KEYS))
    if unknown:
        problems.append(f"[test]: 모르는 키 {', '.join(unknown)}")
    for name in ("quick", "full"):
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"[test]: {name} 는 비지 않은 명령 문자열이어야 한다")
    quick, full = raw.get("quick"), raw.get("full")
    if isinstance(quick, str) and quick.strip() and quick.count("{files}") != 1:
        problems.append("[test]: quick 은 {files} 를 정확히 한 번 담는다")
    if isinstance(full, str) and "{files}" in full:
        problems.append("[test]: full 은 {files} 를 담지 않는다")
    timeout = raw.get("timeout_min", DEFAULT_TIMEOUT_MIN)
    if not _int(timeout) or not 1 <= timeout <= 240:
        problems.append("[test]: timeout_min 은 1–240 정수")
    ok_exit = raw.get("ok_exit", [0])
    if not isinstance(ok_exit, list) or not ok_exit or not all(_int(x) for x in ok_exit):
        problems.append("[test]: ok_exit 는 정수 목록")
    if not isinstance(raw.get("exclusive", False), bool):
        problems.append("[test]: exclusive 는 true 또는 false")
    return problems


def parse_config(raw) -> GateConfig:
    problems = config_problems(raw)
    if problems:
        raise OrchError(f"{PLAN_FILE} [test] 검증 실패: " + "; ".join(problems), 2)
    return GateConfig(raw["quick"].strip(), raw["full"].strip(), raw.get("timeout_min", DEFAULT_TIMEOUT_MIN),
                      tuple(raw.get("ok_exit", [0])), raw.get("exclusive", False))


def load_config(p: Project) -> GateConfig | None:
    """작업 트리 tasks.toml 의 [test]. 파일이나 표가 없으면 None(관문 꺼짐). 읽지 못하거나 형식이 틀리면 exit 2."""
    try:
        data = tomllib.loads((p.root / PLAN_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
        raise OrchError(f"{PLAN_FILE} 읽기 실패: {exc}", 2) from exc
    if "test" not in data:
        return None
    return parse_config(data["test"])


def require_config(p: Project) -> GateConfig:
    cfg = load_config(p)
    if cfg is None:
        raise OrchError(f"{PLAN_FILE} 에 [test] 가 없다. 테스트 관문이 꺼져 있다", 2)
    return cfg


def tests_dir(p: Project) -> Path:
    return p.state / "tests"


def record_path(p: Project, name: str) -> Path:
    """name: 태스크 ID 또는 "final"."""
    return tests_dir(p) / f"{name}.json"


def load_records(p: Project, name: str) -> dict:
    data = read_json(record_path(p, name), {})
    return data if isinstance(data, dict) else {}


def save_record(p: Project, name: str, kind: str, entry: dict) -> None:
    data = load_records(p, name)
    data[kind] = entry
    write_json(record_path(p, name), data)


def log_path(p: Project, name: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / "orca-team" / "runs" / p.root.name / f"{name}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_tail(path: Path) -> str:
    """로그 마지막 TAIL_LINES 줄. 빈 줄은 빼고 줄마다 TAIL_WIDTH 자로 자른다."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            raw = fh.read()
    except OSError:
        return ""
    lines = [line[:TAIL_WIDTH] for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return "\n".join(lines[-TAIL_LINES:])


def kill_group(proc: subprocess.Popen) -> None:
    """시간을 넘긴 명령의 프로세스 그룹을 끝낸다(SIGTERM 5초 뒤 SIGKILL)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


def execute(root: Path, command: str, log: Path, timeout_sec: float, ok_exit: tuple[int, ...]) -> dict:
    """명령 하나를 /bin/sh 로 돌리고 출력을 log 에 쓴다. status: passed|failed|timeout|unrunnable(종료 코드 126·127)."""
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as fh:
        fh.write(f"$ {command}\n")
        fh.flush()
        try:
            proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=root, stdin=subprocess.DEVNULL, stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            raise OrchError(f"[test] 명령을 실행하지 못했다: {exc}", 2) from exc
        try:
            code = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            code = None
    seconds = round(time.monotonic() - started, 1)
    if code is None:
        status = "timeout"
    elif code in (126, 127):
        status = "unrunnable"
    else:
        status = "passed" if code in ok_exit else "failed"
    return {"status": status, "exit": code, "seconds": seconds, "tail": read_tail(log)}


@contextmanager
def run_lock(p: Project, cfg: GateConfig, wait_sec: float):
    """exclusive 면 state/tests/.lock 을 잡는다. wait_sec 안에 못 잡으면 exit 2."""
    if not cfg.exclusive:
        yield
        return
    path = tests_dir(p) / ".lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        deadline = time.monotonic() + wait_sec
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise OrchError(f"테스트 잠금(exclusive)을 {wait_sec:g}초 안에 얻지 못했다. 다른 테스트 실행이 "
                                    "끝난 뒤 다시 한다", 2) from None
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def attempt(p: Project, cfg: GateConfig, command: str, name: str, timeout_sec: float | None = None,
            lock_wait: float | None = None) -> dict:
    """명령 한 번. 시간 상한과 잠금 대기는 기본 timeout_min 분이다."""
    limit = cfg.timeout_min * 60
    log = log_path(p, name)
    with run_lock(p, cfg, limit if lock_wait is None else lock_wait):
        result = execute(p.root, command, log, limit if timeout_sec is None else timeout_sec, cfg.ok_exit)
    return {"command": command, "log": str(log), **result}


def run_quick(p: Project, cfg: GateConfig, files: list[str], name: str) -> dict:
    """관련 테스트. 대상 파일이 없으면 skipped. 실패하면 한 번 더 돌려 통과하면 flaky."""
    if not files:
        return {"status": "skipped", "exit": None, "seconds": 0.0, "command": None, "log": None, "tail": "",
                "files": []}
    command = cfg.quick.replace("{files}", " ".join(shlex.quote(f) for f in files))
    first = attempt(p, cfg, command, f"{name}-quick-a1")
    if first["status"] != "failed":
        return {**first, "files": files}
    second = attempt(p, cfg, command, f"{name}-quick-a2")
    if second["status"] == "passed":
        return {**second, "status": "flaky", "files": files, "first": {"exit": first["exit"], "log": first["log"]}}
    return {**second, "files": files}


def unrunnable(result: dict) -> OrchError:
    return OrchError(f"[test] 명령을 실행할 수 없다(exit {result['exit']}, 명령: {result['command']}). "
                     f"기획 태스크로 {PLAN_FILE} 의 [test] 를 고치거나 사용자가 직접 고친다. "
                     f"출력 끝: {result['tail'][-300:]}", 2)


def task_target(p: Project, task_id: str) -> tuple[dict, list[tuple[str, str]]]:
    """(태스크 상태, 작업 커밋). review·tasks 가 이 모듈을 import 하므로 실행 시점에 읽는다."""
    from .review import task_commits
    from .tasks import TASK_ID_RE, must_state

    if not TASK_ID_RE.match(task_id):
        raise OrchError(f"태스크 ID 형식이 아니다: {task_id}", 2)
    st = must_state(p, task_id)
    if not st.get("base"):
        raise OrchError(f"{task_id}: 아직 시작하지 않은 태스크다(base 없음)", 2)
    return st, task_commits(p.root, task_id, st["base"])


def code_head(p: Project, task_id: str, commits: list[tuple[str, str]]) -> str | None:
    """리포트(docs/tasks/T-xxx/)만 바꾼 커밋을 뺀 마지막 작업 커밋. 테스트 결과를 다시 쓸지 가르는 기준이다.
    워커는 테스트를 돌린 뒤 리포트를 커밋하므로 리포트 커밋이 재사용을 막지 않게 한다."""
    from .review import commit_paths

    report = f"docs/tasks/{task_id}/"
    for sha, _ in reversed(commits):
        if any(not f.startswith(report) for f in commit_paths(p.root, sha)):
            return sha
    return None


def quick_files(p: Project, task_id: str, commits: list[tuple[str, str]]) -> list[str]:
    """작업 커밋이 바꾼 파일 중 작업 트리에 있는 것. 리포트 경로는 뺀다."""
    from .review import changed_files

    report = f"docs/tasks/{task_id}/"
    return [f for f in changed_files(p.root, [sha for sha, _ in commits])
            if not f.startswith(report) and (p.root / f).is_file()]


def dirty_files(p: Project, allowed: list[str]) -> list[str]:
    """allowed 밖에서 커밋되지 않은 추적 파일 변경(실행 직후). 다른 워커의 작업 중 파일 탓인 실패를 가르는 데 쓴다."""
    from .review import worktree_status

    tracked, _ = worktree_status(p.root, untracked=False)
    return sorted(path for path in tracked if not matches_any(path, allowed))


def run_task(p: Project, task_id: str, kind: str) -> dict:
    """태스크 하나의 quick 또는 full. 결과를 state/tests/T-xxx.json 에 남긴다. 실행 불가면 기록 없이 exit 2."""
    cfg = require_config(p)
    st, commits = task_target(p, task_id)
    rnd = st.get("round", 1)
    name = f"{task_id}-r{rnd}"
    if kind == "quick":
        result = run_quick(p, cfg, quick_files(p, task_id, commits), name)
    else:
        result = attempt(p, cfg, cfg.full, f"{name}-full")
    if result["status"] == "unrunnable":
        raise unrunnable(result)
    entry = {"round": rnd, "head": code_head(p, task_id, commits), **result,
             "dirty": dirty_files(p, [*st.get("owns", []), f"docs/tasks/{task_id}/**"]), "at": now()}
    save_record(p, task_id, kind, entry)
    return {"task": task_id, "kind": kind, **entry}


def run_final(p: Project) -> dict:
    """태스크 없는 full(마지막 확인). state/tests/final.json 에 남긴다."""
    cfg = require_config(p)
    result = attempt(p, cfg, cfg.full, "final-full")
    if result["status"] == "unrunnable":
        raise unrunnable(result)
    entry = {"head": git(p.root, "rev-parse", "HEAD").strip(), **result, "dirty": dirty_files(p, []), "at": now()}
    save_record(p, "final", "full", entry)
    return {"task": None, "kind": "full", **entry}


def exit_code(result: dict) -> int:
    return 0 if result["status"] in PASSED else 1
```

- [ ] **Step 5: CLI 연결** — `orca-team/lib/orch/cli.py` `build_parser` 의 `sub.add_parser("which").add_argument("ref")` 줄 바로 앞에 넣는다.

```python
    s = sub.add_parser("test", help="[test] 명령으로 태스크 관련 테스트(quick)나 전체 테스트(--full)를 돌린다")
    s.add_argument("id", nargs="?", help="태스크 ID. 없으면 --full 로 전체 테스트만 돌린다(마지막 확인)")
    s.add_argument("--full", action="store_true", help="전체 테스트(full)")
```

`run` 의 `elif args.cmd == "review":` 앞에 넣는다.

```python
    elif args.cmd == "test":
        from . import testgate

        if args.id is None and not args.full:
            raise OrchError("태스크 ID 또는 --full 이 필요하다", 2)
        out = testgate.run_task(p, args.id, "full" if args.full else "quick") if args.id else testgate.run_final(p)
        emit(out)
        return testgate.exit_code(out)
```

- [ ] **Step 6: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_testgate -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 7: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK` (576 + 새 테스트)

- [ ] **Step 8: 커밋**

```bash
git add -- orca-team/lib/orch/testgate.py orca-team/lib/orch/cli.py orca-team/tests/helpers.py orca-team/tests/test_testgate.py
git commit -m "feat(orca-team): orch test 가 [test] 명령으로 관련·전체 테스트를 돌리고 결과를 남긴다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 2: 기획 검사 — `[test]` 형식과 묶음 규칙 (`plan.py`)

**Files:**
- Modify: `orca-team/lib/orch/plan.py` (`read_plan`, `check`, `check_only`, 새 함수)
- Test: `orca-team/tests/test_plan.py`

**Interfaces:**
- Consumes: `testgate.config_problems`, `testgate.DEVELOPER_ROLE`, `testgate.TESTER_ROLE` (Task 1).
- Produces: `plan.read_plan_data(p, rel=PLAN_FILE) -> dict`, `plan.docs_only(t: PlanTask) -> bool`, `plan.group_problems(tasks, new_keys=None) -> list[str]`. `orch plan check` 출력에 `"test": bool`.

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_plan.py` 의 `class StartReadyTest` 앞에 넣는다.

```python
TEST_TABLE = '\n[test]\nquick = "npx vitest related --run --passWithNoTests {files}"\nfull = "npx vitest run"\n'
GROUPED = [("store", "developer", ["src/store/**"], []),
           ("ui", "developer", ["src/ui/**"], ["store"]),
           ("guide", "developer", ["docs/user/**"], []),
           ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["store", "ui"])]


class GroupRuleTest(PlanCase):
    """[test] 가 있으면 developer 태스크는 tester 태스크(묶음) 하나에 들어야 한다."""

    def write(self, rows, test=True, table=TEST_TABLE):
        write_plan(self.root, "\n".join(toml_task(*row) for row in rows) + (table if test else ""))

    def check(self):
        return self.orch("plan", "check", check=False)

    def test_valid_groups_pass_and_report_test(self):
        self.write(GROUPED)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(json.loads(proc.stdout)["test"], True)

    def test_without_test_table_no_group_rules(self):
        self.write([*GROUPED, ("extra", "developer", ["src/extra/**"], [])], test=False)
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIs(json.loads(proc.stdout)["test"], False)

    def test_ungrouped_developer_rejected(self):
        self.write([*GROUPED, ("extra", "developer", ["src/extra/**"], [])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("extra: 문서 전용이 아닌 developer 태스크는 정확히 한 tester 태스크", proc.stderr)
        self.assertIn("지금: 없음", proc.stderr)

    def test_developer_in_two_groups_rejected(self):
        self.write([*GROUPED, ("grp2", "tester", ["tests/grp2/**", "docs/qa/grp2.md"], ["store"])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("store: 문서 전용이 아닌", proc.stderr)
        self.assertIn("지금: grp, grp2", proc.stderr)

    def test_tester_owns_must_match_key(self):
        rows = [*GROUPED[:3], ("grp", "tester", ["tests/**"], ["store", "ui"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn('grp: tester 태스크 owns 는 정확히 ["tests/grp/**", "docs/qa/grp.md"]', proc.stderr)

    def test_tester_depends_only_on_developers(self):
        rows = [("login-design", "designer", ["docs/design/login/**"], []), *GROUPED[:3],
                ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["store", "ui", "login-design"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("grp: tester 태스크의 depends_on 에는 developer 태스크만 둔다(login-design)", proc.stderr)

    def test_developer_must_not_depend_on_tester(self):
        rows = [*GROUPED, ("next", "developer", ["src/next/**"], ["grp"]),
                ("grp2", "tester", ["tests/grp2/**", "docs/qa/grp2.md"], ["next"])]
        self.write(rows)
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("next: developer 태스크는 tester 태스크에 의존하지 않는다(grp)", proc.stderr)

    def test_test_table_format_errors_reported(self):
        self.write(GROUPED, table='\n[test]\nquick = "npx vitest"\nfull = "npx vitest run"\n')
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test]: quick 은 {files} 를 정확히 한 번 담는다", proc.stderr)

    def test_append_checks_only_new_tasks(self):
        self.write([("old", "developer", ["src/old/**"], [])], test=False)
        self.orch("plan", "apply")
        self.write([("old", "developer", ["src/old/**"], []), ("new", "developer", ["src/new/**"], []),
                    ("grp", "tester", ["tests/grp/**", "docs/qa/grp.md"], ["new"])])
        proc = self.check()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.write([("old", "developer", ["src/old/**"], []), ("new2", "developer", ["src/new2/**"], [])])
        proc = self.check()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("new2:", proc.stderr)
        self.assertNotIn("old:", proc.stderr)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan.GroupRuleTest -v 2>&1 | tail -5`
Expected: FAIL (`KeyError: 'test'`, 묶음 오류가 나오지 않음)

- [ ] **Step 3: 구현** — `orca-team/lib/orch/plan.py`

import 를 바꾼다: `from . import comm` → `from . import comm, testgate`.

`read_plan` 을 둘로 나눈다(기존 함수 자리에).

```python
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
```

`director_problems` 함수 뒤에 넣는다.

```python
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
```

`check` 에서 `tasks = read_plan(p, cfg, rel)` 를 바꾼다.

```python
    data = read_plan_data(p, rel)
    tasks = parse_plan(data, cfg, rel)
```

`changed` 검사 뒤(`def settled` 앞)에:

```python
    if "test" in data:
        problems += testgate.config_problems(data["test"])
```

`if not graph:` 블록을 바꾼다.

```python
    if not graph:
        new_keys = {t.key for t in tasks if t.key not in applied}
        problems += director_problems(tasks, cfg, new_keys)
        if "test" in data:
            problems += group_problems(tasks, new_keys)
```

`check_only` 의 반환에 `"test"` 를 더한다.

```python
    return {"ok": True, "file": rel or PLAN_FILE, "tasks": len(tasks), "new": len(new), "roles": roles,
            "groups": parallel_groups(tasks), "test": "test" in read_plan_data(p, rel or PLAN_FILE)}
```

- [ ] **Step 4: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_plan -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 5: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/lib/orch/plan.py orca-team/tests/test_plan.py
git commit -m "feat(orca-team): plan check 가 [test] 형식과 묶음 규칙을 검사한다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 3: 리뷰 관문과 테스트·묶음 절 (`review.py`)

**Files:**
- Modify: `orca-team/lib/orch/testgate.py` (끝에 `gate_results`)
- Modify: `orca-team/lib/orch/review.py`
- Modify: `orca-team/templates/review-prompt.md`
- Create: `orca-team/templates/review-tests.md`, `orca-team/templates/review-group.md`
- Test: `orca-team/tests/test_review.py`

**Interfaces:**
- Consumes: Task 1 의 `load_config`, `load_records`, `run_task`, `code_head`, `PASSED`, `GATED_ROLES`, `TESTER_ROLE`.
- Produces:
  - `testgate.gate_results(p, task_id, role, rnd, head) -> dict | None` — `{"quick": 기록, "full": 기록(tester 이고 quick 통과일 때만)}`.
  - `review.describe(entry, kind) -> list[str]`, `review.test_section(gate) -> str`, `review.group_section(p, doc, gate) -> str`, `review.gate_failure(task_id, entry) -> str`.
  - `review.compose_prompt(p, cfg, doc, rnd, review_rel, commits, files, gate=None)`.
  - `orch review` 출력과 history 항목에 `tests: {kind: status}`(관문이 돌았을 때).

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_review.py` 위쪽 import 에 더한다(`Project` 가 이미 있으면 그 줄은 건너뛴다).

```python
from helpers import fake_test_commands, read_test_args, set_test_modes, write_test_config
from orch import testgate
from orch.paths import Project
```

`class ReviewTest` 안에 테스트 하나를 더한다(관문이 없을 때 지금 문장이 남는지).

```python
    def test_without_test_config_prompt_keeps_run_it_yourself(self):
        self.commit_work()
        self.modes("approve")
        self.review()
        prompt = sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")
        self.assertIn("리포트의 검증 출력을 그대로 믿지 말고 가능하면 직접 실행한다.", prompt)
        self.assertNotIn("## orch 테스트 결과", prompt)
        self.assertNotIn("{verify_rule}", prompt)
```

파일 끝(`if __name__` 앞)에 클래스를 더한다.

```python
class GateReviewCase(ReviewCase):
    """[test] 를 가짜 테스트 명령으로 둔 리뷰 프로젝트. 테스트 메서드가 없는 공통 부모다(상속한 테스트가 중복 실행되지 않게)."""

    def setUp(self):
        super().setUp()
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)
        self.git("add", "--", "docs/planning/tasks.toml")
        self.git("commit", "-q", "-m", "chore: test 설정")

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")


class ReviewGateTest(GateReviewCase):
    """[test] 가 있으면 orch 가 Codex 전에 quick 을 확인한다."""

    def test_quick_failure_skips_codex(self):
        self.commit_work()
        set_test_modes(self, "fail", "fail")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]), ("REQUEST_CHANGES", "orch", {"quick": "failed"}))
        self.assertEqual(self.runner_calls(), [])
        review = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        for text in ("quick: 실패", "FAIL tests/a.test.ts", "`.orchestration/bin/orch test T-001`"):
            self.assertIn(text, review)
        self.assertEqual(read_test_args(self), [["src/login.ts"], ["src/login.ts"]])
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_quick_pass_goes_to_codex_with_results(self):
        self.commit_work()
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]), ("APPROVE", "codex", {"quick": "passed"}))
        prompt = self.prompt()
        for text in ("## orch 테스트 결과", "- quick: 통과", "전체 테스트를 돌리지 않고"):
            self.assertIn(text, prompt)
        for text in ("가능하면 직접 실행", "{test_section}", "{group_section}", "## 묶음 리뷰"):
            self.assertNotIn(text, prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_flaky_is_noted(self):
        self.commit_work()
        set_test_modes(self, "fail", "pass")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["source"], "codex")
        self.assertIn("불안정 의심", self.prompt())
        self.assertIn("quick 첫 실행 로그:", self.prompt())

    def test_passing_record_for_same_code_is_reused(self):
        self.commit_work()
        self.orch("test", "T-001")
        self.commit_work("docs/tasks/T-001/report.md", "[T-001] docs: 리포트")
        self.review()
        self.assertEqual(len(read_test_args(self)), 1)

    def test_changed_code_reruns_quick(self):
        self.commit_work()
        self.orch("test", "T-001")
        self.commit_work("src/b.ts", "[T-001] feat: 더하기")
        self.review()
        self.assertEqual(read_test_args(self), [["src/login.ts"], ["src/b.ts", "src/login.ts"]])

    def test_unrunnable_stops_review_without_round(self):
        self.commit_work()
        set_test_modes(self, "missing")
        proc = self.review(check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[test] 명령을 실행할 수 없다", proc.stderr)
        st = self.state("T-001")
        self.assertEqual((st["status"], st["round"]), ("working", 1))
        self.assertNotIn("[T-001] review", self.git("log", "--format=%s"))
        self.assertEqual(self.runner_calls(), [])

    def test_non_gated_role_has_no_gate(self):
        self.assertIsNone(testgate.gate_results(Project(self.root), "T-001", "designer", 1, None))


class ReviewGroupTest(GateReviewCase):
    """T-001(developer, done)과 그것에 의존하는 묶음 테스트 T-002(tester)."""

    def setUp(self):
        super().setUp()
        self.commit_work()
        self.set_state("T-001", status="done")
        self.write_spec("T-002", role="tester", title="묶음 테스트",
                        extra="owns: tests/grp/**, docs/qa/grp.md\ndepends_on: T-001\n")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 묶음 테스트")
        self.orch("start", "T-002")
        self.commit_work("tests/grp/a.test.ts", "[T-002] test: 묶음 통합 테스트")

    def review_group(self, check=True):
        return self.orch("review", "T-002", check=check)

    def test_tester_review_runs_full_and_adds_group_section(self):
        set_test_modes(self, "pass", "fail")
        out = json.loads(self.review_group().stdout)
        self.assertEqual((out["verdict"], out["source"]), ("APPROVE", "codex"))
        self.assertEqual(out["tests"], {"quick": "passed", "full": "failed"})
        self.assertEqual(read_test_args(self)[-2:], [["tests/grp/a.test.ts"], ["--all"]])
        prompt = self.prompt()
        for text in ("## 묶음 리뷰", "T-001 [developer] 로그인 — 작업 커밋 1개, 바꾼 파일: src/login.ts",
                     "- full: 실패", "출력 끝:", "FAIL tests/a.test.ts", "제품 회귀"):
            self.assertIn(text, prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_full_record_of_same_round_is_reused(self):
        self.orch("test", "T-002", "--full")
        out = json.loads(self.review_group().stdout)
        self.assertEqual(out["tests"], {"quick": "passed", "full": "passed"})
        self.assertEqual(read_test_args(self)[-2:], [["--all"], ["tests/grp/a.test.ts"]])
        self.assertIn("- full: 통과", self.prompt())
        self.assertNotIn("출력 끝:", self.prompt())

    def test_quick_failure_skips_full(self):
        set_test_modes(self, "fail", "fail")
        out = json.loads(self.review_group().stdout)
        self.assertEqual((out["verdict"], out["source"], out["tests"]), ("REQUEST_CHANGES", "orch", {"quick": "failed"}))
        self.assertNotIn(["--all"], read_test_args(self))
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review -v 2>&1 | tail -5`
Expected: FAIL (`AttributeError: module 'orch.testgate' has no attribute 'gate_results'`, 관문 없이 Codex 호출)

- [ ] **Step 3: `testgate.gate_results`** — `orca-team/lib/orch/testgate.py` 끝에 더한다.

```python
def gate_results(p: Project, task_id: str, role: str, rnd: int, head: str | None) -> dict | None:
    """리뷰 관문 결과 {"quick": 기록, "full": 기록}. [test] 가 없거나 관문 직무가 아니면 None. 실행 불가면 exit 2.

    quick 은 같은 head(code_head)의 통과 기록을 다시 쓴다. full 은 tester 만, 같은 라운드 기록을 다시 쓴다.
    quick 이 통과하지 못하면 full 은 돌리지 않는다(어차피 돌려보낸다)."""
    if role not in GATED_ROLES or load_config(p) is None:
        return None
    records = load_records(p, task_id)
    quick = records.get("quick")
    if not (isinstance(quick, dict) and quick.get("head") == head and quick.get("status") in PASSED):
        quick = run_task(p, task_id, "quick")
    out = {"quick": quick}
    if role == TESTER_ROLE and quick["status"] in PASSED:
        full = records.get("full")
        if not (isinstance(full, dict) and full.get("round") == rnd):
            full = run_task(p, task_id, "full")
        out["full"] = full
    return out
```

- [ ] **Step 4: 템플릿 두 개 작성**

`orca-team/templates/review-tests.md`(마지막 줄 뒤 줄바꿈 하나):

```markdown
## orch 테스트 결과
orch 가 `docs/planning/tasks.toml` 의 `[test]` 명령으로 이 태스크가 바꾼 파일과 관련된 테스트(quick)를 돌렸다.
{test_lines}
```

`orca-team/templates/review-group.md`(마지막 줄 뒤 줄바꿈 하나):

```markdown
## 묶음 리뷰
이 태스크는 묶음 `{group_key}` 의 테스트 태스크다. 태스크 하나가 아니라 묶음 전체를 본다: 기능끼리 `docs/planning/contracts.md` 계약이 맞는지, 서로 부딪히는 동작, 빠진 연결.

묶음의 개발 태스크(diff 는 각 태스크의 `[T-xxx]` 커밋을 `git show` 로 본다):
{group_tasks}

orch 전체 테스트(full) 결과:
{full_lines}

{full_tail}full 실패는 나눠 적는다.
- 테스트 코드의 잘못(이 태스크 산출물): `## 지적 사항` 의 P1.
- 제품 회귀: `## 회귀` 에 실패한 테스트 이름, 원인으로 보이는 태스크 ID, 근거를 적는다. 이 태스크 판정을 막지 않는다.
- 실행 때 커밋 안 된 다른 파일 탓으로 보이는 실패: `## 회귀` 에 "작업 중 파일 의심"으로 적는다.
```

- [ ] **Step 5: 리뷰 프롬프트 두 곳 바꾸기** — `orca-team/templates/review-prompt.md`

`1. spec 의 Acceptance 를 실제로 충족하는가. 리포트의 검증 출력을 그대로 믿지 말고 가능하면 직접 실행한다.` 를 다음으로 바꾼다.

```markdown
1. spec 의 Acceptance 를 실제로 충족하는가. {verify_rule}
```

`{design_section}{text_section}## REVIEW_FILE 형식` 을 다음으로 바꾼다.

```markdown
{test_section}{group_section}{design_section}{text_section}## REVIEW_FILE 형식
```

- [ ] **Step 6: `review.py` 구현**

import 를 바꾼다.

```python
from . import orca, plan, testgate
```

```python
from .tasks import (ACTIVE, TaskDoc, all_states, deps_of, load_doc, load_state, must_state, owns_of, review_pid,
                    save_state)
```

`bullets` 함수 뒤에 넣는다.

```python
VERIFY_DEFAULT = "리포트의 검증 출력을 그대로 믿지 말고 가능하면 직접 실행한다."
VERIFY_GATED = ("테스트는 아래 orch 테스트 결과를 쓴다. 전체 테스트를 돌리지 않고, 결과가 의심되면 그 테스트 하나만 "
                "직접 돌린다. 빌드·화면 확인은 직접 해도 된다.")
STATUS_TEXT = {"passed": "통과", "flaky": "통과(불안정 의심: 첫 실행은 실패했고 다시 실행해 통과했다)",
               "skipped": "돌리지 않음(관련 파일 없음)", "failed": "실패", "timeout": "시간 초과"}
DIRTY_SHOWN = 20
GROUP_FILES_SHOWN = 15


def describe(entry: dict, kind: str) -> list[str]:
    """테스트 기록 한 건을 프롬프트·리뷰 파일용 줄로."""
    head = f"{kind}: {STATUS_TEXT.get(entry.get('status'), entry.get('status'))}"
    if entry.get("command"):
        head += f" ({entry.get('seconds')}초, exit {entry.get('exit')})"
    lines = [head]
    if entry.get("command"):
        lines.append(f"{kind} 명령: `{entry['command']}`")
    if entry.get("log"):
        lines.append(f"{kind} 로그: {entry['log']}")
    first = entry.get("first")
    if isinstance(first, dict) and first.get("log"):
        lines.append(f"{kind} 첫 실행 로그: {first['log']}")
    dirty = entry.get("dirty") or []
    if dirty:
        more = f" 외 {len(dirty) - DIRTY_SHOWN}개" if len(dirty) > DIRTY_SHOWN else ""
        lines.append("실행 때 커밋 안 된 다른 파일: " + ", ".join(dirty[:DIRTY_SHOWN]) + more)
    return lines


def test_section(gate: dict | None) -> str:
    """관문이 돌았으면 quick 결과 절(templates/review-tests.md). 다음 절과 사이의 빈 줄까지 붙인다."""
    if not gate:
        return ""
    return render((TEMPLATES / "review-tests.md").read_text(encoding="utf-8"),
                  {"test_lines": bullets(describe(gate["quick"], "quick"))}) + "\n"


def group_rows(p: Project, doc: TaskDoc) -> list[str]:
    rows = []
    for dep in deps_of(doc):
        st = load_state(p, dep) or {}
        commits = task_commits(p.root, dep, st["base"]) if st.get("base") else []
        files = changed_files(p.root, [sha for sha, _ in commits])
        shown = ", ".join(files[:GROUP_FILES_SHOWN]) or "없음"
        more = f" 외 {len(files) - GROUP_FILES_SHOWN}개" if len(files) > GROUP_FILES_SHOWN else ""
        rows.append(f"{dep} [{st.get('role', '?')}] {st.get('title', '')} — 작업 커밋 {len(commits)}개, "
                    f"바꾼 파일: {shown}{more}")
    return rows


def group_section(p: Project, doc: TaskDoc, gate: dict | None) -> str:
    """묶음 테스트(tester) 리뷰 절(templates/review-group.md). full 결과가 없으면 빈 문자열."""
    if not gate or "full" not in gate:
        return ""
    full = gate["full"]
    failed = full.get("status") not in testgate.PASSED and full.get("tail")
    tail = f"출력 끝:\n```\n{full['tail']}\n```\n" if failed else ""
    st = load_state(p, doc.id) or {}
    return render((TEMPLATES / "review-group.md").read_text(encoding="utf-8"), {
        "group_key": st.get("plan_key") or doc.id, "group_tasks": bullets(group_rows(p, doc)),
        "full_lines": bullets(describe(full, "full")), "full_tail": tail}) + "\n"


def gate_failure(task_id: str, entry: dict) -> str:
    """quick 이 통과하지 못했을 때 orch 사전 검사 리뷰 본문."""
    lines = ["orch 테스트 관문: quick 이 통과하지 못했다.", "", *(f"- {line}" for line in describe(entry, "quick"))]
    if entry.get("tail"):
        lines += ["", "출력 끝:", "```", entry["tail"], "```"]
    lines += ["", f"고친 뒤 `.orchestration/bin/orch test {task_id}` 로 다시 확인한다."]
    return "\n".join(lines)
```

`compose_prompt` 를 바꾼다.

```python
def compose_prompt(p: Project, cfg: Config, doc: TaskDoc, rnd: int, review_rel: str,
                   commits: list[tuple[str, str]], files: list[str], gate: dict | None = None) -> str:
    prior = [f"docs/reviews/{doc.id}/r{n}.md" for n in range(1, rnd)
             if (p.root / f"docs/reviews/{doc.id}/r{n}.md").is_file()]
    shas = [sha for sha, _ in commits]
    return render((TEMPLATES / "review-prompt.md").read_text(encoding="utf-8"), {
        "task_id": doc.id, "round": rnd, "role": doc.role, "role_title": cfg.title(doc.role), "title": doc.title,
        "review_path": review_rel, "prior_reviews": ", ".join(prior) or "없음",
        "commits": "\n".join(f"- {sha[:10]} {subject}" for sha, subject in commits),
        "files": "\n".join(f"- {f}" for f in files),
        "verify_rule": VERIFY_GATED if gate else VERIFY_DEFAULT,
        "test_section": test_section(gate),
        "group_section": group_section(p, doc, gate),
        "design_section": design_section(p, cfg, doc.role, files, shas),
        "text_section": text_section(p, files, shas)})
```

`run_review` 에서 `untracked: list[str] = []` 줄 뒤에 `gate = None` 을 더하고, `plan_problem` 분기의 `else:` 를 바꾼다.

```python
            else:
                gate = testgate.gate_results(p, task_id, doc.role, rnd, testgate.code_head(p, task_id, commits))
                if gate and gate["quick"]["status"] not in testgate.PASSED:
                    verdict, source = auto_review(p, review_rel, task_id, rnd, gate_failure(task_id, gate["quick"])), "orch"
                else:
                    prompt = compose_prompt(p, cfg, doc, rnd, review_rel, commits, files, gate)
                    (verdict, untracked), source = codex_review(p, cfg, doc, rnd, review_rel, prompt), "codex"
```

`result = {...}` 줄 뒤(`if untracked:` 앞)에 더한다.

```python
    if gate:
        entry["tests"] = result["tests"] = {kind: rec["status"] for kind, rec in gate.items()}
```

- [ ] **Step 7: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review test_templates -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 8: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/lib/orch/testgate.py orca-team/lib/orch/review.py orca-team/templates/review-prompt.md orca-team/templates/review-tests.md orca-team/templates/review-group.md orca-team/tests/test_review.py
git commit -m "feat(orca-team): 리뷰가 Codex 전에 quick 관문을 돌리고 묶음 테스트 리뷰에 full 결과를 넣는다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 4: 수렴형 리뷰 — 등급, 라운드 범위, 판정 맞추기

**Files:**
- Modify: `orca-team/templates/review-prompt.md` (전체 교체)
- Create: `orca-team/templates/review-round.md`
- Modify: `orca-team/lib/orch/review.py`
- Modify: `orca-team/lib/orch/tasks.py` (`REVIEW_LINE`)
- Modify: `orca-team/tests/fakes/codex-run.sh` (`body` 모드)
- Test: `orca-team/tests/test_review.py`

**Interfaces:**
- Consumes: Task 3 의 `compose_prompt(..., gate)`, `run_review` 구조.
- Produces:
  - history review 항목의 `head`(그 리뷰가 본 태스크 마지막 작업 커밋, 없으면 `None`).
  - `review.last_codex_review(history) -> dict | None`, `review.round_section(doc, rnd, commits, history) -> str`.
  - `review.p1_count(text) -> int | None`, `review.align_verdict(path, verdict) -> str | None`(바꿨으면 `"원래→바꾼 것"`).
  - `orch review` 출력·history 의 `verdict_adjusted`.
  - `compose_prompt(p, cfg, doc, rnd, review_rel, commits, files, gate=None, history=())`.
- 리뷰 파일 형식(설계 §5.5): `## 판정 근거`, `## 이전 지적 확인`, `## 지적 사항`, `## 후속`, `## 기획 누락`, `## 회귀`, `## 직접 확인한 것`, 마지막 줄 `VERDICT: …`. Task 5 가 이 절 이름을 읽는다.

- [ ] **Step 1: 가짜 Codex 에 `body` 모드** — `orca-team/tests/fakes/codex-run.sh` 의 docstring `모드:` 목록 끝에 `| body` 를 더하고, 그 아래에 줄을 더한다.

```text
body: $FAKE_DIR/review_body.md 를 리뷰 파일로 그대로 쓴다(판정 줄 포함).
```

`review.write_text("# 리뷰\n\n없음\n" + ...)` 줄 바로 뒤에 넣는다.

```python
    if mode == "body":
        review.write_text((fake / "review_body.md").read_text(encoding="utf-8"), encoding="utf-8")
```

- [ ] **Step 2: 실패하는 테스트 작성** — `orca-team/tests/test_review.py` 끝(`if __name__` 앞)에 더한다.

```python
def review_body(findings: str, verdict: str, rnd: int = 1) -> str:
    return (f"# T-001 r{rnd} 리뷰\n## 판정 근거\n근거\n## 이전 지적 확인\n- 해당 없음\n## 지적 사항\n{findings}\n"
            "## 후속\n- 없음\n## 기획 누락\n- 없음\n## 회귀\n- 해당 없음\n## 직접 확인한 것\n- 읽음\n\n"
            f"VERDICT: {verdict}\n")


class ReviewRoundTest(ReviewCase):
    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def round_part(self, prompt):
        start = prompt.index("## 라운드 2 확인 범위")
        end = prompt.index("\n## ", start + 1)
        return prompt[start:end]

    def body(self, text):
        (self.fake_dir / "review_body.md").write_text(text, encoding="utf-8")

    def test_round_one_has_no_round_section_and_new_format(self):
        self.commit_work()
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 라운드", prompt)
        self.assertNotIn("{round_section}", prompt)
        for text in ("## 등급과 판정", "P1(막는다)", "## 후속", "## 기획 누락", "## 회귀", "## 이전 지적 확인",
                     "r1-F1 [P1]", "orch 는 `## 지적 사항` 의 `[P1]` 줄 수로 판정을 맞춘다"):
            self.assertIn(text, prompt)
        self.assertNotIn("P1 이 하나라도 있으면", prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_round_two_lists_only_new_commits_and_records_head(self):
        self.commit_work()
        first = self.git("rev-parse", "HEAD").strip()
        self.modes("changes", "approve")
        self.review()
        self.assertEqual(self.state("T-001")["history"][-1]["head"], first)
        self.orch("fix", "T-001")
        self.commit_work("src/b.ts", "[T-001] fix: 리뷰 반영")
        self.review()
        part = self.round_part(self.prompt())
        self.assertIn("[T-001] fix: 리뷰 반영", part)
        self.assertNotIn("[T-001] feat: 로그인", part)
        self.assertIn(f"`{first[:10]}`", part)
        self.assertIn("`r1-F1: 해결`", part)
        self.assertIn("docs/reviews/T-001/r1.md", part)

    def test_orch_only_prior_round_gets_full_review(self):
        self.review()  # 작업 커밋 없음 → orch 사전 검사
        self.orch("fix", "T-001")
        self.commit_work()
        self.review()
        self.assertNotIn("## 라운드", self.prompt())

    def test_missing_head_in_old_history_lists_all(self):
        self.commit_work()
        self.modes("changes", "approve")
        self.review()
        history = self.state("T-001")["history"]
        for entry in history:
            entry.pop("head", None)
        self.set_state("T-001", history=history)
        self.orch("fix", "T-001")
        self.commit_work("src/b.ts", "[T-001] fix: 리뷰 반영")
        self.review()
        self.assertIn("커밋 기록이 없다", self.round_part(self.prompt()))

    def test_request_changes_without_p1_becomes_approve(self):
        self.commit_work()
        self.body(review_body("- 없음", "REQUEST_CHANGES"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["verdict_adjusted"]), ("APPROVE", "REQUEST_CHANGES→APPROVE"))
        self.assertEqual(self.state("T-001")["status"], "approved")
        self.assertEqual(self.state("T-001")["history"][-1]["verdict_adjusted"], "REQUEST_CHANGES→APPROVE")
        text = (self.root / "docs/reviews/T-001/r1.md").read_text(encoding="utf-8")
        self.assertTrue(text.rstrip().endswith("VERDICT: APPROVE"))
        self.assertIn("orch 판정 맞추기", text)
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "[T-001] review r1: APPROVE")
        self.assertIn("판정 맞추기: REQUEST_CHANGES→APPROVE", self.git("log", "-1", "--format=%B"))

    def test_approve_with_p1_becomes_request_changes(self):
        self.commit_work()
        self.body(review_body("- r1-F1 [P1] src/login.ts:1 — 입력 검증이 없다. 검증한다.", "APPROVE"))
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual((out["verdict"], out["verdict_adjusted"]), ("REQUEST_CHANGES", "APPROVE→REQUEST_CHANGES"))
        self.assertEqual(self.state("T-001")["status"], "changes")

    def test_old_format_is_not_adjusted(self):
        self.commit_work()
        self.body("# 리뷰\n지적 없음\n\nVERDICT: REQUEST_CHANGES\n")
        self.modes("body")
        out = json.loads(self.review().stdout)
        self.assertEqual(out["verdict"], "REQUEST_CHANGES")
        self.assertNotIn("verdict_adjusted", out)

    def test_fix_spec_tells_worker_to_fix_only_blocking(self):
        self.commit_work()
        self.modes("changes")
        self.review()
        self.orch("fix", "T-001")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("막는 지적(`## 지적 사항` 의 P1, orch 사전 검사 사유)을 모두 고친다", spec)
        self.assertIn("`## 후속`·`## 기획 누락`·`## 회귀` 는 고치지 않는다", spec)
```

- [ ] **Step 3: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review.ReviewRoundTest -v 2>&1 | tail -5`
Expected: FAIL

- [ ] **Step 4: 리뷰 프롬프트 전체 교체** — `orca-team/templates/review-prompt.md` 를 다음 내용으로 바꾼다(마지막 줄 뒤 줄바꿈 하나).

```markdown
너는 orca-team 의 {role_title}({role}) 리뷰어다. 아래 태스크 결과를 리뷰하고 판정한다.

<execution_policy>
- 리뷰만 한다. REVIEW_FILE 하나만 쓴다. 다른 파일을 만들거나 고치거나 지우지 않는다.
- git add·commit·stash·reset·checkout 을 하지 않는다. 같은 체크아웃에서 다른 워커가 동시에 일한다.
- 확인은 읽기 명령과 테스트·빌드 실행으로 한다. 추적 안 된 산출물은 괜찮지만 추적 파일 내용을 바꾸면 판정이 무효가 된다.
- 질문이나 계획으로 끝내지 않는다. 리뷰 파일을 다 쓰고 판정까지 마친다.
</execution_policy>

REVIEW_FILE: {review_path}

## 태스크
- {task_id} 라운드 {round} — [{role}] {title}
- spec: `.orchestration/tasks/{task_id}.md`
- 직무 정의: `.orchestration/roles/{role}.md`
- 워커 리포트: `docs/tasks/{task_id}/report.md`
- 이전 리뷰: {prior_reviews}

## 리뷰 대상 커밋
{commits}

변경 파일:
{files}

diff 는 `git show <sha>` 로 본다.

## 기준
1. spec 의 Acceptance 를 실제로 충족하는가. {verify_rule}
2. 직무 정의의 품질 기준과 도구 규칙(앱·웹 화면은 Claude Design 소스 `docs/design/`, 래스터 에셋은 `assets/`)을 지키는가.
3. 버그, 보안, 회귀 위험.
4. 수정 라운드면 이전 리뷰의 P1 지적이 모두 반영됐는가.

## 등급과 판정
- P1(막는다): 수용 기준 미충족, 재현되는 동작 버그, 보안 문제, 데이터 손실, `docs/planning/contracts.md` 위반, orch 테스트 실패, 아래 디자인·문구 기준이 P1 으로 정한 것.
- P2·P3(막지 않는다): 고치면 좋은 것, 유지보수, 스타일, 문서. `## 후속` 에 적는다.
- 기획 누락(막지 않는다): spec·`docs/planning/contracts.md`·Acceptance 에 없는 요구. 코드 결함으로 지적하지 않는다. `## 기획 누락` 에 무엇이 없고 왜 필요한지 적는다.
- 지적은 이번 라운드에 모두 낸다. 지적마다 `r{round}-F1`, `r{round}-F2` 처럼 번호를 붙인다. 절이 달라도 번호는 이어서 센다.
- P1 이 있으면 REQUEST_CHANGES, 없으면 APPROVE 다. orch 는 `## 지적 사항` 의 `[P1]` 줄 수로 판정을 맞춘다.

{test_section}{round_section}{group_section}{design_section}{text_section}## REVIEW_FILE 형식
# {task_id} r{round} 리뷰
## 판정 근거
## 이전 지적 확인
- r1-F1: 해결 (라운드 1이면 "해당 없음")
## 지적 사항
- r{round}-F1 [P1] 파일:줄 — 문제. 고칠 방향. (없으면 "없음")
## 후속
- r{round}-F2 [P2|P3] 파일:줄 — 문제. 고칠 방향. (없으면 "없음")
## 기획 누락
- r{round}-F3 무엇이 spec·contracts·Acceptance 에 없는가. 왜 필요한가. (없으면 "없음")
## 회귀
- 실패한 테스트 — 원인으로 보이는 태스크 ID. 근거. (묶음 리뷰만. 아니면 "해당 없음")
## 직접 확인한 것
- 실행한 명령과 결과 요약

마지막 줄은 정확히 둘 중 하나다:
VERDICT: APPROVE
VERDICT: REQUEST_CHANGES
```

- [ ] **Step 5: 라운드 절 템플릿** — `orca-team/templates/review-round.md`(마지막 줄 뒤 줄바꿈 하나):

```markdown
## 라운드 {round} 확인 범위
지난 Codex 리뷰는 r{prev}(`{prev_review}`)다. 그 리뷰가 본 마지막 커밋은 {prev_head} 다. 그 뒤 새 커밋:
{new_commits}

이번 라운드는 세 가지만 본다.
1. r{prev} 의 P1 지적마다 해결했는지. `## 이전 지적 확인` 에 `r{prev}-F1: 해결` 또는 `r{prev}-F1: 미해결 — 근거` 로 적는다. 미해결은 이번 라운드 `## 지적 사항` 에 P1 으로 다시 적는다.
2. 새 커밋이 새 P1 을 만들었는지.
3. r{prev} 이후 바뀌지 않은 코드에서 새로 찾은 문제: 수용 기준 미충족·보안·데이터 손실만 P1 으로 적고 끝에 "(r{prev}에서 놓침)"을 붙인다. 나머지는 `## 후속` 에 적는다.
r{prev} 의 P2·P3 를 고치지 않은 것은 지적하지 않는다.
```

- [ ] **Step 6: `review.py` 구현**

`VERDICT_RE` 아래에 정규식을 더한다.

```python
FINDINGS_RE = re.compile(r"^## 지적 사항[ \t]*\n(.*?)(?=^## |^VERDICT:|\Z)", re.M | re.S)
```

`gate_failure` 뒤에 넣는다.

```python
def last_codex_review(history) -> dict | None:
    """가장 최근의 Codex 리뷰 기록. orch 사전 검사 기록은 건너뛴다."""
    for entry in reversed(list(history)):
        if entry.get("stage") == "review" and entry.get("source") == "codex":
            return entry
    return None


def round_section(doc: TaskDoc, rnd: int, commits: list[tuple[str, str]], history) -> str:
    """라운드 2부터 좁혀 보는 범위(templates/review-round.md). 앞 라운드에 Codex 리뷰가 없으면 빈 문자열(전체를 본다)."""
    prev = last_codex_review(history)
    if rnd <= 1 or prev is None:
        return ""
    shas = [sha for sha, _ in commits]
    prev_head = prev.get("head")
    if prev_head in shas:
        new = commits[shas.index(prev_head) + 1:]
        new_commits = "\n".join(f"- {sha[:10]} {subject}" for sha, subject in new) or "- 없음(새 커밋이 없다)"
        head_text = f"`{prev_head[:10]}`"
    else:
        new_commits = "- 지난 리뷰의 커밋 기록이 없다. 위 리뷰 대상 커밋 전체를 본다"
        head_text = "기록 없음"
    prev_rnd = prev.get("round")
    return render((TEMPLATES / "review-round.md").read_text(encoding="utf-8"), {
        "round": rnd, "prev": prev_rnd, "prev_review": f"docs/reviews/{doc.id}/r{prev_rnd}.md",
        "prev_head": head_text, "new_commits": new_commits}) + "\n"


def p1_count(text: str) -> int | None:
    """`## 지적 사항` 절의 `[P1]` 항목 수. 절이 없으면(이전 형식) None."""
    match = FINDINGS_RE.search(text)
    if not match:
        return None
    return sum(1 for line in match.group(1).splitlines() if line.lstrip().startswith("- ") and "[P1]" in line)


def align_verdict(path: Path, verdict: str) -> str | None:
    """P1 이 있으면 REQUEST_CHANGES, 없으면 APPROVE 로 판정을 맞춘다. 바꿨으면 "원래→바꾼 것", 아니면 None."""
    text = path.read_text(encoding="utf-8")
    count = p1_count(text)
    if count is None:
        return None
    want = "REQUEST_CHANGES" if count else "APPROVE"
    if want == verdict:
        return None
    last = list(VERDICT_RE.finditer(text))[-1]
    note = f"> orch 판정 맞추기: `## 지적 사항` 의 P1 {count}개라 {want} 로 바꿨다(원래 {verdict}).\n\n"
    path.write_text(text[:last.start()] + note + f"VERDICT: {want}" + text[last.end():], encoding="utf-8")
    return f"{verdict}→{want}"
```

`compose_prompt` 시그니처에 `history` 를 더한다.

```python
def compose_prompt(p: Project, cfg: Config, doc: TaskDoc, rnd: int, review_rel: str,
                   commits: list[tuple[str, str]], files: list[str], gate: dict | None = None,
                   history=()) -> str:
```

render 값 dict 의 `"test_section": test_section(gate),` 뒤에:

```python
        "round_section": round_section(doc, rnd, commits, history),
```

`run_review`:
- `gate = None` 뒤에 `commits: list[tuple[str, str]] = []` 와 `adjusted = None` 을 더한다.
- `compose_prompt(...)` 호출을 `compose_prompt(p, cfg, doc, rnd, review_rel, commits, files, gate, st["history"])` 로 바꾼다.
- `codex_review` 호출 줄 바로 뒤(같은 `else:` 블록 안)에 넣는다.

```python
                    adjusted = align_verdict(p.root / review_rel, verdict)
                    if adjusted:
                        verdict = adjusted.split("→")[1]
```

- `message = f"[{task_id}] review r{rnd}: {verdict}"` 뒤에 넣는다(제목 줄은 바꾸지 않는다. `task_commits` 가 제목으로 리뷰 커밋을 가른다).

```python
        if adjusted:
            message += f"\n\n판정 맞추기: {adjusted}(`## 지적 사항` 의 P1 수 기준)"
```

- `entry = {...}` 에 `"head"` 를 더한다.

```python
    entry = {"stage": "review", "round": rnd, "at": now(), "verdict": verdict, "source": source,
             "head": commits[-1][0] if commits else None}
```

- `if gate:` 블록 뒤에:

```python
    if adjusted:
        entry["verdict_adjusted"] = result["verdict_adjusted"] = adjusted
```

- [ ] **Step 7: 수정 워커 지시 바꾸기** — `orca-team/lib/orch/tasks.py` 의 `REVIEW_LINE` 을 바꾼다.

```python
REVIEW_LINE = ("`{review}` 리뷰를 읽고 막는 지적(`## 지적 사항` 의 P1, orch 사전 검사 사유)을 모두 고친다. "
               "고치지 않는 항목은 리포트에 이유를 적는다. `## 후속`·`## 기획 누락`·`## 회귀` 는 고치지 않는다"
               "(코디네이터가 따로 태스크로 만든다).\n")
```

- [ ] **Step 8: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review test_tasks test_templates -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 9: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/templates/review-prompt.md orca-team/templates/review-round.md orca-team/lib/orch/review.py orca-team/lib/orch/tasks.py orca-team/tests/fakes/codex-run.sh orca-team/tests/test_review.py
git commit -m "feat(orca-team): 리뷰가 라운드 2부터 이전 지적과 새 커밋만 보고 P1 수로 판정을 맞춘다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 5: 후속 모으기 `orch followups`

**Files:**
- Create: `orca-team/lib/orch/followups.py`
- Modify: `orca-team/lib/orch/cli.py`
- Test: `orca-team/tests/test_followups.py` (새)

**Interfaces:**
- Consumes: Task 4 의 리뷰 파일 절 이름, `testgate.DEVELOPER_ROLE`·`TESTER_ROLE`.
- Produces: `followups.followups(p, task_id=None, rest=False) -> dict` — `{"tasks": [...], "regressions": [...], "followups": [...], "planning_gaps": [...]}`. 항목: `{"task", "round", "id", "text"}`. `followups.SOURCE_RE`(spec Change 첫 줄 `출처: (T-xxx|rest|final) followups`).

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_followups.py`

```python
import json
import unittest

from test_tasks import TaskCase

REVIEW = """# T-001 r2 리뷰
## 판정 근거
근거
## 이전 지적 확인
- r1-F1: 해결
## 지적 사항
- 없음
## 후속
- r2-F2 [P2] src/a.ts:3 — 이름이 모호하다. 바꾼다.
  둘째 줄 설명
- r2-F3 [P3] src/a.ts:9 — 주석 오타.
## 기획 누락
- r2-F4 빈 목록일 때 문구가 spec 에 없다. 빈 상태가 필요하다.
## 회귀
- 해당 없음
## 직접 확인한 것
- 읽음

VERDICT: APPROVE
"""
OLD_R1 = "# T-001 r1 리뷰\n## 지적 사항\n- 없음\n## 후속\n- r1-F9 [P2] 옛 지적\n\nVERDICT: REQUEST_CHANGES\n"
TESTER_REVIEW = """# T-003 r1 리뷰
## 지적 사항
- 없음
## 후속
- 없음
## 기획 누락
- 없음
## 회귀
- tests/grp/a.test.ts > 합계 — T-002. 합계가 틀린다.

VERDICT: APPROVE
"""
DERIVED_SPEC = """---
id: {id}
role: developer
title: 회귀 수정
owns: src/**
---
## Target
src/

## Change
출처: T-003 followups
- 합계 고치기

## Constraints
없음

## Acceptance
- orch test 통과
"""


class FollowupsTest(TaskCase):
    def put_state(self, task_id, role, status="done", depends_on=(), rounds=(1,)):
        st = {"id": task_id, "role": role, "title": task_id, "status": status, "round": max(rounds),
              "owns": [], "depends_on": list(depends_on), "history": [],
              "verdicts": {str(r): "APPROVE" for r in rounds}}
        path = self.root / f".orchestration/state/tasks/{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(st), encoding="utf-8")

    def put_review(self, task_id, rnd, text):
        path = self.root / f"docs/reviews/{task_id}/r{rnd}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def followups(self, *args, check=True):
        proc = self.orch("followups", *args, check=check)
        return json.loads(proc.stdout) if check else proc

    def group(self):
        self.put_state("T-001", "developer", rounds=(1, 2))
        self.put_review("T-001", 1, OLD_R1)
        self.put_review("T-001", 2, REVIEW)
        self.put_state("T-002", "developer")
        self.put_review("T-002", 1, REVIEW.replace("r2-", "r1-"))
        self.put_state("T-003", "tester", depends_on=("T-001", "T-002"))
        self.put_review("T-003", 1, TESTER_REVIEW)

    def test_group_collects_latest_round_of_members(self):
        self.group()
        out = self.followups("T-003")
        self.assertEqual(out["tasks"], ["T-001", "T-002", "T-003"])
        self.assertEqual([(f["task"], f["round"], f["id"]) for f in out["followups"]],
                         [("T-001", 2, "r2-F2"), ("T-001", 2, "r2-F3"), ("T-002", 1, "r1-F2"), ("T-002", 1, "r1-F3")])
        self.assertIn("이름이 모호하다. 바꾼다. 둘째 줄 설명", out["followups"][0]["text"])
        self.assertEqual([(g["task"], g["id"]) for g in out["planning_gaps"]], [("T-001", "r2-F4"), ("T-002", "r1-F4")])
        self.assertEqual(out["regressions"], [{"task": "T-003", "round": 1, "id": None,
                                               "text": "tests/grp/a.test.ts > 합계 — T-002. 합계가 틀린다."}])
        self.assertNotIn("r1-F9", json.dumps(out, ensure_ascii=False))

    def test_non_tester_task_is_alone(self):
        self.group()
        self.assertEqual(self.followups("T-001")["tasks"], ["T-001"])

    def test_rest_only_done_ungrouped_developers(self):
        self.group()
        self.put_state("T-004", "developer")
        self.put_state("T-005", "developer", status="working")
        self.put_state("T-006", "designer")
        self.assertEqual(self.followups("--rest")["tasks"], ["T-004"])

    def test_followup_derived_task_skips_followups(self):
        self.put_state("T-004", "developer")
        self.put_review("T-004", 1, REVIEW.replace("r2-", "r1-"))
        (self.root / ".orchestration/tasks/T-004.md").write_text(DERIVED_SPEC.format(id="T-004"), encoding="utf-8")
        out = self.followups("--rest")
        self.assertEqual(out["tasks"], ["T-004"])
        self.assertEqual(out["followups"], [])
        self.assertEqual(len(out["planning_gaps"]), 1)

    def test_old_format_review_gives_nothing(self):
        self.put_state("T-001", "developer")
        self.put_review("T-001", 1, "# 리뷰\n\n없음\n\nVERDICT: APPROVE\n")
        out = self.followups("T-001")
        self.assertEqual((out["followups"], out["planning_gaps"], out["regressions"]), ([], [], []))

    def test_argument_rules(self):
        self.assertEqual(self.followups(check=False).returncode, 2)
        self.assertEqual(self.followups("T-001", "--rest", check=False).returncode, 2)
        self.assertEqual(self.followups("T-099", check=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_followups -v 2>&1 | tail -5`
Expected: FAIL (`invalid choice: 'followups'`)

- [ ] **Step 3: `orca-team/lib/orch/followups.py` 작성**

```python
"""리뷰 파일의 `## 후속`·`## 기획 누락`·`## 회귀` 항목을 모은다(읽기 전용).
코디네이터가 이것으로 회귀 수정·묶음 정리 태스크와 사용자 질문을 만든다."""
from __future__ import annotations

import re
from pathlib import Path

from .paths import OrchError, Project
from .tasks import TASK_ID_RE, all_states, load_state, must_state, parse_task, task_path
from .testgate import DEVELOPER_ROLE, TESTER_ROLE

SECTIONS = (("regressions", "회귀"), ("followups", "후속"), ("planning_gaps", "기획 누락"))
EMPTY = {"없음", "해당 없음", "(없음)", "(해당 없음)"}
ITEM_ID_RE = re.compile(r"^(r\d+-F\d+)\b")
SOURCE_RE = re.compile(r"^출처: (T-\d{3,}|rest|final) followups\s*$")


def section_items(text: str, title: str) -> list[str]:
    """`## {title}` 절의 `- ` 항목. 들여 쓴 다음 줄은 앞 항목에 잇는다. 없음·해당 없음은 뺀다."""
    match = re.search(rf"^## {re.escape(title)}[ \t]*\n(.*?)(?=^## |^VERDICT:|\Z)", text, re.M | re.S)
    if not match:
        return []
    items: list[str] = []
    for line in match.group(1).splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items and line[:1] in (" ", "\t") and line.strip():
            items[-1] += " " + line.strip()
    return [item for item in items if item and item.rstrip(".") not in EMPTY]


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
    """후속 처리로 만든 태스크인가(spec `## Change` 첫 줄이 `출처: … followups`)."""
    path = task_path(p, task_id)
    if not path.is_file():
        return False
    lines = [line.strip() for line in parse_task(path).sections.get("Change", "").splitlines() if line.strip()]
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
```

- [ ] **Step 4: CLI 연결** — `orca-team/lib/orch/cli.py` `build_parser` 의 `test` 파서 뒤에:

```python
    s = sub.add_parser("followups", help="묶음(또는 묶음 밖) 리뷰의 회귀·후속·기획 누락을 모은다(코디네이터, 읽기 전용)")
    s.add_argument("id", nargs="?", help="tester 태스크 ID(그 묶음) 또는 태스크 ID")
    s.add_argument("--rest", action="store_true", help="어떤 묶음에도 들지 않은 done developer 태스크")
```

`run` 의 `elif args.cmd == "test":` 블록 뒤에:

```python
    elif args.cmd == "followups":
        from . import followups

        if bool(args.id) == bool(args.rest):
            raise OrchError("태스크 ID 와 --rest 중 하나만 준다", 2)
        emit(followups.followups(p, args.id, args.rest))
```

- [ ] **Step 5: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_followups -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 6: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/lib/orch/followups.py orca-team/lib/orch/cli.py orca-team/tests/test_followups.py
git commit -m "feat(orca-team): orch followups 가 묶음 리뷰의 회귀·후속·기획 누락을 모은다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 6: 워커 spec 테스트 절 `{test_block}`

**Files:**
- Modify: `orca-team/lib/orch/tasks.py` (`compose_spec`, 새 상수·함수)
- Modify: `orca-team/templates/worker-spec.md`
- Test: `orca-team/tests/test_tasks.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: `testgate.load_config`, `GATED_ROLES`, `TESTER_ROLE`.
- Produces: `tasks.gate_block(p, doc) -> str`. 워커 spec 의 `## 테스트` 절(빈 줄로 끝난다).

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_tasks.py` 위쪽 import 에 `from helpers import fake_test_commands, write_test_config` 를 더한다(`from helpers import ProjectCase` 줄에 이어 써도 된다). `class ComposeSpecTest` 뒤에 넣는다.

```python
class TestBlockTest(TaskCase):
    def setUp(self):
        super().setUp()
        self.quick, self.full = fake_test_commands(self)
        write_test_config(self.root, self.quick, self.full)

    def spec_for(self, task_id, role, extra=""):
        self.write_spec(task_id, role=role, extra=extra)
        self.orch("start", task_id)
        return self.arg(self.last_start(), "--spec")

    def test_developer_gets_quick_rules(self):
        spec = self.spec_for("T-001", "developer")
        self.assertIn("묻는다.\n\n## 테스트\n", spec)
        self.assertIn("`.orchestration/bin/orch test T-001` 가 통과해야 한다", spec)
        self.assertIn("전체 테스트는 돌리지 않는다", spec)
        self.assertLess(spec.index("## 테스트"), spec.index("## 작업 규칙"))
        self.assertNotIn("\n\n\n", spec)
        self.assertNotIn("{test_block}", spec)

    def test_tester_gets_full_once(self):
        spec = self.spec_for("T-001", "tester", "owns: tests/grp/**, docs/qa/grp.md\n")
        self.assertIn("`.orchestration/bin/orch test T-001 --full`", spec)
        self.assertIn("제품 코드는 고치지 않는다", spec)

    def test_other_roles_and_no_config_have_no_block(self):
        self.assertNotIn("## 테스트", self.spec_for("T-001", "designer"))
        (self.root / "docs/planning/tasks.toml").unlink()
        self.assertNotIn("## 테스트", self.spec_for("T-002", "developer", "owns: src/other/**\n"))

    def test_broken_test_table_has_no_block(self):
        write_test_config(self.root, "npx vitest", self.full)
        self.assertNotIn("## 테스트", self.spec_for("T-001", "developer"))

    def test_fix_stage_keeps_spacing(self):
        self.spec_for("T-001", "developer")
        self.set_state("T-001", status="changes")
        self.orch("fix", "T-001", "--note", "P1 반영")
        spec = self.arg(self.last_start(), "--spec")
        self.assertIn("코디네이터 지시: P1 반영\n\n## 테스트\n", spec)
        self.assertNotIn("\n\n\n", spec)
```

`orca-team/tests/test_templates.py` `test_placeholders` 의 worker-spec 키 목록에 `"{test_block}"` 을 더한다.

```python
        for key in ("{role_doc}", "{task_body}", "{owns}", "{fix_block}", "{test_block}", "[{task_id}]", "worker_done"):
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_tasks.TestBlockTest test_templates -v 2>&1 | tail -5`
Expected: FAIL

- [ ] **Step 3: 구현**

`orca-team/templates/worker-spec.md`: `{fix_block}## 작업 규칙` 을 `{fix_block}{test_block}## 작업 규칙` 으로 바꾼다.

`orca-team/lib/orch/tasks.py` 의 `from . import orca` 를 `from . import orca, testgate` 로 바꾼다. `REVIEW_LINE` 뒤에 넣는다.

```python
TEST_BLOCK = """## 테스트
- worker_done 전에 `.orchestration/bin/orch test {task_id}` 가 통과해야 한다(exit 0). 이 태스크가 바꾼 파일과 관련된 테스트만 돈다. 실패하면 고치고 다시 돌린다.
- 전체 테스트는 돌리지 않는다. 묶음 테스트 태스크와 orch 가 돌린다.
- 리포트의 검증 출력에 orch test 출력의 `status`·`seconds` 를 붙인다. 실패를 고쳤으면 그 `tail` 도 붙인다.

"""
TESTER_TEST_BLOCK = """## 테스트
- 이 태스크는 묶음 테스트다. 선행 태스크(묶음 개발 태스크)의 기능을 잇는 통합 테스트를 소유 경로에 쓰고 커밋한다.
- 커밋한 뒤 `.orchestration/bin/orch test {task_id} --full` 로 전체 테스트를 한 번 돌린다. 다시 돌리지 않는다.
- 실패는 QA 리포트에서 둘로 나눈다. 테스트 코드의 잘못은 고치고 `.orchestration/bin/orch test {task_id}` 로 확인한다. 제품 회귀는 실패한 테스트 이름, 원인으로 보이는 선행 태스크 ID, 근거를 적는다. 제품 코드는 고치지 않는다.
- worker_done 전에 `.orchestration/bin/orch test {task_id}` 가 통과해야 한다(exit 0).

"""
```

`compose_spec` 앞에 넣는다.

```python
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
```

`compose_spec` 의 render 값 마지막 줄을 바꾼다.

```python
        "owns": ", ".join(owns), "fix_block": fix_block, "test_block": gate_block(p, doc)})
```

- [ ] **Step 4: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_tasks test_templates test_hooks -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 5: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/lib/orch/tasks.py orca-team/templates/worker-spec.md orca-team/tests/test_tasks.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): [test] 가 있으면 개발·테스트 워커 spec 에 테스트 절을 넣는다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 7: 기존 프로젝트 갱신 `orch upgrade`

**Files:**
- Modify: `orca-team/lib/orch/setup.py`
- Modify: `orca-team/lib/orch/cli.py`
- Test: `orca-team/tests/test_setup.py`

**Interfaces:**
- Produces: `setup.upgrade(p) -> dict` — `{"updated": [...], "created": [...], "skipped": [{"path", "reason"}], "diff_stat": str, "next": str}`. `orch upgrade`.

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_setup.py` 의 `class DoctorTest` 앞에 넣는다.

```python
class UpgradeTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.scaffold()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: setup")

    def upgrade(self):
        return json.loads(self.orch("upgrade").stdout)

    def template(self, rel):
        return (PLUGIN / "templates" / rel).read_text(encoding="utf-8")

    def commit_text(self, rel, text):
        (self.root / rel).write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", f"chore: {rel}")

    def test_same_files_change_nothing(self):
        out = self.upgrade()
        self.assertEqual((out["updated"], out["created"], out["skipped"], out["diff_stat"]), ([], [], [], ""))
        self.assertIn("/orca-team:up", out["next"])

    def test_overwrites_committed_old_copy_without_commit(self):
        self.commit_text(".orchestration/coordinator.md", "옛 지침\n")
        head = self.git("rev-parse", "HEAD").strip()
        out = self.upgrade()
        self.assertEqual(out["updated"], [".orchestration/coordinator.md"])
        self.assertEqual((self.root / ".orchestration/coordinator.md").read_text(encoding="utf-8"),
                         self.template("coordinator.md"))
        self.assertIn(".orchestration/coordinator.md", out["diff_stat"])
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)

    def test_skips_uncommitted_change(self):
        (self.root / ".orchestration/roles/tester.md").write_text("작업 중\n", encoding="utf-8")
        out = self.upgrade()
        self.assertEqual(out["skipped"], [{"path": ".orchestration/roles/tester.md",
                                           "reason": "커밋 안 된 변경이 있다. 커밋하거나 되돌린 뒤 다시 한다"}])
        self.assertEqual((self.root / ".orchestration/roles/tester.md").read_text(encoding="utf-8"), "작업 중\n")

    def test_keeps_config_toml(self):
        self.commit_text(".orchestration/config.toml", self.template("config.toml") + "# 사용자 설정\n")
        out = self.upgrade()
        self.assertTrue((self.root / ".orchestration/config.toml").read_text(encoding="utf-8").endswith("# 사용자 설정\n"))
        self.assertNotIn(".orchestration/config.toml", json.dumps(out, ensure_ascii=False))

    def test_creates_missing_file(self):
        self.git("rm", "-q", "--", ".orchestration/roles/director.md")
        self.git("commit", "-q", "-m", "chore: director 없음")
        out = self.upgrade()
        self.assertEqual(out["created"], [".orchestration/roles/director.md"])

    def test_untracked_file_skipped(self):
        self.git("rm", "-q", "--cached", "--", ".orchestration/text-rules.md")
        self.git("commit", "-q", "-m", "chore: 추적 해제")
        out = self.upgrade()
        self.assertEqual(out["skipped"][0]["path"], ".orchestration/text-rules.md")
        self.assertIn("추적되지 않는다", out["skipped"][0]["reason"])
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_setup.UpgradeTest -v 2>&1 | tail -5`
Expected: FAIL (`invalid choice: 'upgrade'`)

- [ ] **Step 3: 구현** — `orca-team/lib/orch/setup.py`

util import 에 `git`, `git_ok` 를 더한다.

```python
from .util import git, git_ok, load_run, now, pid_alive, record_plugin_root, require_coordinator, update_run, write_json
```

`scaffold` 함수 뒤에 넣는다.

```python
UPGRADE_KEEP = (".orchestration/config.toml",)  # 사용자 설정이라 덮어쓰지 않는다
UPGRADE_NEXT = ("바뀐 파일을 확인하고(git diff -- .orchestration) 커밋한다. 코디네이터는 시작할 때만 지침을 읽으므로 "
                "커밋한 뒤 코디네이터 터미널을 닫고 메인에서 /orca-team:up 으로 다시 띄운다. 테스트 관문을 쓰려면 "
                "docs/planning/tasks.toml 에 [test] 를 넣는다(README \"묶음 검증\").")


def upgrade(p: Project) -> dict:
    """프로젝트에 복사된 규칙 파일(config.toml 제외)을 이 플러그인의 템플릿으로 바꾼다.
    git 에 커밋돼 있고 커밋 안 된 변경이 없는 파일만 덮어쓰고, 없는 파일은 만든다. 커밋하지 않는다."""
    updated, created, skipped = [], [], []
    targets = [(src, dst) for src, dst in SCAFFOLD_FILES if dst not in UPGRADE_KEEP]
    for src, dst in targets:
        target = p.root / dst
        new = (TEMPLATES / src).read_bytes()
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new)
            created.append(dst)
        elif not git_ok(p.root, "ls-files", "--error-unmatch", "--", dst):
            skipped.append({"path": dst, "reason": "git 에 추적되지 않는다. 템플릿과 직접 비교해 바꾼다"})
        elif git(p.root, "status", "--porcelain", "--", dst).strip():
            skipped.append({"path": dst, "reason": "커밋 안 된 변경이 있다. 커밋하거나 되돌린 뒤 다시 한다"})
        elif target.read_bytes() != new:
            target.write_bytes(new)
            updated.append(dst)
    shim = p.root / ".orchestration/bin/orch"
    if shim.exists():
        shim.chmod(0o755)
    stat = git(p.root, "diff", "--stat", "--", *(dst for _, dst in targets), check=False)
    return {"updated": updated, "created": created, "skipped": skipped, "diff_stat": stat.strip(), "next": UPGRADE_NEXT}
```

`orca-team/lib/orch/cli.py` `build_parser` 의 `sub.add_parser("setup-done", help="세팅 모드 해제")` 뒤에:

```python
    sub.add_parser("upgrade", help="복사된 규칙 파일을 이 플러그인 버전 템플릿으로 바꾼다(config.toml 제외, 커밋 안 함)")
```

`run` 의 `if args.cmd in ("setup-done", "up", "bind", "status"):` 를 `if args.cmd in ("setup-done", "up", "bind", "status", "upgrade"):` 로 바꾸고 handlers dict 에 `"upgrade": lambda: setup.upgrade(p)` 를 더한다.

- [ ] **Step 4: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_setup -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 5: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/lib/orch/setup.py orca-team/lib/orch/cli.py orca-team/tests/test_setup.py
git commit -m "feat(orca-team): orch upgrade 가 복사된 규칙 파일을 새 템플릿으로 바꾼다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 8: 코디네이터·직무 문서

**Files:**
- Modify: `orca-team/templates/coordinator.md`, `roles/planner.md`, `roles/tester.md`, `roles/developer.md`
- Test: `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: 명령 `orch test`, `orch followups`, 리뷰 파일 절 이름, `followups.SOURCE_RE` 형식(`출처: T-xxx followups`, `출처: rest followups`, `출처: final followups`), `plan.group_problems`, `testgate.config_problems`.

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_templates.py`

위쪽 `from orch import ...` 줄에 `testgate` 를 더한다.

`test_planner_examples_pass_orch_validation` 끝에 더한다.

```python
        data = tomllib.loads(block)
        self.assertEqual(testgate.config_problems(data["test"]), [])
        self.assertEqual(plan.group_problems(tasks), [])
        self.assertIn("| todo-test | tester | ui-foundation, todo-store, todo-ui |", confirm["context"])
```

같은 클래스에 새 테스트를 더한다.

```python
    def test_batch_verify_rules_in_templates(self):
        coordinator = (T / "coordinator.md").read_text(encoding="utf-8")
        for key in ("## 묶음 후속", "## 마무리", "orch followups T-xxx", "orch followups --rest", "orch test --full",
                    "출처: T-xxx followups", "출처: rest followups", "출처: final followups",
                    "`[test]` 명령을 실행할 수 없다", "묶음 테스트(tester) 태스크였으면"):
            self.assertIn(key, coordinator)
        planner = (T / "roles/planner.md").read_text(encoding="utf-8")
        for key in ('`["tests/<key>/**", "docs/qa/<key>.md"]`', "{files}", "ok_exit = [0, 5]", "exclusive = true",
                    "3–6개", "developer 태스크는 tester 태스크에 의존하지 않는다"):
            self.assertIn(key, planner)
        tester = (T / "roles/tester.md").read_text(encoding="utf-8")
        for key in ("orch test T-xxx --full", "제품 회귀", "`tests/<key>/`", "`docs/qa/<key>.md`"):
            self.assertIn(key, tester)
        developer = (T / "roles/developer.md").read_text(encoding="utf-8")
        self.assertIn("워커 spec 의 `## 테스트` 절", developer)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates -v 2>&1 | tail -5`
Expected: FAIL

- [ ] **Step 3: `coordinator.md` 바꾸기**

(a) "리뷰 완료 알림이 오면" 의 APPROVE 첫 하위 항목을 바꾼다.

```markdown
  - 출력 `status` 가 `done` 이고 기획(planner) 태스크였으면 아래 "요청 처리" 4단계(`orch plan apply`)로 간다. 묶음 테스트(tester) 태스크였으면 아래 "묶음 후속"을 한 뒤 `orch start-ready`. 그 밖에는 `orch start-ready`.
```

(b) `- exit≠0 → 출력의 지시를 따른다. ...` 줄 바로 뒤에 하위 항목을 넣는다.

```markdown
  - `orch review` 가 exit 2 로 "`[test]` 명령을 실행할 수 없다"고 하면 라운드는 쓰이지 않았다. `docs/planning/tasks.toml` 의 `[test]` 를 고치는 기획 태스크를 만든다(사용자가 직접 넣은 `[test]` 면 `orch notify --type question --json -` 로 알린다). 고친 뒤 같은 태스크를 다시 `orch review` 한다(Bash `run_in_background: true`).
```

(c) "요청 처리" 7번을 바꾼다.

```markdown
7. 모든 태스크가 done 또는 dropped 면 아래 "마무리"를 한다.
```

(d) `## 디자인 일괄 승인` 바로 앞에 두 절을 넣는다.

```markdown
## 묶음 후속
묶음 테스트(tester) 태스크를 accept 하면 `orch followups T-xxx` 를 실행한다(읽기 전용). 출력의 세 목록을 처리한다.
- `regressions`(제품 회귀): 원인 태스크마다 회귀 수정 태스크를 만든다: `orch task new developer "회귀 수정: <요약>"`. front matter 에 원인 태스크와 같은 `owns:` 줄을 넣는다. `## Change` 첫 줄은 `출처: T-xxx followups`(T-xxx 는 tester 태스크)이고 다음 줄부터 항목 원문이다. `## Acceptance` 는 "실패한 테스트 <이름>이 `orch test T-yyy` 에서 통과한다"이다. "요청 처리" 2번처럼 spec 을 경로 지정 커밋하고 `orch start T-yyy`. 원인 태스크를 알 수 없으면 "사용자에게 묻기"대로 묻는다.
- `followups`(P2·P3): 묶음 정리 태스크 하나로 모은다: `orch task new developer "묶음 정리: <묶음 key>"`. `owns:` 는 항목이 가리키는 파일을 소유한 태스크들의 owns 를 합친 것이다. `## Change` 첫 줄은 `출처: T-xxx followups`, 다음 줄부터 항목 id 와 원문이다. 커밋하고 `orch start`. 소유 경로가 진행 중 태스크와 겹쳐 거부되면 그 태스크가 끝난 뒤 다시 start 한다.
- `planning_gaps`(기획 누락): `docs/planning/` 문서로 답할 수 있으면 묶음 정리 태스크 Change 에 함께 넣는다. 제품 결정이 필요한 항목은 모아서 질문 하나로 `orch notify --type question --json -` 한다. 답이 넣는 쪽이면 증분 기획 태스크("요청 처리" 1번의 추가 요청 방식)를 만들고, 빼는 쪽이면 아무것도 만들지 않는다.
- 세 목록이 모두 비면 할 일이 없다.
- 다시 시작한 코디네이터는 `.orchestration/tasks/` 의 `출처: T-xxx followups` 줄로 이미 처리한 묶음을 확인하고 다시 만들지 않는다.

## 마무리
모든 태스크가 done 또는 dropped 면 다음을 차례로 한다.
1. `orch followups --rest` 로 묶음 밖 developer 태스크의 후속을 "묶음 후속"과 같은 기준으로 처리한다. 만드는 태스크의 `## Change` 첫 줄은 `출처: rest followups` 다. 이 단계는 한 번만 한다. `.orchestration/tasks/` 에 `출처: rest followups` 태스크가 이미 있으면 건너뛴다. 만든 태스크가 있으면 그것들이 끝난 뒤 이 절을 다시 시작한다.
2. `docs/planning/tasks.toml` 에 `[test]` 가 있으면 `orch test --full` 을 Bash `run_in_background: true` 로 돌린다. exit 1 이면 출력의 `tail` 과 `log` 로 원인 태스크를 찾아 "묶음 후속"의 회귀 수정 태스크처럼 만든다(`## Change` 첫 줄 `출처: final followups`). 그 태스크들이 done 이 되면 다시 `orch test --full`. exit 2 면 위 exit≠0 처리를 따른다.
3. 통과하면(또는 `[test]` 가 없으면) `orch notify --type done "<결과 요약, 바뀐 주요 파일, 태스크별 리뷰 판정과 라운드 수, 마지막 full 결과>"`.
```

(e) "## 직무 선택" 의 tester 줄을 바꾼다.

```markdown
- tester: 묶음 통합 테스트(묶음마다 하나), QA 리포트
```

- [ ] **Step 4: `roles/planner.md` 바꾸기**

(a) 확정 질문 예시 `context` 의 태스크 그래프 표 끝(`| todo-ui | developer | todo-design, ui-foundation, todo-store |`) 뒤에 `\n| todo-test | tester | ui-foundation, todo-store, todo-ui |` 를 더한다(JSON 문자열 안이므로 `\n` 으로 잇는다).

(b) tasks.toml 예시 블록의 `todo-ui` 태스크 `acceptance` 줄 뒤(닫는 코드 펜스 앞)에 넣는다.

```toml

[[task]]
key = "todo-test"
role = "tester"
title = "할 일 묶음 테스트"
owns = ["tests/todo-test/**", "docs/qa/todo-test.md"]
depends_on = ["ui-foundation", "todo-store", "todo-ui"]
target = "할 일 묶음(UI 기반, 저장 모듈, 할 일 화면)"
change = "묶음 기능을 잇는 통합 테스트와 QA 리포트"
constraints = "contracts.md 1절 인터페이스로 검증한다. src/ 는 고치지 않는다"
acceptance = ["docs/qa/todo-test.md 에 전체 테스트 결과와 실패 분류가 있다"]

[test]
quick = "npx vitest related --run --passWithNoTests {files}"
full = "npx vitest run"
```

(c) `- 증분 기획: 기존 \`[[task]]\` 는 한 글자도 바꾸지 않고 …` 줄 바로 앞에 넣는다.

```markdown
- 묶음: 함께 끝내고 함께 검증할 developer 태스크 3–6개를 한 묶음으로 둔다. 묶음마다 tester 태스크 하나를 두고 `depends_on` 에 묶음의 developer 태스크를 모두 적는다. tester 태스크 `owns` 는 `["tests/<key>/**", "docs/qa/<key>.md"]`(`<key>` 는 그 tester 태스크 key)다.
- 문서 전용 developer 태스크(`owns` 가 모두 `README.md` 이거나 `docs/` 아래)는 묶음에 넣지 않아도 된다. 나머지 developer 태스크는 정확히 한 묶음에 든다.
- developer 태스크는 tester 태스크에 의존하지 않는다. 다음 묶음 구현이 앞 묶음 테스트를 기다리지 않게 한다. tester 태스크의 `depends_on` 에는 developer 태스크만 둔다.
- 단위 테스트는 developer 가 자기 소유 경로 안(예: `src/**/*.test.ts`)에 쓴다. tester 는 묶음 통합 테스트와 QA 리포트를 쓴다.
- `[test]` 표에 테스트 명령 두 개를 쓴다. `quick` 은 바뀐 파일과 관련된 테스트만 돌리고 `{files}` 를 정확히 한 번 담는다(예: `npx vitest related --run --passWithNoTests {files}`, `npx jest --findRelatedTests --passWithNoTests {files}`). `full` 은 전체 테스트다. 관련 테스트가 없을 때 성공으로 끝나야 한다. pytest 처럼 모은 테스트가 없을 때 5 로 끝나는 도구는 `ok_exit = [0, 5]` 를 넣는다. 테스트가 DB·포트를 같이 써서 동시에 돌면 깨지면 `exclusive = true` 를 넣는다. `timeout_min`(기본 30)으로 한 번 실행의 상한을 정한다.
- 테스트 도구를 설치하는 첫 developer 태스크의 `acceptance` 에 "`[test]` 의 quick·full 명령이 실행된다"를 넣는다.
- `[test]` 가 있으면 `orch plan check` 가 묶음 규칙과 `[test]` 형식을 검사한다.
```

- [ ] **Step 5: `roles/tester.md` 전체 교체**

```markdown
# tester — 테스트

## 책임
- 묶음 테스트: 묶음 developer 태스크(선행 태스크)의 기능을 잇는 통합 테스트를 쓰고 실행한다. QA 리포트를 쓴다.

## 입력
- `docs/planning/` 수용 기준과 `contracts.md`, 묶음 developer 태스크 리포트(`docs/tasks/<선행 태스크 ID>/report.md`, 실행 방법)

## 산출물
- `tests/<key>/` 묶음 통합 테스트(`<key>` 는 이 태스크의 기획 key. 소유 경로를 따른다)
- `docs/qa/<key>.md` QA 리포트: 시나리오, 결과, 재현 절차, 스크린샷 경로, 전체 테스트 결과와 실패 분류

## 테스트 실행
- `docs/planning/tasks.toml` 에 `[test]` 가 있으면 워커 spec 의 `## 테스트` 절을 따른다. 전체 테스트는 `orch test T-xxx --full` 로 한 번만 돌린다.
- 전체 테스트 실패는 둘로 나눈다. 테스트 코드의 잘못은 고친다. 제품 회귀는 실패한 테스트 이름, 원인으로 보이는 선행 태스크 ID, 근거를 QA 리포트에 적는다.
- `[test]` 가 없는 프로젝트는 수용 기준 테스트를 직접 돌리고 출력 원문을 리포트에 붙인다.

## 품질 기준
- 묶음 수용 기준마다 테스트 또는 수동 시나리오를 하나 이상 대응시킨다.
- 실패는 재현 절차와 함께 적는다.

## 하지 않는 것
- `src/` 수정. 버그는 리포트에 적고, 코디네이터가 developer 태스크로 만든다.
- developer 의 단위 테스트(그 태스크 소유 경로 안 테스트) 고치기.
```

- [ ] **Step 6: `roles/developer.md` 바꾸기** — "## 품질 기준" 첫 항목을 바꾼다.

```markdown
- spec 의 Acceptance 를 실제로 실행해 확인하고 출력 원문을 리포트에 붙인다. `docs/planning/tasks.toml` 에 `[test]` 가 있으면 테스트는 워커 spec 의 `## 테스트` 절을 따라 `orch test` 로 돌리고 전체 테스트는 돌리지 않는다.
```

- [ ] **Step 7: 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates -v 2>&1 | tail -5`
Expected: `OK`

- [ ] **Step 8: 전체 테스트 후 커밋**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` → `OK`

```bash
git add -- orca-team/templates/coordinator.md orca-team/templates/roles/planner.md orca-team/templates/roles/tester.md orca-team/templates/roles/developer.md orca-team/tests/test_templates.py
git commit -m "docs(orca-team): 코디네이터·기획·테스트·개발 지침에 묶음 검증 흐름을 적는다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

### Task 9: 문서와 0.6.0

**Files:**
- Modify: `README.md`, `docs/superpowers/2026-11-05-orca-team-status.md`, `docs/superpowers/specs/2026-10-30-orca-team-design.md`(§13 끝)
- Modify: `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Test: `orca-team/tests/test_cli.py`

- [ ] **Step 1: 버전 테스트 먼저 바꾸기** — `orca-team/tests/test_cli.py` 의 `"orca-team 0.5.2"` 를 `"orca-team 0.6.0"` 으로 바꾼다.

Run: `cd orca-team/tests && python3 -m unittest test_cli -v 2>&1 | tail -3` → FAIL

- [ ] **Step 2: 버전 세 곳** — `paths.py` 의 `VERSION = "0.5.2"` → `"0.6.0"`, `plugin.json` 과 `marketplace.json` 의 `"version": "0.5.2"` → `"0.6.0"`.

Run: `cd orca-team/tests && python3 -m unittest test_cli -v 2>&1 | tail -3` → `OK`

- [ ] **Step 3: README** — `README.md` 의 `## 보안 주의` 바로 앞에 넣는다.

```markdown
## 묶음 검증

- 0.6.0 부터 테스트와 리뷰를 기능마다 무겁게 반복하지 않고 묶음으로 나눈다.
- planner 가 `docs/planning/tasks.toml` 에 `[test]` 를 쓴다. `quick` 은 바뀐 파일과 관련된 테스트(`{files}` 자리에 파일 목록), `full` 은 전체 테스트다. 선택 키: `timeout_min`(기본 30), `ok_exit`(기본 `[0]`), `exclusive`(기본 false. 테스트가 DB·포트를 같이 쓰면 true).
- 묶음: 함께 끝내는 developer 태스크 3–6개와 그것들에 의존하는 tester 태스크 하나. tester 태스크가 묶음 통합 테스트를 쓰고 `orch test T-xxx --full` 로 전체 테스트를 한 번 돌린다. 다음 묶음 구현은 앞 묶음 테스트를 기다리지 않는다.
- developer·tester 워커는 worker_done 전에 `orch test T-xxx`(quick)를 통과시킨다. `orch review` 는 Codex 리뷰 전에 quick 을 확인하고(같은 코드면 워커 결과를 다시 쓴다), 실패하면 Codex 없이 REQUEST_CHANGES 를 낸다. 리뷰어는 전체 테스트를 다시 돌리지 않는다.
- 리뷰 등급: P1(수용 기준 미충족, 동작 버그, 보안, 데이터 손실, 계약 위반, 테스트 실패)만 승인을 막는다. P2·P3 는 `## 후속`, spec 에 없는 요구는 `## 기획 누락` 에 적는다. 라운드 2부터는 이전 지적과 새 커밋만 본다. orch 는 `## 지적 사항` 의 P1 수로 판정을 맞춘다. 수정 워커는 P1 만 고친다.
- 코디네이터는 묶음 tester 태스크를 accept 하면 `orch followups T-xxx` 로 회귀·후속·기획 누락을 모아 회귀 수정 태스크, 묶음 정리 태스크, 사용자 질문을 만든다. 모든 태스크가 끝나면 `orch test --full` 을 한 번 더 돌린 뒤 완료를 알린다.
- `[test]` 가 없는 프로젝트는 테스트 관문과 묶음 검사가 꺼진다. 리뷰 등급·라운드 규칙은 모든 프로젝트에 적용된다.
- 0.5.x 로 만든 프로젝트에 적용하기:
  1. 플러그인을 0.6.0 으로 업데이트한다.
  2. 프로젝트 루트에서 `.orchestration/bin/orch upgrade` 를 실행한다. 복사된 규칙 파일(`coordinator.md`, `roles/*.md`, `design-rules.md`, `text-rules.md`, `bin/orch`)을 새 템플릿으로 바꾸고 커밋하지 않는다. `config.toml` 은 건드리지 않고, 커밋 안 된 변경이 있는 파일은 건너뛴다.
  3. `git diff -- .orchestration` 으로 확인하고 커밋한다.
  4. `docs/planning/tasks.toml` 에 `[test]` 를 넣고 커밋한다. 직접 쓰거나 메인에 "테스트 명령을 정해 달라"고 증분 기획을 요청한다.
  5. 코디네이터 터미널을 닫고 메인에서 `/orca-team:up` 으로 다시 띄운다.
- 설계: `docs/superpowers/specs/2026-11-08-orca-team-batch-verify-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-08-orca-team-batch-verify.md`.
```

- [ ] **Step 4: 기본 설계 §13** — `docs/superpowers/specs/2026-10-30-orca-team-design.md` 의 `### 디자인 규칙 (2026-11-06, 0.5.2)` 절 뒤(다음 `##`/`###` 제목 앞, 없으면 파일 끝)에 넣는다.

```markdown
### 묶음 검증 (2026-11-08, 0.6.0)

- 설계: `2026-11-08-orca-team-batch-verify-design.md`. 기능 하나에 몇 시간이 걸리고 대부분 3라운드 이상 가던 문제를 줄인다.
- `docs/planning/tasks.toml` `[test]`(quick·full)를 orch 가 직접 돌린다(`orch test`, `lib/orch/testgate.py`). `orch review` 는 Codex 전에 quick 관문을 세우고, 묶음 테스트(tester) 리뷰에 full 결과와 묶음 절을 넣는다.
- 묶음은 tester 태스크 하나와 그 `depends_on` 의 developer 태스크들이다. `[test]` 가 있으면 `orch plan check` 가 묶음 규칙을 검사한다.
- 리뷰는 P1 만 막는다. 라운드 2부터는 이전 지적과 새 커밋만 본다(history 의 `head`). orch 가 `## 지적 사항` 의 P1 수로 판정을 맞춘다(`verdict_adjusted`). 수정 워커는 P1 만 고친다.
- `orch followups` 가 리뷰 파일의 회귀·후속·기획 누락을 모으고, 코디네이터가 회귀 수정·묶음 정리 태스크를 만든다. 마지막에 `orch test --full` 을 한 번 더 돌린다.
- `orch upgrade` 가 기존 프로젝트의 복사된 규칙 파일을 바꾼다(config.toml 제외, 커밋 안 함).
- 한계: 설계 문서 §10.
```

- [ ] **Step 5: 상태 문서** — `docs/superpowers/2026-11-05-orca-team-status.md`
  - 제목 줄의 `(2026-11-06, 0.5.2)` 를 `(2026-11-08, 0.6.0)` 으로 바꾼다.
  - 버전 표 끝에 행을 더한다.

```markdown
| 0.6.0 | 묶음 검증: `[test]` quick·full 을 orch 가 돌리는 테스트 관문(`orch test`), 묶음(tester 태스크) 단위 전체 테스트와 묶음 리뷰, P1 만 막는 수렴형 리뷰와 판정 맞추기, 후속 모으기(`orch followups`), 기존 프로젝트 갱신(`orch upgrade`) | `specs/2026-11-08-orca-team-batch-verify-design.md`, `plans/2026-11-08-orca-team-batch-verify.md` |
```

  - "## 검증" 의 테스트 줄을 실제 전체 테스트 수와 날짜로 바꾼다(Step 6 출력의 `Ran N tests`).
  - "## 실환경에서 확인하지 않은 흐름" 끝에 더한다.

```markdown
- 0.6.0 묶음 검증 전체: 실제 Codex 의 라운드 2 좁혀 보기와 `## 후속`·`## 기획 누락` 분류, 실제 테스트 도구(vitest)의 quick·full, 묶음 겹쳐 진행, 회귀 수정 태스크, `orch upgrade`. 확인 방법은 설계 §12 다.
```

  - "## 다음 작업 후보" 맨 앞에 더한다.

```markdown
- 0.6.0 실환경 확인(설계 §12): 리뷰만 확인 → Orca 한 바퀴 → 실제 사용 프로젝트에 `orch upgrade`. 태스크별 라운드 수와 전체 시간을 설계 §14 에 적는다.
```

- [ ] **Step 6: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: `OK` — 이 테스트 수를 상태 문서에 적는다.

- [ ] **Step 7: 공개 저장소 검사와 물결표 검사**

Run: `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'`
Expected: 출력 없음

Run: `git diff main --name-only -- '*.md' | xargs grep -n -P '\d~\d'`
Expected: 출력 없음

- [ ] **Step 8: 커밋**

```bash
git add -- README.md docs/superpowers/2026-11-05-orca-team-status.md docs/superpowers/specs/2026-10-30-orca-team-design.md orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json orca-team/tests/test_cli.py
git commit -m "docs(orca-team): 0.6.0 묶음 검증을 문서에 적고 버전을 올린다" -m "Co-Authored-By: <작성 모델> <noreply@anthropic.com>"
```

---

## 구현 뒤 (메인 세션이 한다)

1. 브랜치 전체 최종 리뷰(`git diff main...feat/0.6.0-batch-verify`).
2. 설계 §12 실환경 확인: 리뷰만 확인(Orca 없이) → Orca 한 바퀴. Orca 재시작·터미널 닫기·시험 프로젝트 정리는 사용자 승인을 받는다. 결과를 설계 문서 §14 에 적는다.
3. 사용자 승인 뒤 main 병합과 푸시(`git push origin main`. `backup/full-history` 는 푸시하지 않는다).
