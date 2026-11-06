# orca-team 코디네이터 자동 부활 설계

- 날짜: 2026-11-06
- 대상: orca-team 0.5.0(`orca-team/`). 선행 설계는 `2026-10-30-orca-team-design.md`(§7 코디네이터, §13 구현 조정)다.
- 상태: 사용자가 브레인스토밍 결정 4개와 설계 1–3절을 승인했다. 버전은 0.5.1 로 정했다.

## 1. 목적

코디네이터가 죽어 있으면 사용자가 `/orca-team:up` 을 직접 실행해야 한다. Orca 를 재시작했거나 코디네이터 세션이 끝났을 때 메인에 요청을 보내면, 요청은 Run 메일함에 쌓이기만 하고 처리되지 않는다.

메인 세션의 훅이 코디네이터 생존을 확인하고, 확실히 죽었을 때만 자동으로 다시 띄운다. 사용자 프롬프트는 막지 않는다.

### 1.1 지금 구조에서 쓰는 것

- `orch tell` 은 `run:<run_id>` 메일함으로 보낸다. 코디네이터가 죽은 동안 보낸 요청도 쌓여 있다가, 새 코디네이터가 같은 Run 에 다시 붙으면(`orch bind` → `run-use`) 처리된다. 그래서 훅은 띄우기만 하고 준비를 기다리지 않는다.
- `orch up`(`setup.up`)은 실행 직전에 `run.json` 에 `launched_at` 을 쓰고, 180초(`LAUNCH_GRACE_SECONDS`) 동안 다시 띄우지 않는다.
- Stop 훅은 메인의 메시지 watcher 가 꺼져 있으면 턴을 막고 다시 띄우게 한다.

## 2. 사용자 결정

| # | 질문 | 결정 |
|---|---|---|
| 1 | 기본값 | 켬. `config.toml` `[coordinator] auto_up = false` 로 끈다. 키가 없는 기존 프로젝트도 켜진 것으로 본다 |
| 2 | 확인 시점 | 세션 시작(SessionStart) 때 항상, 프롬프트(UserPromptSubmit) 때는 마지막 확인 뒤 60초가 지났을 때만 |
| 3 | 알림 | 메인이 답변 첫 줄에 한 줄로 알린다(다시 띄움, 확인 불가, 기록 불확실, 자동 꺼짐) |
| 4 | 방식 | 훅이 판정하고 `orch up` 을 분리된 백그라운드 프로세스로 직접 실행한다(상시 감시 프로세스나 메인 모델 실행 방식이 아니다) |
| 5 | 버전 | 0.5.1 |

## 3. 생존 판정 (`lib/orch/orca.py` `terminal_state`)

`orca terminal show --terminal <handle> --json` 응답을 셋으로 나눈다.

| 판정 | 조건 |
|---|---|
| `alive` | `ok: true`, `result.terminal.connected: true`, `result.terminal.agentIdentity: "claude"` |
| `dead` | `ok: false` 이고 `error.code` 가 `terminal_handle_stale`(닫힌 핸들, Orca 재시작 뒤의 옛 핸들). 또는 `ok: true` 이고 `result.terminal` 이 있는데 `connected` 가 true 가 아니거나 `agentIdentity` 가 `"claude"` 가 아님(Claude 가 끝나고 셸만 남음) |
| `unknown` | 그 밖의 모든 경우: orca 실행 파일 없음(`OrchError` 코드 12), 출력 파싱 실패, 다른 오류 코드, `ok: true` 인데 `result.terminal` 이 없거나 빔 |

- 2026-11-06 Orca 1.4.245 에서 닫힌 핸들과 없는 핸들 모두 `ok: false`, `error.code: "terminal_handle_stale"`, exit 1 을 확인했다.
- 기존 `terminal_alive(handle)` 은 `terminal_state(handle) == "alive"` 로 바꾼다. 코드 12 는 지금처럼 전파한다(`status`·`up` 의 기존 동작 유지). `terminal_state` 는 코드 12 를 전파하지 않고 `unknown` 으로 돌려준다.

## 4. 자동 실행 조건 (`lib/orch/autoup.py` `decide`)

다음을 모두 만족하면 `orch up` 을 띄운다.

1. 메인 세션이다(`role_of(p, session_id) == "main"`).
2. 세팅이 끝났다: `run.json` 에 `run_id` 가 있고 `.orchestration/state/setup-mode` 가 없다.
3. `config.toml` 을 읽을 수 있고 `[coordinator] auto_up` 이 false 가 아니다.
4. `run.json` 에 `coordinator_handle` 이 있고 `terminal_state` 가 `dead` 다.
5. 시작 유예 중이 아니다(`setup.starting(run)` 이 false).
6. 진행 중인 자동 실행이 없다(`.orchestration/state/auto-up.pid` 의 pid 가 살아 있지 않거나, pid 파일이 60초(`PID_STALE_SECONDS`)보다 오래됐다).
7. 직전 자동 실행 뒤 10분(`RETRY_WINDOW_SECONDS = 600`)이 지났다(`auto-up.json` 의 `spawned_at`). 그 안에 다시 `dead` 면 띄우지 않고 재사망 문구만 넘긴다.

### 4.1 결정 결과

`decide` 는 부수 효과 없이 `(action, context)` 를 돌려준다. `action` 은 `"spawn"` 또는 `None`, `context` 는 메인에게 넘길 문구이거나 None 이다.

판정 순서(앞에서 결과가 정해지면 멈춘다):
1. 조건 1·2 불만족 또는 설정 파일 오류 → None, 문구 없음
2. 시작 유예 중 → None, 문구 없음
3. 자동 실행 진행 중(pid 살아 있고 pid 파일이 60초 안) → None, 문구 없음
4. `coordinator_handle` 없음 → None, 기록 불확실 문구
5. `terminal_state`: `alive` → None, 문구 없음 / `unknown` → None, 확인 불가 문구 / `dead` → `spawned_at` 이 10분 안이면 None·재사망 문구, 아니면 `auto_up` 이 true 면 spawn·다시 띄움 문구, false 면 None·자동 꺼짐 문구

| 상황 | action | 메인에게 넘기는 문구 |
|---|---|---|
| 조건 1·2 불만족, 설정 파일 오류 | None | 없음 |
| `alive`, 시작 유예 중, 자동 실행 진행 중 | None | 없음 |
| 조건을 모두 만족 | spawn | 다시 띄움 문구 |
| `auto_up = false` 이고 `dead` | None | 자동 꺼짐 문구 |
| `coordinator_handle` 없음, 시작 유예 아님 | None | 기록 불확실 문구 |
| 자동 실행 뒤 10분 안에 다시 `dead`(`auto_up` 과 무관) | None | 재사망 문구 |
| `unknown` | None | 확인 불가 문구 |

문구(모두 `orca-team:` 으로 시작한다):

- 다시 띄움: `코디네이터가 꺼져 있어 자동으로 다시 띄우는 중이다. 답변 첫 줄에 사용자에게 이 사실을 한 줄로 알린다. 요청은 평소대로 orch tell 로 넘긴다(Run 메일함에 쌓였다가 코디네이터가 붙으면 처리된다). 메시지 watcher 가 꺼져 있으면 띄우고, 답하지 않은 질문이 있으면 orch reask 로 다시 보여 준다.`
- 확인 불가: `Orca 에 연결하지 못해 코디네이터가 살아 있는지 확인하지 못했다. 답변 첫 줄에 사용자에게 Orca 가 켜져 있는지 확인해 달라고 한 줄로 알린다. 확인되면 /orca-team:up 을 실행한다.`
- 기록 불확실: `코디네이터 실행 기록이 불확실하다(run.json 에 코디네이터 터미널이 없다). 답변 첫 줄에 사용자에게 한 줄로 알리고 /orca-team:up 결과를 확인한다.`
- 자동 꺼짐: `코디네이터가 꺼져 있다(config.toml 의 auto_up = false). 답변 첫 줄에 사용자에게 한 줄로 알리고 /orca-team:up 을 안내한다.`
- 재사망: `자동으로 다시 띄운 코디네이터가 10분 안에 다시 꺼졌다(기록: .orchestration/state/auto-up.log). 답변 첫 줄에 사용자에게 한 줄로 알리고, 원인을 확인한 뒤 /orca-team:up 을 실행하도록 안내한다. 10분 동안은 자동으로 다시 띄우지 않는다.`

## 5. 실행과 확인 간격

### 5.1 `spawn_up(p)`
- `[sys.executable, <플러그인>/bin/orch, "up"]` 을 프로젝트 루트에서 `start_new_session=True` 로 띄운다. 표준 출력·오류는 `.orchestration/state/auto-up.log` 에 덧붙인다. 대기 플래그(`--wait-run`)는 주지 않는다.
- 띄운 pid 를 `.orchestration/state/auto-up.pid` 에 쓴다. 훅은 기다리지 않고 돌아온다. `orch up` 은 대기 없이 몇 초 안에 끝나므로 60초(`PID_STALE_SECONDS`)보다 오래된 pid 파일은 진행 중으로 보지 않는다(다른 프로세스가 재사용한 pid 일 수 있다).
- 띄우면 `auto-up.json` 에 `spawned_at` 을 쓴다. 4절 조건 7(10분 안 재사망은 띄우지 않음)이 이 값을 본다.
- `setup.up` 은 run.json 잠금 안에서 시작 유예를 다시 확인하고 `launched_at` 을 써서 기동을 선점한다. 동시에 돈 두 `orch up` 중 선점에 진 쪽은 `agent launch` 를 부르지 않고 `starting` 을 돌려준다.
- 환경은 훅 프로세스 것을 그대로 물려준다. `ORCA_TERMINAL_HANDLE`(메인 터미널)이 있어야 `orch up` 이 메인 자신의 핸들을 코디네이터로 기록하는 실수를 막는다(`setup.up` 의 기존 검사).
- 실행에 실패하면(예: OSError) 확인 불가 문구를 넘긴다.

### 5.2 확인 간격
- `.orchestration/state/auto-up.json` 에 `{"checked_at": "<ISO 8601>"}` 를 쓴다. SessionStart 와 UserPromptSubmit 이 함께 갱신한다. 같은 파일에 `spawned_at`(5.1)과 `notice` 도 둔다.
- `notice` 는 마지막으로 넘긴 문구다. UserPromptSubmit 은 문구가 직전 `notice` 와 같으면 넘기지 않는다. SessionStart(force)는 같아도 넘긴다. 실제로 코디네이터를 다시 띄운 안내(REVIVED)는 `notice` 와 같아도 항상 넘긴다. 문구가 없으면 `notice` 를 지워 다음 문제는 다시 알린다.
- 생존 확인(`terminal_state`)은 Orca 응답을 3초(`orca.TERMINAL_STATE_TIMEOUT`)만 기다리고, 넘으면 `unknown` 이다.
- SessionStart 는 간격과 무관하게 확인한다. UserPromptSubmit 은 `checked_at` 뒤 60초(`CHECK_INTERVAL_SECONDS`)가 지났을 때만 확인한다. 기록이 없거나 읽을 수 없으면 확인한다.
- 조건 1·2(메인, 세팅 끝)를 만족하지 않으면 간격 기록도 하지 않는다.

## 6. 훅 (`hooks/orca_team_hook.py`)

### 6.1 UserPromptSubmit (`role-mark`)
- 지금처럼 세션 역할을 먼저 기록한다. 그 뒤 메인 세션이면 간격 규칙대로 `decide` 를 부르고, `spawn` 이면 `spawn_up` 을 실행한다.
- 문구가 있으면 `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": <문구>}}` 를 돌려준다. 없으면 지금처럼 아무것도 돌려주지 않는다.

### 6.2 SessionStart (`session-start`)
- 메인 세션이면 간격과 무관하게 `decide`·`spawn_up` 을 실행한다.
- 지금 안내의 `- 먼저 /orca-team:up 으로 코디네이터와 메시지 watcher 를 확인한다.` 줄은 결정 결과로 바꾼다.
  - 문구가 있으면 그 문구를 넣는다.
  - 문구가 없으면 ``- 코디네이터는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다. 메시지 watcher 가 꺼져 있으면 Bash `run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. 코디네이터 상태가 의심되면 `/orca-team:up` 을 실행한다.`` 를 넣는다.
- 밀린 메시지·답하지 않은 질문 안내 줄은 그대로 둔다.

### 6.3 시간 제한과 오류
- 훅 시간 제한은 10초 그대로다. 확인은 Orca 호출 1번(3초 제한, 넘으면 `unknown`)과 프로세스 실행뿐이다.
- 훅 안의 예외는 지금처럼 `.orchestration/state/hook-errors.log` 에 기록하고 세션을 막지 않는다.

## 7. 설정 (`lib/orch/config.py`, `templates/config.toml`)
- `Config.auto_up` 속성: `[coordinator] auto_up` 이 없으면 true. 불리언이 아니면 설정 오류(`config.toml` 의 다른 잘못된 값과 같은 방식)다.
- `templates/config.toml` `[coordinator]` 에 `auto_up = true` 를 넣고, 파일 머리 주석에 한 줄 설명을 더한다.

## 8. 문서
- `templates/CLAUDE.section.md` 메인 규칙의 `- 세션을 다시 열었으면 /orca-team:up 을 먼저 실행한다.` → `- 코디네이터는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다(config.toml auto_up). 훅 안내가 없는데 코디네이터 상태가 의심되면 /orca-team:up 을 실행한다.`
- `skills/up/SKILL.md` 설명: 자동 부활이 꺼져 있거나 훅이 안내했을 때 쓰는 수동 복구라고 밝힌다. 단계는 그대로다.
- README "복구" 절: 자동 부활 동작, 끄는 법, 한계.
- 기본 설계 §13 에 "코디네이터 자동 부활 (2026-11-06, 0.5.1)" 소절, 상태 문서 갱신.

## 9. 한계
- 프로세스는 살아 있는데 멈춘 코디네이터(권한 창 대기, 응답 없음)는 `alive` 로 보여 감지하지 못한다. 지금과 같다.
- Orca 앱이 꺼져 있으면 살리지 못하고 확인 불가 문구만 넘긴다.
- 메인 세션 밖에서 코디네이터가 죽으면 다음 메인 프롬프트(최대 60초 간격) 또는 세션 시작까지 알지 못한다.

## 10. 테스트

기존 504개에 더한다. `PYTHONWARNINGS=default bash orca-team/tests/run.sh` 가 경고 없이 통과해야 한다.

- `tests/test_orca.py`: `terminal_state` 의 alive, 닫힌 핸들(stale), 셸만 남음, 파싱 실패, 그 밖의 오류 코드. `terminal_alive` 가 같은 결과를 내는지.
- `tests/test_autoup.py`
  - 4절 조건을 하나씩 어긴 경우의 `decide` 결과와 문구
  - 확인 간격(60초 안에는 확인하지 않음, 기록이 깨졌으면 확인)
  - pid 잠금(살아 있는 pid 가 있으면 spawn 하지 않음)
  - `spawn_up` 이 가짜 orca 로 실제 `orch up` 을 띄워 `run.json` 의 `coordinator_handle` 이 바뀌고 pid·로그 파일이 생기는지
- `tests/test_hooks.py`
  - 메인 세션의 SessionStart·UserPromptSubmit 이 다시 띄우고 문구를 돌려주는지
  - 코디네이터·워커 세션, 세팅 중, `auto_up = false`, 살아 있음에서는 띄우지 않는지(자동 꺼짐은 문구만)
  - 훅 예외가 세션을 막지 않는지
- `tests/test_templates.py`·`test_config.py`: `auto_up` 기본값과 잘못된 값, CLAUDE.section·up 스킬 문구
- `tests/test_cli.py`: 버전 0.5.1

## 11. 버전과 실환경 확인
- 0.5.1. `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`.
- 실환경 확인(사용자 승인 뒤):
  1. 시험 프로젝트에서 코디네이터 터미널만 닫고 메인에 요청을 보낸다. 자동으로 다시 뜨는지, 메인이 한 줄로 알리는지, 그 요청이 처리되는지 본다.
  2. Orca 를 재시작한 뒤 메인 세션을 다시 연다(SessionStart). Orca 재시작은 별도 승인이 필요하다.
  3. 바쁜 코디네이터가 잠깐이라도 shell-only(dead)로 보이지 않는지 본다.
  4. Orca 재시작 뒤 Orca 가 코디네이터를 되살리면 새 핸들로 바뀌어 옛 핸들이 stale 로 보이는지 본다(중복 실행 위험).

## 12. 범위 밖
- 멈춘(hung) 코디네이터 감지와 강제 재시작
- 상시 감시 프로세스
- 워커 자동 재시작(코디네이터의 `orch status` next 가 이미 다룬다)

## 13. 실환경 확인 결과 (2026-11-06, 시험 프로젝트 "메모 웹앱")

| 확인 | 결과 |
|---|---|
| init 이 0.5.1 로 `auto_up = true` 를 깔고 SessionStart 에서 확인 기록(`auto-up.json`)을 남김 | 통과 |
| 코디네이터 터미널을 끊은 뒤(connected false) 첫 메인 프롬프트에서 dead 판정, `orch up` 분리 실행, 새 코디네이터 1개 | 통과 |
| 새 코디네이터가 같은 Run(`run_id` 그대로)에 다시 붙고 `launched_at` 이 지워짐 | 통과 |
| 메인이 답변 첫 줄에 다시 띄우는 중이라고 한 줄로 알림 | 통과 |
| 코디네이터가 꺼져 있는 동안 보낸 요청을 새 코디네이터가 처리(약 60초 뒤 planner 태스크 시작) | 통과 |

- 관찰: `orca terminal close --terminal <코디네이터>` 가 `runtime_error: tab_not_found` 를 돌려줬지만 터미널은 연결이 끊겼다(Orca 1.4.245). 자동 부활은 이 상태를 dead 로 봤다.
- 확인하지 않은 것: Orca 재시작 뒤 SessionStart 부활, §11 의 바쁜 코디네이터 오판 여부.

