# orca-team 코디네이터 자동 부활 구현 계획 (0.5.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 메인 세션의 SessionStart·UserPromptSubmit 훅이 코디네이터 생존을 확인하고, 확실히 죽었을 때만 `orch up` 을 분리 프로세스로 다시 띄운다.

**Architecture:** `orca.terminal_state` 가 생존을 alive·dead·unknown 으로 나누고, 새 모듈 `lib/orch/autoup.py` 가 실행 조건 판정(`decide`)·확인 간격(`due`)·실행(`spawn_up`)·훅 진입점(`check`)을 맡는다. 훅은 `check` 만 부르고 돌려받은 문구를 `additionalContext` 로 메인에게 넘긴다.

**Tech Stack:** Python 3.11 표준 라이브러리(`subprocess`, `datetime`), `/bin/sh`(분리 실행), unittest, 가짜 orca(`tests/fakes/orca`).

**설계:** `docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md`

## Global Constraints

- 표준 라이브러리만 쓴다. Python 3.11 이상.
- 테스트 전체: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` 이 경고 없이 통과해야 한다(기존 504개 + 추가분). 한 파일만: `cd orca-team/tests && python3 -m unittest test_autoup -v`.
- **테스트가 실제 Orca 를 부르면 안 된다.** 같은 프로세스 안에서 `orch` 함수나 훅 함수를 부르는 테스트는 반드시 `patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})` 로 가짜 orca 를 쓴다. 그렇지 않으면 실제 Orca 에 코디네이터를 띄울 수 있다.
- 버전은 0.5.1 이고 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다.
- 공개 저장소다. 커밋 전에 `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'` 결과가 비어야 한다.
- Markdown 에서 숫자 범위를 `~` 로 쓰지 않는다(`6–9`).
- 템플릿·문서 문체: 한다체, 코드 표기 뒤 조사는 띄운다("`x` 를").
- 작업 브랜치는 `feat/auto-up` 이다. main 에 직접 커밋하지 않는다.
- 커밋 메시지 끝에 실제 작성 모델의 `Co-Authored-By` 트레일러를 붙인다.
- 서브에이전트는 `~/.claude/plugins` 와 Orca 를 건드리지 않는다. `.superpowers/` 를 커밋하지 않는다.

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `orca-team/lib/orch/orca.py` | `terminal_state` 추가, `terminal_alive` 가 같은 판정 사용 | 1 |
| `orca-team/tests/test_orca.py` | `terminal_state` 테스트 | 1 |
| `orca-team/lib/orch/config.py` | `Config.auto_up` | 2 |
| `orca-team/lib/orch/autoup.py` (새) | 판정·확인 간격·분리 실행·훅 진입점 | 2 |
| `orca-team/templates/config.toml` | `[coordinator] auto_up = true` | 2 |
| `orca-team/tests/test_autoup.py` (새), `orca-team/tests/test_config.py` | 자동 부활 단위·통합 테스트 | 2 |
| `orca-team/hooks/orca_team_hook.py` | `role-mark`·`session-start` 연결, 오류 기록 함수 분리 | 3 |
| `orca-team/tests/test_hooks.py` | 훅 테스트와 기존 테스트 보정 | 3 |
| 템플릿·스킬·README·설계 문서·버전 | 문서와 0.5.1 | 4 |

---

### Task 1: 생존 판정 `orca.terminal_state`

**Files:**
- Modify: `orca-team/lib/orch/orca.py` (`terminal_alive` 정의 부분)
- Test: `orca-team/tests/test_orca.py`

**Interfaces:**
- Produces: `orca.terminal_state(handle: str | None) -> str` — `"alive"`, `"dead"`, `"unknown"` 중 하나. 예외를 내지 않는다(orca 없음도 `"unknown"`).
- `orca.terminal_alive(handle)` 은 그대로 bool 을 돌려주고 코드 12 는 지금처럼 전파한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_orca.py` 끝(`if __name__` 앞)에 클래스를 더한다. 이 파일에는 이미 `os`, `patch`, `orca`, `ProjectCase`, `fake_env` 가 있다(없으면 기존 import 줄에 더한다).

```python
class TerminalStateTest(ProjectCase):
    """자동 부활은 dead 일 때만 다시 띄운다. 확인할 수 없으면 unknown 이다."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, fake_env(self))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_states(self):
        cases = [
            ({"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}, 0, "alive"),
            ({"ok": False, "error": {"code": "terminal_handle_stale"}}, 1, "dead"),
            ({"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}, 0, "dead"),
            ({"ok": True, "result": {"terminal": {"connected": False, "agentIdentity": "claude"}}}, 0, "dead"),
            ({"ok": True, "result": {}}, 0, "unknown"),
            ({"ok": False, "error": {"code": "runtime_unavailable"}}, 1, "unknown"),
        ]
        for body, code, expected in cases:
            with self.subTest(body=body):
                self.respond("terminal show", body, code=code)
                self.assertEqual(orca.terminal_state("term_x"), expected)

    def test_unparseable_and_missing_binary_are_unknown(self):
        self.respond_raw("terminal show", "<<not json>>")
        self.assertEqual(orca.terminal_state("term_x"), "unknown")
        with patch.dict(os.environ, {"ORCA_TEAM_ORCA": str(self.tmp / "no-such-orca")}):
            self.assertEqual(orca.terminal_state("term_x"), "unknown")

    def test_no_handle_is_unknown_without_calling_orca(self):
        self.assertEqual(orca.terminal_state(None), "unknown")
        self.assertEqual(self.calls(), [])

    def test_alive_uses_same_verdict(self):
        self.respond("terminal show", {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}})
        self.assertFalse(orca.terminal_alive("term_x"))
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertFalse(orca.terminal_alive("term_x"))
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_orca.TerminalStateTest -v`
Expected: FAIL — `AttributeError: module 'orch.orca' has no attribute 'terminal_state'`

- [ ] **Step 3: 구현** — `orca-team/lib/orch/orca.py` 의 `terminal_alive` 함수를 다음 세 함수로 바꾼다.

```python
def _terminal_verdict(code: int, data: dict) -> str:
    """`orca terminal show` 응답 → "alive" | "dead" | "unknown".

    dead 는 닫힌 핸들(terminal_handle_stale, Orca 재시작 뒤 옛 핸들 포함)이거나, 터미널은 있는데 연결이 끊겼거나
    claude 가 끝나 셸만 남은 경우다. Orca 1.4.240 의 orphaned 는 렌더러 pane 마운트 여부일 뿐 생존과 무관해 보지 않는다."""
    if code == 0 and data.get("ok"):
        term = (data.get("result") or {}).get("terminal")
        if not isinstance(term, dict) or not term:
            return "unknown"
        return "alive" if term.get("connected") is True and term.get("agentIdentity") == "claude" else "dead"
    error = data.get("error")
    if isinstance(error, dict) and error.get("code") == "terminal_handle_stale":
        return "dead"
    return "unknown"


def terminal_state(handle: str | None) -> str:
    """코디네이터 자동 부활용 생존 판정. 예외를 내지 않는다(orca 없음·파싱 실패도 unknown)."""
    if not handle:
        return "unknown"
    try:
        code, data = call(["terminal", "show", "--terminal", handle], check=False)
    except OrchError:
        return "unknown"
    return _terminal_verdict(code, data)


def terminal_alive(handle: str | None) -> bool:
    """터미널이 연결돼 있고 그 안에서 claude 에이전트가 도는지. 코드 12(orca 없음)는 전파한다."""
    if not handle:
        return False
    try:
        code, data = call(["terminal", "show", "--terminal", handle], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return False
    return _terminal_verdict(code, data) == "alive"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_orca -v 2>&1 | tail -3`
Expected: `OK` (기존 `terminal_alive` 테스트 포함)

- [ ] **Step 5: 커밋**

```bash
git add orca-team/lib/orch/orca.py orca-team/tests/test_orca.py
git commit -m "feat(orca-team): 코디네이터 생존을 alive·dead·unknown 으로 나누는 terminal_state 를 더한다"
```

---

### Task 2: 자동 부활 모듈 `autoup.py` 와 `auto_up` 설정

**Files:**
- Create: `orca-team/lib/orch/autoup.py`
- Modify: `orca-team/lib/orch/config.py` (`coordinator()` 메서드 뒤)
- Modify: `orca-team/templates/config.toml` (머리 주석, `[coordinator]`)
- Create: `orca-team/tests/test_autoup.py`
- Modify: `orca-team/tests/test_config.py`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: `orca.terminal_state(handle) -> str`(Task 1), `setup.starting(run) -> bool`, `util.load_run/now/pid_alive/read_json/write_json`, `Config.load(path)`, `Project.config_path`, `Project.state`, `Project.setup_mode`
- Produces (Task 3 이 쓴다):
  - `autoup.check(p: Project, force: bool = False) -> str | None` — 메인에게 넘길 문구 또는 None
  - `autoup.decide(p: Project) -> tuple[str | None, str | None]`, `autoup.due(p) -> bool`, `autoup.spawn_up(p) -> bool`, `autoup.eligible(p) -> bool`
  - 문구 상수 `REVIVED`, `UNKNOWN`, `NO_HANDLE`, `DISABLED`(모두 `"orca-team: "` 로 시작), `CHECK_INTERVAL_SECONDS = 60`
  - 파일: `.orchestration/state/auto-up.json`(`{"checked_at": "<ISO 8601>"}`), `auto-up.pid`, `auto-up.log`
  - `Config.auto_up -> bool`

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_config.py` 의 기존 테스트 클래스에 더한다(`Config`, `OrchError` import 가 없으면 더한다).

```python
    def test_auto_up_defaults_true_and_rejects_non_bool(self):
        self.assertTrue(Config({}).auto_up)
        self.assertTrue(Config({"coordinator": {"auto_up": True}}).auto_up)
        self.assertFalse(Config({"coordinator": {"auto_up": False}}).auto_up)
        with self.assertRaises(OrchError):
            Config({"coordinator": {"auto_up": "yes"}}).auto_up  # noqa: B018
```

`orca-team/tests/test_templates.py` `TemplateTest` 에 더한다.

```python
    def test_config_enables_auto_up(self):
        text = (T / "config.toml").read_text(encoding="utf-8")
        self.assertIs(tomllib.loads(text)["coordinator"]["auto_up"], True)
        self.assertIn("# auto_up:", text)
```

`orca-team/tests/test_autoup.py` 를 만든다.

```python
import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from helpers import FAKES, ProjectCase
from orch import autoup
from orch.paths import Project
from orch.util import now, process_alive

STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
SHELL = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": None}}}


class AutoUpCase(ProjectCase):
    """가짜 orca 로만 돈다. 같은 프로세스에서 autoup 을 부르므로 os.environ 을 가짜 orca 로 고정한다."""

    def setUp(self):
        super().setUp()
        self.scaffold()
        self.orch("setup-done")
        env = {k: v for k, v in os.environ.items() if k != "ORCA_TERMINAL_HANDLE"}
        env.update({"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = Project(self.root)

    def set_run(self, **run):
        self.p.run_json.write_text(json.dumps(run), encoding="utf-8")

    def set_auto_up(self, value: str):
        path = self.p.config_path
        path.write_text(path.read_text(encoding="utf-8").replace("auto_up = true", f"auto_up = {value}"), encoding="utf-8")

    def wait_exit(self, pid: int, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(process_alive(pid), "자동 실행한 orch up 이 끝나지 않았다")


class DecideTest(AutoUpCase):
    def test_dead_or_shell_only_spawns(self):
        for body, code in ((STALE, 1), (SHELL, 0)):
            with self.subTest(body=body):
                self.respond("terminal show", body, code=code)
                self.assertEqual(autoup.decide(self.p), ("spawn", autoup.REVIVED))

    def test_alive_does_nothing(self):
        self.respond("terminal show", LIVE)
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_unknown_reports_without_spawn(self):
        self.respond_raw("terminal show", "<<not json>>")
        self.assertEqual(autoup.decide(self.p), (None, autoup.UNKNOWN))
        self.respond("terminal show", {"ok": True, "result": {}})
        self.assertEqual(autoup.decide(self.p), (None, autoup.UNKNOWN))

    def test_disabled_reports_only(self):
        self.set_auto_up("false")
        self.respond("terminal show", STALE, code=1)
        self.assertEqual(autoup.decide(self.p), (None, autoup.DISABLED))

    def test_missing_handle_reports_uncertain(self):
        self.set_run(run_id="run_x")
        self.assertEqual(autoup.decide(self.p), (None, autoup.NO_HANDLE))
        self.assertEqual(self.calls_for("terminal show"), [])

    def test_starting_or_running_spawn_skips(self):
        self.respond("terminal show", STALE, code=1)
        self.set_run(run_id="run_x", coordinator_handle="term_coord", launched_at=now())
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.set_run(run_id="run_x", coordinator_handle="term_coord")
        (self.p.state / "auto-up.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, None))

    def test_not_eligible_skips_without_orca(self):
        self.p.setup_mode.write_text("", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.p.setup_mode.unlink()
        self.set_run(coordinator_handle="term_coord")
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.assertEqual(self.calls_for("terminal show"), [])

    def test_broken_config_skips(self):
        self.respond("terminal show", STALE, code=1)
        self.set_auto_up('"yes"')
        self.assertEqual(autoup.decide(self.p), (None, None))
        self.p.config_path.write_text("[[[", encoding="utf-8")
        self.assertEqual(autoup.decide(self.p), (None, None))


class CheckTest(AutoUpCase):
    def test_interval_and_force(self):
        self.respond("terminal show", STALE, code=1)
        with patch.object(autoup, "spawn_up", return_value=True) as spawn:
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
            self.assertIsNone(autoup.check(self.p))
            self.assertEqual(autoup.check(self.p, force=True), autoup.REVIVED)
            old = (datetime.now().astimezone() - timedelta(seconds=autoup.CHECK_INTERVAL_SECONDS + 1)).isoformat()
            (self.p.state / "auto-up.json").write_text(json.dumps({"checked_at": old}), encoding="utf-8")
            self.assertEqual(autoup.check(self.p), autoup.REVIVED)
        self.assertEqual(spawn.call_count, 3)

    def test_corrupt_check_file_is_due(self):
        (self.p.state / "auto-up.json").write_text("garbage", encoding="utf-8")
        self.assertTrue(autoup.due(self.p))

    def test_spawn_failure_reports_unknown(self):
        self.respond("terminal show", STALE, code=1)
        with patch.object(autoup, "spawn_up", return_value=False):
            self.assertEqual(autoup.check(self.p), autoup.UNKNOWN)

    def test_not_eligible_writes_nothing(self):
        self.p.setup_mode.write_text("", encoding="utf-8")
        self.assertIsNone(autoup.check(self.p, force=True))
        self.assertFalse((self.p.state / "auto-up.json").exists())


class SpawnTest(AutoUpCase):
    def test_spawn_runs_orch_up_detached(self):
        self.set_run(run_id="run_x", coordinator_handle="term_old")
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        self.assertTrue(autoup.spawn_up(self.p))
        pid = int((self.p.state / "auto-up.pid").read_text(encoding="utf-8").split()[0])
        self.wait_exit(pid)
        self.assertEqual(json.loads(self.p.run_json.read_text(encoding="utf-8"))["coordinator_handle"], "term_new")
        log = (self.p.state / "auto-up.log").read_text(encoding="utf-8")
        self.assertIn("orch up (자동)", log)
        self.assertIn("launched", log)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_autoup test_config test_templates.TemplateTest.test_config_enables_auto_up 2>&1 | tail -5`
Expected: FAIL — `ModuleNotFoundError: No module named 'orch.autoup'`, `AttributeError: 'Config' object has no attribute 'auto_up'`, `KeyError: 'auto_up'`

- [ ] **Step 3: 설정** — `orca-team/lib/orch/config.py` 의 `coordinator()` 메서드 바로 뒤에 더한다.

```python
    @property
    def auto_up(self) -> bool:
        """[coordinator] auto_up. 메인 세션 훅이 죽은 코디네이터를 자동으로 다시 띄울지. 없으면 true."""
        value = self.data.get("coordinator", {}).get("auto_up", True)
        if not isinstance(value, bool):
            raise OrchError(f"config.toml [coordinator].auto_up 은 true 또는 false 여야 한다: {value!r}", 2)
        return value
```

`orca-team/templates/config.toml`: 머리 주석 마지막 줄(`# director: …`) 뒤에 한 줄을 더한다.

```toml
# auto_up: 메인 세션 훅이 코디네이터가 확실히 죽은 것을 보면 자동으로 다시 띄운다(기본 true). false 면 안내만 한다.
```

`[coordinator]` 표를 다음으로 바꾼다.

```toml
[coordinator]
model = "opus"
effort = "xhigh"
auto_up = true
```

- [ ] **Step 4: 모듈** — `orca-team/lib/orch/autoup.py` 를 만든다.

```python
"""코디네이터 자동 부활. 메인 세션의 SessionStart·UserPromptSubmit 훅이 check() 를 부른다.

확실히 죽은(dead) 코디네이터만 `orch up` 을 분리된 프로세스로 다시 띄운다. 확인할 수 없으면(unknown) 띄우지 않고
메인에게 알린다. 요청은 Run 메일함에 쌓이므로 새 코디네이터가 붙을 때까지 기다리지 않는다."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import orca
from .config import Config
from .paths import OrchError, Project
from .setup import starting
from .util import load_run, now, pid_alive, read_json, write_json

CHECK_INTERVAL_SECONDS = 60  # 프롬프트 때 확인 간격. 세션 시작 때는 force 로 항상 확인한다
PREFIX = "orca-team: "
REVIVED = (PREFIX + "코디네이터가 꺼져 있어 자동으로 다시 띄웠다. 답변 첫 줄에 사용자에게 이 사실을 한 줄로 알린다. "
           "요청은 평소대로 orch tell 로 넘긴다(Run 메일함에 쌓였다가 코디네이터가 붙으면 처리된다). "
           "메시지 watcher 가 꺼져 있으면 띄우고, 답하지 않은 질문이 있으면 orch reask 로 다시 보여 준다.")
UNKNOWN = (PREFIX + "Orca 에 연결하지 못해 코디네이터가 살아 있는지 확인하지 못했다. 답변 첫 줄에 사용자에게 "
           "Orca 가 켜져 있는지 확인해 달라고 한 줄로 알린다. 확인되면 /orca-team:up 을 실행한다.")
NO_HANDLE = (PREFIX + "코디네이터 실행 기록이 불확실하다(run.json 에 코디네이터 터미널이 없다). 답변 첫 줄에 "
             "사용자에게 한 줄로 알리고 /orca-team:up 결과를 확인한다.")
DISABLED = (PREFIX + "코디네이터가 꺼져 있다(config.toml 의 auto_up = false). 답변 첫 줄에 사용자에게 한 줄로 "
            "알리고 /orca-team:up 을 안내한다.")
# sh 가 orch up 을 백그라운드로 띄우고 pid 를 출력한 뒤 바로 끝난다. 훅이 시간 제한으로 끊겨도 orch up 은 계속 돈다.
LAUNCHER = '"$0" "$@" </dev/null >>"$ORCA_TEAM_AUTO_UP_LOG" 2>&1 & echo $!'


def check_file(p: Project) -> Path:
    return p.state / "auto-up.json"


def pid_file(p: Project) -> Path:
    return p.state / "auto-up.pid"


def log_file(p: Project) -> Path:
    return p.state / "auto-up.log"


def eligible(p: Project) -> bool:
    """세팅이 끝난 프로젝트인가(run.json 에 run_id 가 있고 세팅 중이 아님). 메인 세션인지는 훅이 본다."""
    return bool(load_run(p).get("run_id")) and not p.setup_mode.exists()


def due(p: Project) -> bool:
    """마지막 확인 뒤 CHECK_INTERVAL_SECONDS 가 지났는가. 기록이 없거나 읽을 수 없으면 True."""
    data = read_json(check_file(p), {})
    try:
        age = (datetime.now().astimezone() - datetime.fromisoformat(data["checked_at"])).total_seconds()
    except (TypeError, KeyError, ValueError):
        return True
    return not 0 <= age < CHECK_INTERVAL_SECONDS


def decide(p: Project) -> tuple[str | None, str | None]:
    """(action, context). action 은 "spawn" 또는 None, context 는 메인에게 넘길 문구 또는 None.
    파일은 쓰지 않는다(orca 조회만 한 번 한다). 판정 순서는 설계 §4.1 을 따른다."""
    if not eligible(p):
        return None, None
    try:
        enabled = Config.load(p.config_path).auto_up
    except OrchError:
        return None, None
    run = load_run(p)
    if starting(run) or pid_alive(pid_file(p)):
        return None, None
    handle = run.get("coordinator_handle")
    if not handle:
        return None, NO_HANDLE
    state = orca.terminal_state(handle)
    if state == "alive":
        return None, None
    if state == "unknown":
        return None, UNKNOWN
    return ("spawn", REVIVED) if enabled else (None, DISABLED)


def spawn_up(p: Project) -> bool:
    """`orch up` 을 분리된 프로세스로 띄우고 pid 를 auto-up.pid 에 남긴다. 기다리지 않는다. 띄우지 못하면 False.
    환경을 그대로 물려줘 orch up 이 메인 자신의 터미널(ORCA_TERMINAL_HANDLE)을 코디네이터로 기록하지 않게 한다."""
    orch_bin = Path(__file__).resolve().parents[2] / "bin" / "orch"
    p.ensure_state()
    log = log_file(p)
    try:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {now()} orch up (자동)\n")
        proc = subprocess.run(["/bin/sh", "-c", LAUNCHER, sys.executable, str(orch_bin), "up"], cwd=p.root,
                              env={**os.environ, "ORCA_TEAM_AUTO_UP_LOG": str(log)}, capture_output=True,
                              text=True, timeout=5, start_new_session=True)
        pid = int(proc.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return False
    pid_file(p).write_text(f"{pid}\n", encoding="utf-8")
    return True


def check(p: Project, force: bool = False) -> str | None:
    """훅 진입점. 확인 간격을 지키고(force 면 무시), 결정대로 실행한 뒤 메인에게 넘길 문구를 돌려준다."""
    if not eligible(p) or not (force or due(p)):
        return None
    write_json(check_file(p), {"checked_at": now()})
    action, context = decide(p)
    if action == "spawn" and not spawn_up(p):
        return UNKNOWN
    return context
```

`setup.py` 가 `autoup` 을 import 하지 않으므로 순환 import 는 없다. 생기면 오류 문구를 그대로 보고한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_autoup test_config test_templates -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 6: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: 경고 없는 `OK`. `config.toml` 템플릿을 파싱하는 다른 테스트가 실패하면 원인을 보고하고 고친다.

- [ ] **Step 7: 커밋**

```bash
git add orca-team/lib/orch/autoup.py orca-team/lib/orch/config.py orca-team/templates/config.toml orca-team/tests/test_autoup.py orca-team/tests/test_config.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 죽은 코디네이터를 분리 프로세스로 다시 띄우는 autoup 모듈과 auto_up 설정을 더한다"
```

---

### Task 3: 훅 연결 (`role-mark`, `session-start`)

**Files:**
- Modify: `orca-team/hooks/orca_team_hook.py` (`role_mark`, `session_start`, `main`, 새 함수 2개, 상수 1개)
- Test: `orca-team/tests/test_hooks.py`

**Interfaces:**
- Consumes: `autoup.check(p, force) -> str | None`, 문구 상수(Task 2)
- Produces: UserPromptSubmit 출력 `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": <문구>}}`, SessionStart 안내의 코디네이터 줄

- [ ] **Step 1: 기존 테스트를 가짜 orca 로 고정하고 실패하는 테스트 작성** — `orca-team/tests/test_hooks.py`

import 줄 `from helpers import …` 에 `FAKES` 를 더한다(없으면 `from helpers import FAKES` 를 더한다). 파일 머리에 `time` import 가 없으면 `import time` 을 더한다. `HOOK = …` 아래에 상수를 더한다.

```python
LIVE = {"ok": True, "result": {"terminal": {"connected": True, "agentIdentity": "claude"}}}
STALE = {"ok": False, "error": {"code": "terminal_handle_stale"}}
```

`HookCase.setUp` 끝에 한 줄을 더한다(기존 테스트에서 코디네이터가 살아 있는 것으로 보이게 한다).

```python
        self.respond("terminal show", LIVE)
```

`StopAndSessionStartTest.test_session_start_fails_open_when_open_question_count_breaks` 의 `env = {…}` 줄 바로 뒤에 한 줄을 더한다. **이 테스트는 훅 함수를 같은 프로세스에서 부르므로 가짜 orca 로 고정하지 않으면 실제 Orca 를 부른다.**

```python
        env.update({"ORCA_TEAM_ORCA": str(FAKES / "orca"), "FAKE_DIR": str(self.fake_dir)})
```

파일 끝(`if __name__` 앞)에 클래스를 더한다.

```python
class AutoUpHookTest(HookCase):
    """메인 세션 훅이 죽은 코디네이터를 다시 띄운다. 세팅이 끝난 프로젝트, 가짜 orca."""

    def setUp(self):
        super().setUp()
        self.orch("setup-done")

    def context(self, out):
        return out["hookSpecificOutput"]["additionalContext"] if out else None

    def wait_spawned(self):
        from orch.util import process_alive

        pid = int((self.root / ".orchestration/state/auto-up.pid").read_text(encoding="utf-8").split()[0])
        deadline = time.monotonic() + 20
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(process_alive(pid))

    def run_state(self):
        return json.loads((self.root / ".orchestration/state/run.json").read_text(encoding="utf-8"))

    def test_prompt_revives_dead_coordinator(self):
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        out = self.hook("role-mark", {"session_id": "m", "prompt": "할 일 앱 만들어 줘"})
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("자동으로 다시 띄웠다", self.context(out))
        self.wait_spawned()
        self.assertEqual(self.run_state()["coordinator_handle"], "term_new")

    def test_prompt_within_interval_skips(self):
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        self.respond("terminal show", STALE, code=1)
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "again"}))
        self.assertEqual(len(self.calls_for("terminal show")), 1)

    def test_session_start_checks_regardless_of_interval(self):
        self.hook("role-mark", {"session_id": "m", "prompt": "hi"})
        self.respond("terminal show", STALE, code=1)
        self.respond("agent launch", {"ok": True, "result": {"terminal": {"handle": "term_new"}}})
        context = self.context(self.hook("session-start", {"session_id": "fresh", "source": "startup"}))
        self.assertIn("자동으로 다시 띄웠다", context)
        self.wait_spawned()

    def test_coordinator_and_worker_sessions_skip(self):
        self.respond("terminal show", STALE, code=1)
        self.assertIsNone(self.hook("role-mark", {"session_id": "c", "prompt": "[orca-team:coordinator] 시작"}))
        self.assertIsNone(self.hook("role-mark", {"session_id": "w", "prompt": "Your task ID is: t"}))
        self.assertEqual(self.calls_for("terminal show"), [])
        self.assertEqual(self.calls_for("agent launch"), [])

    def test_disabled_reports_without_launch(self):
        path = self.root / ".orchestration/config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("auto_up = true", "auto_up = false"), encoding="utf-8")
        self.respond("terminal show", STALE, code=1)
        self.assertIn("auto_up = false", self.context(self.hook("role-mark", {"session_id": "m", "prompt": "hi"})))
        self.assertEqual(self.calls_for("agent launch"), [])

    def test_setup_mode_skips(self):
        (self.root / ".orchestration/state/setup-mode").write_text("", encoding="utf-8")
        self.respond("terminal show", STALE, code=1)
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        self.assertEqual(self.calls_for("terminal show"), [])

    def test_auto_up_error_does_not_block(self):
        (self.root / ".orchestration/state/auto-up.json").mkdir()
        self.assertIsNone(self.hook("role-mark", {"session_id": "m", "prompt": "hi"}))
        context = self.context(self.hook("session-start", {"session_id": "fresh", "source": "startup"}))
        self.assertIn("/orca-team:up", context)
        self.assertIn("auto-up", (self.root / ".orchestration/state/hook-errors.log").read_text(encoding="utf-8"))
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_hooks.AutoUpHookTest -v 2>&1 | tail -15`
Expected: FAIL — role-mark 가 아무것도 돌려주지 않는다(`TypeError: 'NoneType' object is not subscriptable` 등), 오류 기록 검사 실패

- [ ] **Step 3: 훅 구현** — `orca-team/hooks/orca_team_hook.py`

모듈 상수 영역(`CONTEXT_FINGERPRINT = 80` 아래)에 더한다.

```python
# 세션 시작 안내의 코디네이터 줄(자동 부활 문구가 없을 때). /orca-team:up 은 수동 복구로 남긴다.
DEFAULT_UP_LINE = ("- 코디네이터는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다. 메시지 watcher 가 꺼져 있으면 Bash "
                   "`run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. 코디네이터 상태가 의심되면 "
                   "`/orca-team:up` 을 실행한다.")
```

`role_mark` 바로 앞에 두 함수를 더한다.

```python
def log_hook_error(p: Project, label: str) -> None:
    """훅 예외를 state/hook-errors.log 에 남긴다. 기록도 실패하면 조용히 넘어간다."""
    try:
        p.ensure_state()
        with (p.state / "hook-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now()} {label}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def auto_up_context(p: Project, force: bool) -> str | None:
    """코디네이터 자동 부활(orch.autoup.check). 실패해도 세션을 막지 않고 hook-errors.log 에 남긴다.
    import 는 여기서 한다. guard 훅은 매 도구 호출마다 돌아 무거운 모듈을 미리 불러오지 않는다."""
    try:
        from orch import autoup

        return autoup.check(p, force=force)
    except Exception:  # noqa: BLE001  훅은 세션을 막지 않는다
        log_hook_error(p, "auto-up")
        return None
```

`role_mark` 를 다음으로 바꾼다(역할 판정과 기록 부분은 그대로 옮긴다).

```python
def role_mark(p: Project, payload: dict):
    session_id = payload.get("session_id")
    if not valid_session_id(session_id):
        return None
    if not (p.sessions / session_id).exists():
        prompt = payload.get("prompt") or ""
        if prompt.lstrip().startswith(COORDINATOR_MARK) or env_coordinator(p):
            role = "coordinator"
        elif any(mark in prompt for mark in WORKER_MARKS):
            found = ASK_WORKER_RE.search(prompt)
            role = f"worker:{found.group(1)}" if found else "worker"
        else:
            role = "main"
        p.ensure_state()
        (p.sessions / session_id).write_text(role + "\n", encoding="utf-8")
    if role_of(p, session_id) != "main":
        return None
    context = auto_up_context(p, force=False)
    if not context:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}
```

`session_start` 의 `lines = [...]` 목록에서 마지막 항목 `"- 먼저 `/orca-team:up` 으로 코디네이터와 메시지 watcher 를 확인한다."` 를 빼고(앞 항목 뒤에서 `]` 로 닫는다), 목록 바로 뒤에 두 줄을 더한다.

```python
    context = auto_up_context(p, force=True)
    lines.append(f"- {context}" if context else DEFAULT_UP_LINE)
```

`main()` 의 `except Exception:` 블록 안의 기록 코드(`try: p.ensure_state() … except OSError: pass`)를 `log_hook_error(p, sys.argv[1])` 한 줄로 바꾸고 `return 0` 은 그대로 둔다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_hooks -v 2>&1 | tail -3`
Expected: `OK`. `/orca-team:up` 을 찾는 기존 세션 시작 테스트 4개는 기본 줄에 `/orca-team:up` 이 있어 그대로 통과해야 한다.

- [ ] **Step 5: 전체 테스트**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`
Expected: 경고 없는 `OK`

- [ ] **Step 6: 커밋**

```bash
git add orca-team/hooks/orca_team_hook.py orca-team/tests/test_hooks.py
git commit -m "feat(orca-team): 메인 세션의 세션 시작·프롬프트 훅이 코디네이터를 확인하고 자동으로 살린다"
```

---

### Task 4: 문서와 0.5.1

**Files:**
- Modify: `orca-team/templates/CLAUDE.section.md`, `orca-team/skills/up/SKILL.md`, `README.md`
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md` (§13 끝), `docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md` (§3, §6.2), `docs/superpowers/2026-11-05-orca-team-status.md`
- Modify: 버전 세 곳, `orca-team/tests/test_cli.py`, `orca-team/tests/test_templates.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_cli.py` `test_version` 의 기대값을 `"orca-team 0.5.1"` 로 바꾼다.

`orca-team/tests/test_templates.py` `TemplateTest` 에 더한다.

```python
    def test_docs_describe_auto_up(self):
        claude = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        self.assertIn("자동으로 다시 띄운다(config.toml auto_up)", claude)
        self.assertNotIn("세션을 다시 열었으면 `/orca-team:up` 을 먼저 실행한다", claude)
        up = (PLUGIN / "skills/up/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("수동 복구", up)
        readme = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("auto_up = false", "자동으로 다시 띄운다", "docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md"):
            self.assertIn(key, readme)
        spec = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        self.assertIn("### 코디네이터 자동 부활 (2026-11-06, 0.5.1)", spec[spec.index("## 13."):])
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_cli test_templates 2>&1 | tail -5`
Expected: FAIL — `test_version`, `test_docs_describe_auto_up`

- [ ] **Step 3: 문서**

`orca-team/templates/CLAUDE.section.md` 메인 규칙의 ``- 세션을 다시 열었으면 `/orca-team:up` 을 먼저 실행한다.`` 줄을 다음으로 바꾼다.

```markdown
- 코디네이터는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다(config.toml auto_up). 훅 안내가 없는데 코디네이터 상태가 의심되면 `/orca-team:up` 을 실행한다.
```

`orca-team/skills/up/SKILL.md` front matter `description` 의 첫 줄(`orca-team 프로젝트에서 세션을 다시 열었을 때 …`)을 다음으로 바꾼다(트리거 줄은 그대로).

```yaml
  orca-team 프로젝트의 코디네이터와 메시지 watcher 를 되살리는 수동 복구. 코디네이터는 보통 메인 세션 훅이 자동으로 살린다(config.toml auto_up). 자동 부활이 꺼져 있거나 훅이 안내했을 때 쓴다.
```

`README.md` `## 복구` 절의 마지막 글머리(``- 코디네이터가 죽었으면 메인에서 `/orca-team:up` 을 실행한다. …``)를 다음 두 글머리로 바꾼다.

```markdown
- 코디네이터가 죽었으면 메인 세션의 훅이 자동으로 다시 띄운다. 세션을 열 때와 프롬프트를 칠 때(60초 간격) 코디네이터 터미널을 확인하고, 닫힌 핸들이거나 Claude 가 끝난 것이 확실할 때만 `orch up` 을 분리 프로세스로 실행한다. 메인은 답변 첫 줄에 한 줄로 알린다. 그사이 보낸 요청은 Run 메일함에 쌓였다가 새 코디네이터가 처리한다. Orca 에 연결하지 못하면 띄우지 않고 확인을 부탁한다. 끄려면 `.orchestration/config.toml` 의 `[coordinator]` 에 `auto_up = false` 를 넣는다. 실행 기록은 `.orchestration/state/auto-up.log` 에 남는다. 살아 있지만 멈춘 코디네이터(권한 창 대기 등)는 감지하지 못한다. 설계: `docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md`.
- 수동 복구는 메인에서 `/orca-team:up` 을 실행한다. 살아 있는 코디네이터는 그대로 두고, 없으면 새로 띄운다. 새 코디네이터는 `orch bind` 로 기존 Run 에 다시 붙고 `orch status` 의 `next` 로 이어 간다.
```

`docs/superpowers/specs/2026-10-30-orca-team-design.md` 끝에 빈 줄과 다음을 더한다.

```markdown
### 코디네이터 자동 부활 (2026-11-06, 0.5.1)

설계: `2026-11-06-orca-team-auto-up-design.md`. 구현에서 정한 것:
- `orca.terminal_state` 는 `ok: true` 인데 `result.terminal` 이 없거나 비면 unknown 이다(설계 초안보다 좁혀 확실한 경우만 dead).
- `autoup.spawn_up` 은 `/bin/sh -c` 로 `orch up` 을 백그라운드에 띄우고 sh 는 pid 를 출력한 뒤 바로 끝난다. 훅이 시간 제한으로 끊겨도 `orch up` 은 계속 돌고, 남는 Popen 객체가 없어 ResourceWarning 도 없다.
- `autoup` 은 훅의 `auto_up_context` 안에서 늦게 import 한다. guard 훅은 매 도구 호출마다 돈다.
- 세션 시작 안내에 자동 부활 문구가 없으면 기본 줄에 `/orca-team:up` 을 수동 복구로 남긴다.
```

`docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md`:
- §3 표 `dead` 행의 `또는 ok: true 인데` 를 `또는 ok: true 이고 result.terminal 이 있는데` 로 바꾸고, `unknown` 행 끝에 `, ok: true 인데 result.terminal 이 없거나 빔` 을 더한다.
- §6.2 의 "문구가 없으면 … 를 넣는다." 줄의 따옴표 안 문장을 Task 3 의 `DEFAULT_UP_LINE` 문장(앞의 `- ` 포함)으로 바꾼다.

`docs/superpowers/2026-11-05-orca-team-status.md`:
- 제목을 `# orca-team 현재 상태 (2026-11-06, 0.5.1)` 로 바꾼다.
- 버전 표 끝에 행을 더한다: ``| 0.5.1 | 코디네이터 자동 부활: 메인 세션 훅이 확실히 죽은 코디네이터를 orch up 분리 실행으로 살림(config.toml auto_up) | `specs/2026-11-06-orca-team-auto-up-design.md`, `plans/2026-11-06-orca-team-auto-up.md` |``
- "검증" 의 테스트 수를 Step 5 의 `Ran N tests` 의 N 으로 바꾼다.
- "실환경에서 확인하지 않은 흐름" 에 더한다: `- 0.5.1 코디네이터 자동 부활: 실제 Orca 에서 코디네이터 터미널을 닫거나 Orca 를 재시작한 뒤의 자동 부활(단위·통합 테스트만 했다).`

- [ ] **Step 4: 버전 세 곳** — `orca-team/lib/orch/paths.py` `VERSION = "0.5.1"`, `orca-team/.claude-plugin/plugin.json` 과 `.claude-plugin/marketplace.json` 의 `"version": "0.5.1"`.

- [ ] **Step 5: 전체 테스트와 개인 경로 검사**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3; git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'; echo "grep exit=$?"`
Expected: 경고 없는 `OK` 와 `grep exit=1`

- [ ] **Step 6: 커밋**

```bash
git add orca-team/templates/CLAUDE.section.md orca-team/skills/up/SKILL.md README.md docs/superpowers/specs/2026-10-30-orca-team-design.md docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md docs/superpowers/2026-11-05-orca-team-status.md orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json orca-team/tests/test_cli.py orca-team/tests/test_templates.py
git commit -m "orca-team 0.5.1: 코디네이터 자동 부활"
```

---

## 구현 뒤 (메인 세션이 한다)

- 브랜치 전체 최종 리뷰.
- 사용자 승인을 받고 실환경 확인: 시험 프로젝트에서 코디네이터 터미널만 닫고 메인에 요청을 보낸다. 자동으로 다시 뜨는지, 한 줄 안내, 그 요청 처리를 본다. Orca 재시작 확인은 별도 승인을 받는다.
