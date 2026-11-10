# orca-team 고정 에이전트 설계

- 날짜: 2026-11-09
- 대상: orca-team 0.6.0(`orca-team/`). 선행 설계는 `2026-10-30-orca-team-design.md`(3계층과 리뷰 루프), `2026-11-06-orca-team-auto-up-design.md`(코디네이터 자동 부활), `2026-11-08-orca-team-batch-verify-design.md`(묶음 검증·수렴형 리뷰)다.
- 상태: 사용자가 브레인스토밍 결정과 설계 1–6절(전체 구조, 고정 에이전트 생명주기, `orch drive`, 메인 규칙·훅·리뷰 pane, 설정·상태·이전, 오류 처리·테스트)을 승인했다. 버전은 0.7.0 으로 정했다.

## 1. 목적

지금은 지시가 올 때마다 코디네이터가 직무 워커를 새로 띄우고 spec 을 주입한다. 수정 라운드도 새 워커가 한다(`orch fix` 가 이전 워커를 해제하고 새로 띄운다). 그래서 세 가지 비용이 든다.

- 워커가 앞 태스크와 앞 라운드의 결정을 모른다. 매번 문서를 다시 읽고 같은 판단을 다시 한다.
- 메인 → 코디네이터 → 워커로 넘길 때마다 맥락이 요약되며 빠진다.
- 코디네이터 LLM 한 세션이 정해진 절차(완료 대기, 리뷰, accept, fix, 다음 태스크)와 판단(워커 질문 답, 요청을 기획 태스크로, 실패 수정 지시)을 함께 맡는다. 절차를 LLM 이 돌리면 매 단계가 토큰과 시간을 쓰고 규칙을 놓칠 수 있다.

세 가지를 바꾼다.
1. 코디네이터 LLM 을 없앤다. 정해진 절차는 `orch drive` 프로세스가 돌리고, 판단은 메인이 맡는다.
2. 직무 워커를 고정 에이전트 셋(생산·조사·테스트)으로 바꾼다. 같은 세션이 여러 태스크와 수정 라운드를 이어 맡고, 묶음이 끝나면 인수인계 파일을 쓰고 세션을 비운다.
3. 고정 에이전트마다 Orca 탭 하나에 작업 pane(왼쪽)과 리뷰 보기 pane(오른쪽)을 둔다.

바꾸지 않는 것: 리뷰는 지금처럼 리뷰마다 새 Codex 실행이다. 리뷰어가 컨텍스트를 쌓으면 작업자의 설명에 동화되고, 한 번 통과시킨 코드를 다시 보지 않게 된다. 중복 리뷰는 0.6.0 의 라운드 좁히기(`templates/review-round.md`: 앞 리뷰 파일과 그 뒤 커밋만 보고, 앞 P1 해결 여부와 새 P1 만 확인)가 이미 파일로 막는다.

## 2. 사용자 결정

| # | 질문 | 결정 |
|---|---|---|
| 1 | 범위 | 0.7.0: 코디네이터 제거(`orch drive` + 메인 판단), 고정 에이전트(리셋·인수인계·복구), 작업·리뷰 좌우 pane. 0.7.1: 테스트 에이전트를 Codex 로 바꾸고 그 리뷰를 Claude 가 하는 경로 |
| 2 | 에이전트 구성 | 생산 = planner·designer·developer, 조사 = director, 테스트 = tester(0.7.0 은 Claude) |
| 3 | 모델 | 에이전트마다 하나. 기본값 생산 opus, 조사 opus, 테스트 sonnet. designer 태스크도 생산 에이전트의 모델로 돈다 |
| 4 | 병렬 | 고정 에이전트가 바쁠 때 준비된 독립 태스크는 임시 워커(지금 방식)로 돌린다. `max_parallel` 상한, 설정으로 끌 수 있다 |
| 5 | 기존 방식 | 남기지 않는다. 완전히 바꾸고 `orch upgrade` 로 옮긴다. 되돌리려면 0.6.0 커밋을 체크아웃한다 |
| 6 | 0.6.0 실환경 확인 | 0.7.0 구현 전에 지금 구조로 한다 |
| 7 | 판단 주체 | 메인. `orch drive` 는 판단이 필요한 일만 메인에 올린다 |
| 8 | 리뷰어 | 고정하지 않는다(사용자가 처음 낸 "리뷰어도 고정, 컨텍스트 누적" 안에 반대 의견을 냈고 사용자가 받아들였다). pane 은 고정이고 실행은 리뷰마다 새로 한다 |
| 9 | 리뷰 effort | 모든 직무의 `codex_effort` 를 `xhigh` 로 한다 |
| 10 | 세션 리셋 시점 | 묶음이 끝날 때. 오래 쉬었다가 시작할 때는 리셋하지 않는다(캐시가 만료된 뒤에는 인수인계를 쓰는 턴이 긴 컨텍스트를 다시 읽으므로 아끼는 것이 없다) |

### 2.1 구현 계획을 쓰며 바뀐 것 (2026-11-09)

1. Orca 1.4.240 의 `worker-show --json` 은 간략형(`result.state`·`stage`·`terminal`·`liveness`)만 준다. 지금 `orca.worker_info` 가 읽는 `result.worker.*` 는 `--full` 이 있어야 나와 바쁨 판정이 늘 "확인 불가"였다. `--full` 을 붙이고 두 모양을 다 읽는다. `review.py` 가 부르던 `orchestration worker-retain` 은 지금 Orca 에 없는 명령이라 지운다.
2. 토큰 갱신 태스크는 관제가 만들지 않고 `decide` token_update 로 메인에 올린다. 어느 developer 태스크가 "UI 기반" 인지는 planner 가 정한 제목으로만 알 수 있어 기계적으로 고를 수 없다. 보강이 끝난 뒤 화면 `fix --supplement` 는 관제가 한다.
3. 워커 질문 답은 `orch answer` 가 그 자리에서 decide 를 닫고, Orca 회신만 `reply` 요청으로 큐에 넣는다. 큐 명령은 `start`, `start-ready`, `fix`, `accept`, `drop`, `reply`, `design-decide`, `scope`, `agent-reset` 이다.
4. 태스크별 잠금 파일(§6.4)은 두지 않는다. 리뷰 자식 프로세스는 지금의 `review.pid` 잠금을 쓰고, 관제는 리뷰가 끝난 뒤에만 그 태스크 상태를 쓴다.
5. 고정 에이전트에 넣는 태스크의 모델 지정을 무시했다는 기록은 관제 로그가 아니라 이력 entry 의 `model_ignored` 에 남긴다.
6. 훅: `agents.json` 의 작업 터미널 handle 이면 세션 표시가 없어도 워커로 본다(`/clear` 뒤 SessionStart 가 새 세션 id 로 온다). 워커 세션은 디스패치 프리앰블마다 직무를 다시 적는다(한 세션이 여러 직무를 받는다). 메인 Bash 의 `orch drive` 를 막고, `orch drive` 는 `CLAUDECODE` 환경(Claude 세션 안)에서 시작을 거부한다.
7. accept·drop 은 outbox progress 를 쓰지 않는다(메인에는 이정표만 간다). 코디네이터용 `orch notify --json` 질문을 지운다.
8. 자동 부활은 Orca 를 부르지 않고 관제 pid·heartbeat 로 판정한다. pid 는 살아 있는데 heartbeat 가 2분 넘게 멈췄으면 띄우지 않고 메인에 알린다.
9. `start_unknown` 은 시작 기록 뒤 60초가 지나도 그대로일 때 복구한다. 관제는 decide 를 올린 라운드를 재확인에서 다시 처리하지 않는다(`decide_round`).
10. `orch upgrade` 는 CLAUDE.md 의 orca-team 절(`<!-- orca-team -->` 부터 끝까지)을 새 템플릿으로 바꾸고, `[agents.*]` 는 프로젝트에 있는 직무만 넣는다.
11. 최대 라운드를 넘긴 뒤 "한 번 더" 를 실행할 방법이 없던 것을 `orch fix --extra-round` 로 메운다(§5.5).
12. §10.3: 업그레이드는 새 Run 을 만들지 않는다. `run_id` 를 그대로 두고, 관제가 `run-use` 로 같은 Run 에 다시 붙어 reconcile 이 진행 중(working·reviewing) 태스크를 이어받는다. 그래서 진행 중 태스크는 업그레이드를 막는 절대 조건이 아니다. 기본은 기다리는 쪽이 안전해 거부하고, 플러그인을 이미 올려 옛 코디네이터가 설정 오류로 명령을 못 쓰는 경우를 위해 `orch upgrade --force` 가 이 거부만 건너뛴다(코디네이터가 떠 있는 거부는 건너뛰지 않는다). 업그레이드 뒤에는 새 CLAUDE.md 규칙을 읽도록 메인 세션을 새로 연다. 세션 시작 훅은 `[coordinator]` 설정 오류를 보면 `orch upgrade` 를 안내한다.
13. §5.1: `agents.json` 에 `workspace` 필드는 없다. 워커는 항상 `--worktree current` 로 띄운다(§5.3 의 `<workspace>` 도 같다).
14. §6.4: 메인 쪽 검증은 spec 형식과 상태 존재만 본다. 상태 전이·소유 경로 잠금은 관제가 요청을 실행할 때 검사한다. `orch status` 는 Orca 를 부르지 않는다고 적었으나 `next` 를 만들며 `worker-show` 를 읽기 전용으로 부른다(0.6.0 도 같았다). 이 호출이 메인을 Run 에 묶지 않는지는 실환경 검증에서 확인한다.
15. 관제 상태 파일(`pid`, `heartbeat`, `queue/`, `drive.log`)은 `.orchestration/state/drive/` 아래에 둔다.
16. `orch notify --type question` 을 없앴다. `comm.TYPES` 는 progress·done 이다(`orch answer` 가 닫을 수 없는 막다른 길이었다).
17. 시작·수정 보완(코드 리뷰 뒤).
    - `orch start T` 가 새 태스크를 지금 시작할 수 없으면(에이전트가 리셋 중이거나 바쁜데 overflow 가 꺼져 있음, 선행 미완) `pending` 상태로 저장하고 exit 0 과 `{"status": "waiting", "reason": "agent"|"deps"}` 를 돌려준다. 관제가 start-ready 로 나중에 시작한다.
    - `orch fix T` 가 에이전트 자신의 현재 태스크면 자기 뒤에 줄 서지 않는다. 살아 있는 워커에 `--force` 를 주면 그 워커를 버리고 새로 띄운다.
    - 선행 태스크가 dropped 인 pending 태스크는 `escalation` decide 를 한 번 올린다.
    - `launch_failed` 태스크도 `escalation` decide 를 한 번 올린다. 남은 워커 확인은 관제가 한다.
    - 메인에게 보이는 `next` 힌트는 메인이 실행할 수 있는 명령만 적고, 관제만 하는 단계는 "관제가 한다" 로 적는다.
    - 관제는 `orch status` 의 `next` 를 따르지 않는다. 태스크마다 `reconcile_task` 로 상태를 다시 맞추고, `next` 는 메인이 읽는 안내 문장이다. 이 문서에서 관제가 `next` 로 이어 간다고 쓴 곳은 `reconcile_task` 로 읽는다.
    - 소유 경로 잠금 때문에 지금 시작할 수 없는 새 태스크도 `pending` 으로 저장하고 `{"status": "waiting", "reason": "lock"}` 을 돌려준다.

## 3. 실험 결과 (2026-11-08)

Orca 1.4.240, Claude Code 2.1.319, codex-cli 0.166.0, 버리는 시험 프로젝트에서 했다. 원본 기록은 로컬 `.superpowers/sdd/persist/experiments.md`(커밋하지 않음)다.

| 실험 | 방법 | 결과 |
|---|---|---|
| E1 고정 Claude 워커 | `worker-start --spec-file … --agent claude --keep-open` 다음 `task-create` + `worker-start --task <id> --terminal <handle> --worktree <같은 작업 공간>` | 통과. 두 번째 태스크가 첫 태스크 대화의 암호를 기억했다. worker_done 정상, 터미널은 `retained` |
| E2 Claude 세션 비우기 | `orca terminal send --text "/clear" --enter --wait-submit 10`, tui-idle 을 기다린 뒤 다음 태스크 | 통과. 암호를 몰랐다. worker_done 정상(디스패치 프리앰블이 태스크마다 다시 들어간다) |
| E3 좌우 pane | `orca terminal split --terminal <작업> --direction vertical --command …`, `set-pane-title` | 통과. 두 pane 이 같은 `tabId`. split 뒤에도 왼쪽에 `--terminal` 태스크 정상 |
| E4 고정 Codex 워커 | E1 과 같게 `--agent codex` | 통과. 두 번째 태스크가 암호를 기억했다 |
| E4 Codex 세션 비우기 | `terminal send "/new"` 직후 tui-idle 만 보고 태스크 | 처음에는 실패(`start_unknown`, `turn_start_unobserved`). 새 세션 화면을 그리는 동안 넣은 입력이 사라졌다. `--retry-of` 는 거부됐다. `worker-abandon` → `task-update --status ready` → 같은 `--terminal` 로 다시 start 하자 통과(암호를 몰랐다) |
| E5 셸 프로세스 코디네이터 | 셸 터미널에서 스크립트가 `run-create`, `worker-start --worktree current`, `check --wait --types worker_done,escalation,question,status` | 통과. worker_done 을 받았다(JSON 앞에 `_keepalive` 줄). 기다리지 않던 180초 동안 Orca 가 셸에 입력을 넣지 않았고, 메시지는 다음 `check` 에서 받았다 |

확인한 제약:
- `worker-start --terminal` 은 `--model`·`--effort` 와 같이 쓸 수 없다. 고정 에이전트의 모델은 처음 띄울 때 정해진다.
- 디스패치하면 Orca 가 탭 제목을 그 태스크 spec 첫 줄로 바꾼다.
- `--direction vertical` 이 좌우, `horizontal` 이 위아래다.

확인하지 못한 것(승인이 필요해 실환경 확인으로 미룬다): Orca 재시작 뒤 고정 에이전트 터미널과 디스패치 상태, 닫힌 고정 에이전트 터미널에 `send --to dispatch:` 를 보냈을 때 다시 열리는 동작.

## 4. 구조

```
메인 (사용자 터미널, Claude)
  ├─ orch watch (백그라운드): drive·기획·디렉터 워커 메시지를 받는다
  └─ 판단: 워커 질문 답, 요청 → 기획 태스크, 실패 수정 지시, 디자인 승인 해석
관제 탭  : orch drive (셸 pane, Run 주인) — 절차, 세션 리셋, 복구
생산 탭  : [ 생산 에이전트 (Claude opus)    | orch review-view production ]
조사 탭  : [ 조사 에이전트 (Claude opus)    | orch review-view research ]
테스트 탭: [ 테스트 에이전트 (Claude sonnet) | orch review-view test ]
임시 워커: 고정 에이전트가 바쁠 때 독립 태스크. 지금처럼 태스크마다 새 탭
```

- Run 은 관제 탭의 셸에서 도는 `orch drive` 가 만들고 갖는다(E5). Orca 메일(worker_done, question, escalation)은 drive 가 받는다.
- 메인은 지금처럼 `orca orchestration` 을 직접 부르지 않는다. 메인 터미널에서 `worker-start` 를 부르면 Orca 가 메인에 새 Run 을 묶고 알림을 사용자 입력란에 넣는다. 그래서 Orca 를 건드리는 명령은 모두 drive 가 실행한다(§6.4).
- 태스크 spec 에는 지금처럼 그 태스크 직무의 정의(`.orchestration/roles/<직무>.md`)가 들어간다. 고정 에이전트 하나가 태스크마다 다른 직무를 맡는다.
- 없어지는 것: 코디네이터 LLM 세션, `templates/coordinator.md`(프로젝트의 `.orchestration/coordinator.md`), `orch tell`, `orch wait`, `[coordinator]` 설정.

## 5. 고정 에이전트 (`lib/orch/agents.py`, 새 파일)

### 5.1 상태 `.orchestration/state/agents.json`

에이전트 이름(`production`, `research`, `test`)마다 다음을 적는다. drive 만 쓴다.

| 필드 | 뜻 |
|---|---|
| `handle` | 작업 pane 터미널 handle. 없으면 `null`(아직 안 띄웠거나 죽었다) |
| `review_handle` | 리뷰 보기 pane handle |
| `tab_id` | 탭 id |
| `workspace` | 작업 공간 selector(`worker-start --worktree` 에 쓴다) |
| `model`, `effort` | 띄울 때 쓴 값 |
| `task` | 지금 맡은 태스크 id. 없으면 `null` |
| `queue` | 이 에이전트를 기다리는 수정 라운드 태스크 id 목록(§5.5) |
| `since_reset` | 마지막 리셋(또는 처음 띄운 뒤) 처리한 태스크 수 |
| `reset_pending` | 묶음이 끝나 다음 태스크 전에 리셋해야 하면 `true` |
| `launched_at`, `last_reset_at` | 시각 |

태스크 상태(`.orchestration/state/tasks/T-xxx.json`)에 `agent` 필드를 더한다. 값은 에이전트 이름 또는 `temp`(임시 워커)다.

### 5.2 처음 띄우기

그 에이전트의 첫 태스크를 넣을 때 띄운다.
1. 태스크 spec 으로 `orca orchestration worker-start --task <id> --keep-open --agent claude --model <모델> --effort <effort> --worktree current` 를 drive 터미널에서 부른다(Run 은 drive 것).
2. 받은 터미널에 리뷰 pane 을 만든다: `orca terminal split --terminal <handle> --direction vertical --command ".orchestration/bin/orch review-view <에이전트>"`.
3. `set-pane-title` 로 두 pane 이름을 "<제목> (Claude)"·"리뷰 (Codex)" 로, `terminal rename` 으로 탭 이름을 "orca-team <제목>" 으로 정한다.
4. `agents.json` 을 쓴다.

### 5.3 태스크 넣기

1. 에이전트가 비어 있는지 본다: `agents.json` 의 `task` 가 `null` 이고 `handle` 터미널이 살아 있다(`orca.terminal_state`).
2. `reset_pending` 이면 먼저 §5.6 리셋을 한다.
3. `orca orchestration task-create --spec-file <spec>` 로 태스크를 만들고 `worker-start --task <id> --terminal <handle> --worktree <workspace>` 로 넣는다.
4. 탭 이름을 "orca-team <제목>" 으로 되돌린다(디스패치가 바꾸므로).
5. 태스크 상태에 `agent`, `dispatch_id` 를, `agents.json` 에 `task` 를 적는다.

worker_done 이 오면 drive 가 `agents.json` 의 `task` 를 비운다. 리뷰를 기다리는 동안 에이전트는 다음 태스크를 받을 수 있다.

### 5.4 배정 규칙 (`orch start-ready`)

- 지금처럼 `pending` 태스크 중 선행이 끝났고 소유 경로 잠금이 겹치지 않는 것을 `tasks.toml` 순서로 본다.
- 직무 → 에이전트는 `config.toml` `[agents.*].roles` 로 찾는다. 어느 에이전트에도 없는 직무는 임시 워커로만 돈다.
- 에이전트가 비어 있으면 그 에이전트에 넣는다. 에이전트의 `queue`(수정 라운드)가 차 있으면 새 태스크보다 그것을 먼저 넣는다.
- 에이전트가 바쁘고 `[overflow] enabled = true` 면 임시 워커로 띄운다. 끄면 그 태스크는 `waiting`(reason `agent`)으로 남는다.
- `max_parallel` 은 working 태스크 전체(고정 에이전트 + 임시 워커) 수의 상한이다. 지금과 뜻이 같다.

### 5.5 수정 라운드 (`orch fix`)

- 고정 에이전트가 한 태스크(`agent` 가 에이전트 이름)는 같은 에이전트가 고친다. 지금 `orch fix` 가 하는 `worker-release` 를 고정 에이전트에는 하지 않는다(해제하면 터미널이 닫힌다).
- 에이전트가 비어 있으면 바로 넣고, 바쁘면 `queue` 에 넣는다. 수정은 임시 워커로 넘기지 않는다.
- 임시 워커가 한 태스크의 수정은 지금처럼 이전 워커를 해제하고 새 임시 워커를 띄운다.
- `orch accept`·`orch drop` 도 고정 에이전트는 해제하지 않는다. `drop` 할 때 그 에이전트가 이 태스크를 하는 중이면 지금 규칙대로 거부한다.
- 새 옵션 `orch fix T-xxx --extra-round`: 최대 라운드를 넘긴 태스크를 사용자가 "한 번 더"로 정했을 때만 쓴다. 지금은 최대 라운드에서 `orch fix` 가 거부되고 넘길 방법이 없다. 이력에 `extra_round: true` 를 남긴다.

### 5.6 세션 리셋 (`templates/handoff.md`, 새 파일)

시점:
- tester 태스크를 accept 해서 묶음이 끝나면, `since_reset` 이 1 이상인 고정 에이전트마다 `reset_pending = true` 로 둔다.
- 사용자가 `orch agent reset <에이전트>` 로 요청할 때.

순서(에이전트가 비어 있을 때 drive 가 한다):
1. 인수인계 태스크를 넣는다(§5.3 의 3단계). orch 태스크(T-xxx)가 아니라 Orca 태스크만 만든다. 그래서 `tasks.toml`·태스크 상태·리뷰·`max_parallel` 과 상관없다. spec 은 `templates/handoff.md` 로 만든다: `docs/handoff/<에이전트>.md` 를 덮어써서 이 프로젝트에서 정한 결정과 규칙, 남은 일, 다음 세션이 먼저 읽을 파일을 200줄 이하로 적고, 그 파일만 경로 지정 커밋한 뒤 worker_done 을 보낸다. worker_done 이 실패면 리셋을 멈추고 메인에 `decide`(escalation)를 올린다.
2. `orca terminal send --terminal <handle> --text "/clear" --enter --wait-submit 10`.
3. `orca terminal wait --for tui-idle` 뒤 5초를 더 기다린다(E4: 새 세션 화면을 그리는 동안 넣은 입력이 사라졌다). 그래도 놓치면 §5.8 의 `start_unknown` 처리가 받는다.
4. `since_reset = 0`, `reset_pending = false`, `last_reset_at` 을 적는다.

`docs/handoff/<에이전트>.md` 가 있으면 그 에이전트와 그 에이전트 직무의 임시 워커가 받는 모든 태스크 spec 에 "먼저 `docs/handoff/<에이전트>.md` 를 읽는다" 줄을 넣는다(`templates/worker-spec.md`). 이 파일은 리포트처럼 소유 경로와 상관없이 쓸 수 있고, 인수인계 태스크만 쓴다.

### 5.7 임시 워커

지금 `lib/orch/tasks.py` `launch` 그대로다(`worker-start --spec`, 직무 `[roles.*]` 모델, 끝나면 Orca 가 닫는다). 태스크 상태 `agent` 는 `temp` 다.

### 5.8 복구

| 상황 | drive 가 하는 일 |
|---|---|
| 태스크를 넣기 전 작업 터미널이 죽었다 | `handle` 을 비우고 그 태스크로 §5.2 를 한다(새 에이전트는 인수인계 파일을 읽는다) |
| 작업 중 에이전트가 worker_done 없이 죽었다 | 관제의 재확인(`reconcile_task`, §2.1)이 이어받는다. 리포트와 `[T-xxx]` 작업 커밋이 있으면 리뷰한다. 없으면 새 에이전트(§5.2)에 `orch fix --note "이전 작업자가 중간에 끝났다. [T-xxx] 커밋을 확인하고 이어서 한다"` 로 넣는다 |
| `start_unknown` | `worker-abandon` → 그 터미널을 `worker-release` 로 닫는다(늦게 입력이 들어가 같은 체크아웃에서 두 에이전트가 일하는 것을 막는다) → `task-update --status ready` → §5.2 로 새 에이전트를 띄운다. 같은 태스크에서 세 번째면 메인에 `decide`(escalation) |
| 리뷰 보기 pane 이 죽었다 | 다음에 태스크를 넣을 때 다시 split 한다. 리뷰에는 영향이 없다 |
| Orca 재시작 | drive 도 같이 죽는다. 메인 훅이 drive 를 살린다(§6.1). 새 drive 는 `agents.json` 의 handle 을 하나씩 확인해 죽은 것을 비우고, 다음 태스크 때 다시 띄운다 |

## 6. `orch drive` (`lib/orch/drive.py`, 새 파일)

### 6.1 실행과 생존 확인

- `orch up` 이 관제 탭을 만든다: `orca terminal create --worktree current --title "orca-team 관제" --command ".orchestration/bin/orch drive"`. drive 는 자기 터미널(`ORCA_TERMINAL_HANDLE`)에서 `orch bind` 처럼 Run 을 만들거나 다시 붙는다. `run.json` 의 `coordinator_handle` 은 `drive_handle` 로 바뀐다.
- drive 는 하나만 돈다(`state/drive.pid`). 바퀴마다 `state/drive.heartbeat` 에 시각을 쓴다.
- 생존 판정: pid 가 살아 있고 heartbeat 가 2분 안이다. 셸만 남고 drive 가 죽었으면 `orch up` 은 그 터미널에 `terminal send` 로 drive 를 다시 띄우고, 터미널까지 죽었으면 새 관제 탭을 만든다.
- 자동 부활은 0.5.1 그대로 메인 세션 훅이 한다(`lib/orch/autoup.py` 의 대상만 코디네이터에서 drive 로 바꾼다). 설정은 `[drive] auto_up`.
- 진행 로그는 관제 pane 과 `state/drive.log` 에 쓴다.

### 6.2 루프

바퀴마다 차례로 한다.
1. `orca orchestration check --wait --types worker_done,escalation,question,status --timeout-ms 10000`(받은 뒤에는 `--ack <deliveryId>`). `_keepalive` 줄은 건너뛴다(`orca.last_json`).
2. 받은 메시지를 §6.3 대로 처리한다.
3. 메인 요청 큐(§6.4)를 순서대로 실행한다.
4. 끝난 리뷰 자식 프로세스의 결과를 §6.3 대로 처리한다. 리뷰는 `orch review T-xxx` 자식 프로세스로 돌려 루프가 멈추지 않게 한다.
5. 60초마다 재확인(`reconcile_task`, §2.1)으로 메시지 없이 끝난 일(죽은 워커, 끊긴 리뷰)을 찾아 처리한다.

### 6.3 이벤트 처리

`templates/coordinator.md` 의 절차를 코드로 옮긴다. 판단이 필요한 것만 메인에 `decide`(§6.5)로 올린다.

| 이벤트 | drive 가 자동으로 | 메인에 올림 |
|---|---|---|
| worker_done 성공 | 인수인계 태스크면 리셋 다음 단계(§5.6). 그 밖에는 `orch review` | |
| worker_done 실패 | | `decide` worker_failed |
| question(워커 `orca orchestration ask`) | | `decide` worker_question |
| escalation | | `decide` escalation |
| APPROVE | `orch accept`. planner 태스크면 `orch plan apply`(spec front matter `plan: append` 면 `--append`). tester 태스크면 묶음 후속과 리셋 표시. 그다음 start-ready | planner 면 progress(태스크 목록: id, 직무, 제목, 선행). tester 면 progress(묶음 완료) |
| APPROVE, 승인 대상 직무 | accept(→ awaiting_user), start-ready, 승인 대상이 모두 대기면 시스템 추가 제안 처리 뒤 `orch design-review` | approval(지금과 같음) |
| REQUEST_CHANGES | `orch fix`(리뷰 중 생긴 소유 경로 안 추적 안 된 파일이 있으면 note 에 넣는다), start-ready | |
| REQUEST_CHANGES, 최대 라운드 | | `decide` max_rounds |
| orch 사전 검사 반려가 같은 테스트 실패로 2라운드 | | `decide` repeat_test_failure |
| `orch review` exit 2, `[test]` 오류 | | `decide` test_config |
| `orch review` exit 2, 테스트 잠금 대기 초과 | 다음 바퀴에 다시 리뷰 | |
| accept·drop 의 워커 해제 실패 | | progress |
| 묶음 후속(`orch followups`) | 원인 태스크를 아는 회귀마다 회귀 수정 태스크, P2·P3 는 묶음 정리 태스크 하나(지금 코디네이터 규칙과 같은 spec 형식, `출처: T-xxx followups`) | `decide` planning_gaps, 원인 모를 회귀는 `decide` unknown_regression |
| 시스템 추가 제안 | designer 리포트 마지막 라운드의 `### 시스템 추가 제안` 원문을 모아 director 보강 태스크, 보강이 done 이면 제안한 화면마다 `fix --supplement` | `decide` token_update(UI 기반 developer 태스크에 토큰 반영, §2.1 의 2) |
| 모두 done·dropped | `orch followups --rest` 처리, `[test]` 가 있으면 `orch test --full`, 실패하면 회귀 수정 태스크(`출처: final followups`) | 통과하면 done(결과 요약, 바뀐 주요 파일, 태스크별 판정과 라운드 수, 마지막 full 결과) |

spec 을 만드는 태스크(회귀 수정, 묶음 정리, 보강, 토큰 갱신)는 drive 가 `.orchestration/tasks/T-xxx.md` 를 쓰고 그 파일만 경로 지정 커밋한다. 재시작한 drive 는 지금처럼 `출처:` 줄과 `docs/design/direction.md` 기록으로 이미 만든 태스크를 다시 만들지 않는다.

### 6.4 메인 요청 큐 `.orchestration/state/drive/queue/`

메인이 Orca 를 건드리는 명령(`orch start`, `start-ready`, `fix`, `accept`, `drop`, `design-decide`, `scope`, `agent reset`, 그리고 `orch answer` 가 워커 질문에 답할 때의 Orca 회신 `reply`)을 실행하면:
1. 그 자리에서 검증한다: spec 형식, 상태 전이, 소유 경로 잠금. 실패하면 바로 오류로 끝난다.
2. 요청 파일(`<번호>.json`: 명령, 인자, 시각)을 큐에 넣고 결과 파일(`<번호>.result.json`)을 120초까지 기다려 출력한다.
3. 120초 안에 끝나지 않으면 "drive 가 처리 중"을 출력하고 끝난다. 결과는 outbox `result` 메시지로 온다.
4. drive 가 죽어 있으면 큐에 넣지 않고 "drive 가 꺼져 있다. `/orca-team:up`" 오류로 끝난다.

태스크 상태 파일을 쓰는 것은 drive 와 그 자식 프로세스뿐이다. 리뷰 자식 프로세스는 자기 태스크 파일만 쓰고(`review.pid` 잠금), drive 는 리뷰가 끝난 뒤에만 그 태스크를 쓴다(§2.1 의 4).

Orca 를 부르지 않는 명령(`orch status`, `task new`, `plan check`, `which`, `followups`, `test`, `watch`, `answer`(기획·디렉터 `ask`))은 지금처럼 바로 실행한다.

### 6.5 메인에 올리는 메시지 (outbox)

| 종류 | 언제 | 메인이 할 일 |
|---|---|---|
| progress | 이정표만: 기획 적용(태스크 목록), 묶음 완료, 워커 해제 실패 | 한두 줄로 전한다 |
| ask | 기획·디렉터 워커의 `orch ask-user`(지금과 같다) | 질문 표시 규칙대로 묻고 `orch answer` |
| approval | `orch design-review`(지금과 같다) | 묻고 `orch design-decide`(§7.3) |
| decide | §6.3 의 판단 필요 | §7.2 |
| result | 큐 요청이 120초 안에 끝나지 않았을 때의 결과 | 실패면 다시 판단한다 |
| done | 모두 끝났을 때 | 요약해 전한다 |

`decide` 형식:

```json
{"id": "out-0012", "type": "decide", "kind": "worker_failed", "task": "T-012",
 "summary": "리포트 한두 줄 요약",
 "files": ["docs/tasks/T-012/report.md"],
 "commands": ["orch fix T-012 --note \"<지시>\"", "orch drop T-012 --reason \"<이유>\""]}
```

worker_question 은 `question`(원문)과 Orca 메시지 id 를 더 담는다. 모든 `decide` 는 `orch answer <id> "<처리 내용>"` 으로 닫는다. worker_question 이면 이 답이 drive 를 거쳐 `orca orchestration reply` 로 워커에게 간다. 그 밖의 종류는 기록만 한다. 닫히지 않은 `decide` 는 `orch status` 의 `open_questions` 와 `orch reask` 에 나온다.

## 7. 메인 (`templates/CLAUDE.section.md`, `hooks/orca_team_hook.py`)

### 7.1 메인 규칙

- 역할 표에서 코디네이터 행을 지운다. 고정 에이전트는 태스크마다 디스패치 프리앰블로 시작하므로 `/clear` 뒤에도 워커로 판별된다.
- 메인은 산출물(기획 문서, 디자인, 코드, 테스트)을 직접 만들지 않는다. 할 수 있는 것: 태스크 spec(`.orchestration/tasks/*.md`) 작성과 경로 지정 커밋, `orch` 명령.
- 사용자 요청: 메인이 직접 기획 태스크를 만든다(`orch task new planner "<요약>"`, Target·Change·Constraints·Acceptance 를 채우고 Change 에 요청 원문, 증분 기획이면 front matter `plan: append` 와 Change 에 "기존 `docs/planning/` 을 읽고 추가 요청만 인터뷰한다. tasks.toml 의 기존 태스크는 그대로 두고 새 `[[task]]` 만 덧붙인다"), spec 커밋, `orch start T-xxx`.
- 진행 중 태스크에 범위를 더할 때: spec 에 "추가 범위"를 커밋하고 `orch scope T-xxx "<내용>"`(큐 → drive 가 `orca orchestration send --to dispatch:<id>`).
- `orch watch` 는 지금처럼 Bash `run_in_background: true` 로 하나 띄워 둔다.
- `orch tell` 은 없어진다. `/orca-team:up` 은 drive 와 watch 를 살린다.

### 7.2 `decide` 처리 기준

`decide` 에 적힌 파일만 읽는다. 기준은 `coordinator.md` 에서 옮긴다.

| kind | 기준 |
|---|---|
| worker_failed | 리포트를 읽고 지시로 풀 수 있으면 `orch fix --note "<원인과 지시>"`. 아니면 사용자에게 묻는다 |
| worker_question | `docs/planning/spec.md`, `contracts.md`, `interview.md`, `voice.md`, 태스크 spec, 직무 정의, `.orchestration/project.md` 로 먼저 답한다. 문서가 다루지 않는 제품 결정이거나 문서끼리 모순될 때만 사용자에게 묻는다. 코드에 아직 없는 디자인 토큰을 물으면 토큰을 반영한 태스크가 done 이 된 뒤 그 토큰 이름으로 답한다 |
| escalation | 내용을 정리해 사용자에게 묻거나 지시로 푼다 |
| max_rounds | 리뷰 요약과 함께 "한 번 더 / 중단"을 묻는다. 한 번 더면 `orch fix --extra-round`, 중단이면 `orch drop` |
| repeat_test_failure | 실패가 그 태스크 탓이 아닐 수 있다(소유 경로 밖 코드, 다른 워커의 커밋 안 된 파일). 리뷰 파일의 quick 출력 끝을 보고 사용자에게 묻는다 |
| test_config | 사용자가 직접 넣은 `[test]` 면 사용자에게 묻는다. 아니면 `[test]` 를 고치는 기획 태스크를 만든다 |
| planning_gaps | 문서로 답할 수 있으면 묶음 정리 태스크 Change 에 넣는다(`orch scope`). 제품 결정이 필요한 항목은 모아서 한 번에 묻는다. 넣기로 하면 증분 기획 태스크 |
| unknown_regression | 원인 태스크를 사용자와 정해 회귀 수정 태스크를 만든다 |
| token_update | 보강한 디자인 시스템 토큰을 UI 기반 developer 태스크에 반영할지 정한다. done 이면 토큰 갱신 태스크, working 이면 `orch scope`, 리뷰 중이면 결과를 기다린다 |

사용자에게 물을 때는 AskUserQuestion 을 직접 쓴다(질문 하나, 선택지 2–4개, 추천 하나).

### 7.3 `orch design-decide`

```bash
.orchestration/bin/orch design-decide out-0020 --approve T-031,T-032 --fix 'T-033=<사용자 요청 원문>'
```

메인은 사용자 답을 화면별 결정으로 정리해 넘긴다. 첫 인자는 그 approval 메시지 id 이고, 실행되면 그 질문을 닫는다(`state/answered.json`). orch 가 지금 코디네이터 규칙을 코드로 적용한다.
- 승인한 화면은 accept(awaiting_user → done).
- 고치는 화면은 `orch fix --note`(최대 라운드와 상관없다).
- 고치는 화면을 선행으로 둔 승인 대상 태스크는 승인했더라도 accept 하지 않고 "선행 디자인 T-xxx 가 바뀐다: <요청>. 바뀐 선행 디자인에 맞춘다" note 로 함께 fix 한다. 그 태스크가 working 이면 worker_done 뒤에 fix 한다.
- 어느 화면인지 알 수 없는 답은 메인이 사용자에게 다시 묻는다.
- 끝나면 start-ready.

### 7.4 훅

- `role-mark`: `[orca-team:coordinator]` 판별과 `env_coordinator` 를 지운다.
- `guard`: 메인에게 `.orchestration/`(state 제외) 쓰기를 허용한다. 메인 Bash 의 `orca orchestration` 직접 호출을 막고 해당 `orch` 명령을 안내한다. 코디네이터 전용 규칙(AskUserQuestion 금지, `check` 금지, `[main]` reply 금지)은 지운다.
- `session-start`·`stop`: 안내와 자동 부활 대상을 drive 로 바꾼다. watch 가 꺼져 있으면 지금처럼 안내한다.

## 8. 리뷰 보기 pane (`orch review-view <에이전트>`)

- 읽기 전용 셸 프로세스다. 태스크 상태 파일에서 `agent` 가 이 에이전트인 태스크의 리뷰를 찾는다.
- 리뷰가 시작되면 `T-012 r2 리뷰 중 (gpt-6.1-sol, xhigh)` 를 출력하고, 그 리뷰 run-dir(`~/.cache/orca-team/runs/<프로젝트>/…`)의 `progress.log` 를 따라 보여 준다.
- 끝나면 판정과 리뷰 파일 경로(`docs/reviews/T-012/r2.md`)를 출력하고 다음 리뷰를 기다린다. 2초마다 상태를 본다.
- 리뷰 실행은 drive 의 `orch review` 자식 프로세스다(리뷰 중 HEAD·추적 파일 변경 검사 그대로). 보기 창이 죽어도 리뷰에는 영향이 없다.
- 임시 워커 태스크의 리뷰는 관제 pane 로그에만 나온다.

## 9. 설정 (`templates/config.toml`, `lib/orch/config.py`)

```toml
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
```

- `[roles.*]` 는 지금과 같다(title, owns, 임시 워커용 `claude_*`, 리뷰용 `codex_*`). 템플릿의 모든 직무 `codex_effort` 를 `xhigh` 로 바꾼다.
- 검증: 한 직무가 두 에이전트에 있으면 오류. `[agents.*].roles` 의 직무가 `[roles]` 에 없으면 오류. `[agents]` 가 비면 모든 태스크가 임시 워커로 돈다.
- 모델 우선순위: 고정 에이전트는 `[agents.<이름>]` > `[defaults]`. 임시 워커는 지금 규칙(명령 옵션 > 태스크 front matter > `[roles.<직무>]` > `[defaults]`). 고정 에이전트에 넣는 태스크의 front matter 모델 지정은 무시하고 이력 entry 에 `model_ignored` 로 남긴다.
- `[coordinator]` 절이 남아 있으면 설정 오류로 보고 `orch upgrade` 를 안내한다.

## 10. 기존 프로젝트 (`orch upgrade`, `lib/orch/setup.py`)

1. 거부 조건: working·reviewing 태스크가 있거나 코디네이터 터미널이 살아 있으면 거부한다. 옛 Run 의 메일은 새 Run 으로 넘어가지 않는다. 안내: "진행 중 태스크가 끝나기를 기다린 뒤 코디네이터 탭을 닫고 다시 실행한다".
2. 0.6.0 갱신(규칙 파일 교체 등)을 지금처럼 먼저 한다. 0.5.x 프로젝트도 한 번에 0.7.0 이 된다.
3. 0.7.0 갱신:
   - `.orchestration/coordinator.md` 를 지운다.
   - `config.toml` 에 `[agents.*]`·`[overflow]`·`[drive]` 를 넣고 `[coordinator]` 를 지운다(`auto_up` 값은 `[drive]` 로 옮긴다).
   - 직무별 `codex_effort` 가 0.6.0 템플릿 값과 같으면 `xhigh` 로 바꾼다. 사용자가 바꾼 값은 두고, 바꾼 키를 출력한다.
   - CLAUDE.md 의 orca-team 절, 역할 정의(`roles/*.md`), `worker-spec` 을 새 템플릿으로 바꾼다.
   - `run.json` 의 `coordinator_handle` 을 지운다(drive 가 새 Run 을 만든다).
4. 바뀐 파일 목록과 다음 할 일(커밋, `/orca-team:up`)을 출력한다.

## 11. 바꾸는 파일

| 파일 | 변경 |
|---|---|
| `lib/orch/agents.py` (새) | 고정 에이전트 상태, 띄우기, 태스크 넣기, 리셋, 복구 |
| `lib/orch/drive.py` (새) | 루프, 이벤트 처리, 큐 실행, 태스크 생성 규칙 |
| `lib/orch/reviewview.py` (새) | `orch review-view` |
| `lib/orch/tasks.py` | 배정 규칙, 고정 에이전트 해제 생략, `--extra-round`, 큐 경유 |
| `lib/orch/setup.py` | `up`(관제 탭), `bind`(drive 터미널), `status`(에이전트 표, drive 생존), `upgrade` |
| `lib/orch/autoup.py` | 부활 대상을 drive 로 |
| `lib/orch/comm.py` | `tell`·`wait` 제거, outbox `decide`·`result`, 큐 |
| `lib/orch/ask.py` | `answer` 가 `decide` 를 닫고 worker_question 을 큐로 |
| `lib/orch/config.py` | `[agents]`, `[overflow]`, `[drive]` |
| `lib/orch/cli.py` | `drive`, `review-view`, `agent reset`, `design-decide`, `scope`, `fix --extra-round` 추가. `tell`·`wait` 제거 |
| `hooks/orca_team_hook.py` | §7.4 |
| `templates/coordinator.md` | 지운다. 판단 기준은 `CLAUDE.section.md` 로, 절차는 `drive.py` 로 |
| `templates/CLAUDE.section.md` | §7.1–§7.3 |
| `templates/worker-spec.md` | 인수인계 파일 읽기 줄, 질문 대상을 "코디네이터"에서 "메인"으로 |
| `templates/handoff.md` (새) | 인수인계 태스크 spec |
| `templates/roles/*.md` | "코디네이터" 언급을 "메인"으로 |
| `templates/config.toml` | §9 |
| `skills/up/SKILL.md`, `skills/init/SKILL.md` | 코디네이터 대신 drive |
| `README.md`, `docs/superpowers/2026-11-05-orca-team-status.md` | 구조 설명, 0.7.0 |
| `lib/orch/paths.py`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | 0.7.0 |

## 12. 오류 처리 요약

| 상황 | 처리 |
|---|---|
| drive 가 죽었다 | 메인 훅이 살린다. 새 drive 는 재확인(`reconcile_task`)으로 이어간다 |
| Orca 호출 실패 | 지금 `launch_failed`·`--retry` 규칙. 넘기면 `decide` escalation |
| 고정 에이전트 터미널이 죽었다, `start_unknown` | §5.8 |
| 리뷰 자식 프로세스가 죽었다 | 태스크가 `reviewing` 으로 남는다. 재확인(`reconcile_task`)이 다시 리뷰한다 |
| 큐 요청이 잘못됐다 | 검증 단계에서 바로 오류. drive 실행 중 실패면 결과 파일·`result` 에 오류 |
| 메인이 `decide` 에 답하지 않는다 | 그 태스크만 멈춘다. 다른 태스크는 계속 간다 |
| 두 프로세스가 같은 태스크를 쓴다 | 쓰는 쪽은 drive 와 리뷰 자식뿐이고 태스크별 잠금을 건다 |

## 13. 한계

- Orca 재시작과 닫힌 터미널 재개는 실험하지 못했다(§3). 실환경 확인에서 본다.
- 고정 에이전트 컨텍스트는 한 묶음 동안 쌓인다. 묶음이 아주 크면 Claude Code 자동 압축에 기댄다.
- 에이전트마다 모델이 하나라 designer 태스크도 생산 에이전트 모델(기본 opus)로 돈다. 비용이 지금보다 늘 수 있다.
- 선행으로 이어진 생산 태스크는 한 에이전트에서 차례로 돈다. 독립 태스크만 임시 워커로 나뉜다.
- 세션 비우기 뒤 준비 확인은 tui-idle 과 고정 대기(5초)다. 놓치면 `start_unknown` 처리로 새 에이전트를 띄운다. 그 경우 인수인계 뒤라 잃는 맥락은 없지만, 묶음 중간의 `start_unknown` 은 그 묶음의 맥락을 잃는다.
- 메인 컨텍스트가 `decide` 처리만큼 는다.

## 14. 테스트

- 가짜 orca(`tests/fakes/orca`)에 `worker-start --keep-open`·`--terminal`, `task-create`, `terminal send`·`split`·`read`·`wait`·`rename`·`set-pane-title`, `check --wait` 응답 순서(`_keepalive` 포함), `worker-abandon`, `task-update`, `reply` 를 더한다.
- `tests/test_agents.py`: 처음 띄우기와 split, 태스크 넣기, 바쁨 판정, 수정 큐 우선, 리셋 순서(인수인계 → `/clear` → 대기), 죽은 터미널 재기동, `start_unknown` 처리, 해제하지 않음.
- `tests/test_drive.py`: §6.3 표의 각 행, 큐(검증 실패, 120초 초과, drive 꺼짐), 묶음 후속 태스크 생성과 재시작 뒤 중복 없음, 마무리, drive 재시작.
- `tests/test_reviewview.py`: 가짜 run-dir 로 시작·진행·판정 출력.
- `tests/test_config.py`: `[agents]` 검증, 모델 우선순위, `[coordinator]` 오류.
- `tests/test_setup.py`(upgrade): 0.5.x·0.6.0 배치에서 0.7.0 으로, 거부 조건, `codex_effort` 바꾸기 규칙.
- `tests/test_hook.py`: 코디네이터 판별 제거, 메인의 `.orchestration/` 쓰기 허용과 state 차단, `orca orchestration` 차단.
- `tests/test_cli.py`: 새 명령, `tell`·`wait` 제거, 버전 세 곳.
- 실행: `PYTHONWARNINGS=default bash orca-team/tests/run.sh`, 경고 없이 통과.

## 15. 버전과 실환경 확인

- 버전 0.7.0. 0.6.0 실환경 확인(0.6.0 설계 §12)을 지금 구조로 먼저 끝낸다.
- 0.7.0 실환경 확인(버리는 시험 프로젝트, 사용자가 기획·디렉터 질문에 직접 답한다):
  1. `/orca-team:init` → 관제 탭과 drive 가 뜨는지.
  2. 기획 → 디렉터 → 디자인 → 개발 → 묶음 테스트 한 바퀴. 생산·조사·테스트 탭이 좌우 pane 으로 뜨고 리뷰 보기 창이 리뷰를 보여 주는지.
  3. 수정 라운드가 같은 에이전트로 가는지, 독립 태스크가 임시 워커로 나뉘는지.
  4. 묶음 끝 리셋: `docs/handoff/*.md` 커밋, `/clear`, 다음 태스크가 인수인계 파일을 읽는지.
  5. 사용자 승인을 받아 고정 에이전트 터미널을 닫고 복구되는지, Orca 를 재시작하고 drive·에이전트가 살아나는지.
  6. 태스크별 라운드 수, 전체 시간, 메인 컨텍스트 크기를 이 문서 §17 에 적는다.
- 실환경 확인이 끝나기 전에는 main 에 병합하지 않는다.

## 16. 범위 밖

- 테스트 에이전트를 Codex 로 바꾸고 그 리뷰를 Claude 가 하는 경로(0.7.1).
- 태스크 직무에 맞춰 `/model` 로 모델 바꾸기.
- 생산 에이전트 여러 개.
- 리뷰어 고정과 컨텍스트 누적.

## 17. 실환경 확인 결과

2026-11-09–10, Orca 1.4.250, Claude Code 2.1.319, codex-cli 0.166.0. 버리는 시험 프로젝트(할 일 웹앱, `node --test`)를 0.6.1 로 세팅한 뒤 `orch upgrade` 로 0.7.0 에 올렸다. 기획·설치 질문과(사용자 허락 뒤) 디렉터 질문은 컨트롤러가 추천안으로 답했다.

### 숫자

- 태스크 12개(기획 1, 디렉터 2, 디자이너 1, 개발 6, 테스터 2). 라운드: T-001·T-002·T-003·T-006 은 2라운드, 나머지 8개는 1라운드. T-010 은 리뷰가 한 번 무효가 되어 다시 돌렸다.
- 시간: 첫 요청부터 첫 done 까지 3시간 33분(22:30–02:03). 사용자 답을 기다린 시간을 포함한다. 그 뒤 README 요청(T-011) 10분, 묶음 테스트 재실행(T-012) 12분.
- 메인 컨텍스트: 끝에 19%(Opus, 1M). 고정 에이전트는 리셋 전 생산 30% 안팎, 조사 57%(자동 압축이 돌았다).
- 마지막 전체 테스트 186개 통과(0.2초).

### 통과

- 0.6.1 프로젝트에서 `orch up` 이 옛 `[coordinator]` 를 거부하고 `orch upgrade` 를 안내한다. upgrade 뒤 `/orca-team:up` 이 관제 탭과 drive 를 띄운다.
- 생산·조사·테스트 에이전트가 탭 하나에 작업 pane 과 리뷰 보기 pane 으로 뜬다. 리뷰 보기 pane 이 Codex 진행과 판정을 보여 준다.
- 메인 → 관제 큐(start, agent-reset, design-decide), ask 중계(기획 인터뷰, 디렉터 질문 2개, 디자인 시스템·화면 승인).
- 수정 라운드가 같은 에이전트의 같은 세션으로 간다(T-001, T-002, T-003, T-006).
- 화면 디자이너의 시스템 추가 제안 → 디렉터 보강 태스크 → 화면 수정 라운드 → 디자인 승인. token_update decide 는 메인이 스스로 처리했다(반영 태스크 생성, 뒤 태스크의 선행 추가).
- 생산이 바쁠 때 임시 워커로 넘긴다(T-004, T-009). 임시 워커 리뷰는 관제 로그에만 나오고, 끝나면 터미널이 닫힌다.
- 테스트 묶음이 끝나면 후속 태스크(P2 정리)를 만들고 리셋을 표시한다. 인수인계(`docs/handoff/<에이전트>.md` 커밋) → `/clear` → `since_reset` 0. 리셋 뒤 첫 태스크 spec 에 인수인계 읽기 줄이 들어가고 세션이 실제로 읽는다.
- 사용자 승인으로 테스트 에이전트 탭을 닫으면 관제가 handle 을 지우고, 다음 태스크 때 새 탭(작업 + 리뷰 보기)으로 다시 띄운다.
- drive 프로세스는 `CLAUDECODE` 없이 프로젝트 폴더에서 돈다. 메인이 `orch status` 를 30번 넘게 불러도 메인 터미널은 Run 에 묶이지 않는다(`run-current` 가 null).
- 쉬는 고정 에이전트는 worker-list liveness 가 `unverifiable` 이다. 8시간 동안 거짓 escalation 은 없었다.

### 확인하지 못한 것

- Orca 재시작 복구. 사용자가 이번에는 하지 않기로 했다.

### 고칠 것

P1:
1. 리뷰 HEAD 보호가 인수인계 커밋을 막는다. `review.py` 의 `BASE_EXCLUDE` 에 `docs/handoff/**` 가 없어, 리뷰 중 에이전트가 인수인계를 커밋하면 판정이 무효가 된다. 오류 문구도 "Codex 가 HEAD 를 바꿨다" 로 단정한다.
2. start_unknown 복구가 고정 에이전트에 같은 태스크를 두 번 디스패치한다. 리셋 직후 첫 디스패치가 `outcome_unknown` 으로 왔지만 세션은 2초 만에 일을 시작했다. 60초 뒤 관제가 디스패치를 닫고 새로 띄웠는데, `--keep-open` 터미널은 worker-release 로 닫히지 않았다. 두 세션이 같은 체크아웃에서 같은 태스크를 했고 옛 탭이 고아로 남았다. 다시 띄우기 전에 그 터미널이 일하는지 보고, 다시 띄울 거면 옛 터미널을 확실히 닫는다. 리셋 직후 디스패치가 왜 관찰되지 않는지도 본다.
3. 인수인계 디스패치 실패를 원인 없이 올린다. 작업 세션이 자동 압축 중이면 worker-start 가 실패한다. 영수증이 로그·decide 어디에도 없고, 만든 Orca 태스크가 고아로 남고, 그 호출 동안 관제가 약 60초 멈춘다. commands 가 같은 `orch agent reset` 뿐이라 메인이 같은 실패를 되풀이했다. 일시 실패는 관제가 몇 틱 뒤 스스로 다시 하고, 반복될 때 원인과 함께 올린다.

P2:
4. 묶음 후속 태스크가 리셋 표시보다 먼저 시작해 리셋 전 세션으로 들어간다(`batch_done` 순서).
5. `orca.task_workers`: worker-list 항목에 title 이 없고 끝난 워커도 나오는데, state·terminalState 를 거르지 않아 끝난 워커를 남은 워커로 센다.
6. Claude 자체 안전 확인으로 생긴 "needs a person" escalation 의 decide 요약이 Orca 영어 원문을 300자에서 자르고, 메인이 쓸 수 없는 `orchestration send` 를 안내하며, commands 가 비었다. 2분 뒤 자동 차단으로 워커가 이어 가도 decide 가 열린 동안 관제는 그 태스크를 멈춘다.
7. 관제 터미널 제목이 Codex 실행 뒤 "Codex" 로 바뀌고, 고정 에이전트 작업 pane 제목이 첫 태스크 제목으로 남는다.

P3:
8. Orca 를 부르기 전에 `launch_failed` 를 먼저 적어, 상태만 보면 실패로 보인다.
9. 큐가 비면 요청 id 가 `q-000001` 부터 다시 매겨져 drive.log 에서 요청을 구분할 수 없다.
10. drive.log 에 수정 라운드 시작과 죽은 터미널 잊기 줄이 없다.
11. `since_reset` 이 수정 라운드도 센다. §4 표의 "처리한 태스크 수" 와 뜻을 맞춘다.
12. done 요약의 "바뀐 주요 파일" 앞쪽이 `.orchestration/tasks/*.md` 로 채워진다.
13. upgrade 뒤 config 머리 주석이 아직 코디네이터와 "orch start 가 거부한다" 로 설명하고, `[coordinator]` 를 지운 자리에 빈 줄 2개가 남는다.

### 환경 관찰(orca-team 결함 아님)

- 사용자 전역 훅(첫 Bash·Write 에 사실 확인을 요구)이 워커·에이전트의 턴을 늘린다.
- 리뷰 보기 pane 에 사용자 Codex 설정의 MCP 연결 실패·훅 차단 ERROR 줄이 섞인다. 판정은 정상으로 나온다.
- 컨트롤러가 슬래시 명령을 보낼 때는 텍스트와 Enter 를 따로 보내야 실행된다.
