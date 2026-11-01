> orca-team 내장 Codex 래퍼(`orca-team/scripts/codex-run.sh`)와 이미지 생성(`/orca-team:image`)의 설계 문서다. orca-team 에는 `run` 스킬이 없다. 본문의 `run` 스킬 이야기는 설계 당시 기록이다.

# 02 — Codex 호출 계약

이 문서가 정본이다. 래퍼 `orca-team/scripts/codex-run.sh` 와 이 문서가 어긋나면 둘 다 고친다.
시드는 이전 프로젝트에서 80개 회귀 테스트와 도그푸딩을 통과한 래퍼 스크립트이며,
아래 "이전 래퍼와 다른 점" 만 바꾸면 그대로 쓴다.

## 한 문단 요약

codex 호출은 전부 래퍼를 거친다. 래퍼는 **반드시 끝난다** — 전체 타임아웃과 진행 스트림
하트비트로 감시하다가 멈추면 프로세스 그룹째 죽이고 정규화된 종료 코드를 낸다. 래퍼는 정책을
갖지 않는다: 한 번 시도하고 왜 끝났는지 `status.json` 에 보고한다. 재시도 여부는 스킬이 정한다.

## 래퍼가 실행하는 명령

```
codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -C "<cwd>" \
  [--model "<model>"] \
  [-c model_reasoning_effort="<effort>"] \
  [-i <image>]... \
  [--output-schema <schema>] \
  [--dangerously-bypass-hook-trust] \
  --json \
  --output-last-message <run-dir>/result.txt \
  "<프롬프트>"
```

stdout → `<run-dir>/events.jsonl`, stderr → `<run-dir>/progress.log`, **stdin → `/dev/null`**.

| 플래그 | 왜 |
|---|---|
| `--skip-git-repo-check` | git 저장소 밖·미신뢰 디렉터리에서 `Not inside a trusted directory` 로 거부되는 것을 막는다. 저장소 안에서도 무해하므로 항상 붙인다 |
| `--dangerously-bypass-approvals-and-sandbox` | 사용자 요구(우회 모드). 비대화 실행은 승인 프롬프트에서 멈추고, 샌드박스는 credential 을 포함한 환경 제약을 만든다. 사용자 `config.toml` 이 이미 같은 값이어도 **명시한다** — 남의 머신에서도 같아야 한다 |
| `-C <cwd>` | codex 의 작업 루트. 래퍼가 `--cwd` 를 받아 넘기고 기본은 `$PWD`. 이전 래퍼는 `cd` 로 해결했고 빠뜨리면 **엉뚱한 저장소를 보면서 정상 종료**했다. `-C` 는 그 사고를 인자 하나로 막는다 |
| `--model` | 있을 때만. 없으면 사용자 config 상속(`00` D5) |
| `-c model_reasoning_effort=` | codex-cli 0.149~0.153 에 `--effort` 플래그가 **없다.** 이 형태만 먹는다. gpt-5.6 계열 유효값 `low\|medium\|high\|xhigh\|max\|ultra`, `minimal`·`none` 은 거부. 플러그인은 `xhigh` 까지만 노출한다 — `ultra` 는 자동 위임이 붙어 비대화 실행이 예측 불가능해진다 |
| `-i <file>` | 프롬프트에 이미지 첨부. **입력 전용**이다 — 생성은 내장 도구가 한다(`03`) |
| `--dangerously-bypass-hook-trust` | 래퍼 `--bypass-hook-trust`(opt-in)를 줄 때만. 신뢰 기록 없이 활성 훅을 실행한다. `orch review` 만 쓰고 `/orca-team:image` 는 쓰지 않는다 |
| `--output-schema` | 최종 응답을 JSON Schema 로 강제. v1 스킬은 쓰지 않는다. 자유 서술이 필요한 작업에 걸면 서술을 잃는다 |
| `--json` + `--output-last-message` | **한 몸이다.** `--json` 을 켜면 stdout 이 JSONL 이벤트 스트림이 되고 최종 메시지는 `-o` 파일에만 남는다. 한쪽만 떼면 `result.txt` 가 JSONL 로 오염되거나 사라진다 |

`--json` 유무에 따른 스트림 실측(0.150.1):

| | stdout | stderr |
|---|---|---|
| `--json` 없음 | 최종 메시지만 | 진행 전사 전부 |
| `--json` 있음 | JSONL 이벤트 스트림 | 거의 마른다(1줄) |

공식 문서는 `-o` 가 stdout 에도 찍는다고 하지만 `--json` 과 같이 쓰면 안 찍는다. 문서가 아니라 실측이 정본이다.

### stdin 은 반드시 `/dev/null`

`codex exec` 는 프롬프트를 인자로 받아도 stdin 이 열려 있으면 `Reading additional input from stdin...` 을 찍고 EOF 를 기다린다. 백그라운드로 띄우면 stdin 이 무엇일지 보장이 없다. 상속시키면 codex 가 시작조차 못 한 채 하트비트에 걸려 죽는다 — 이전 프로젝트의 첫 실물 호출이 정확히 이렇게 죽었다. 회귀 테스트가 단언한다.

### 프롬프트는 인자가 아니라 파일

스킬이 `<run-dir>/prompt.txt` 를 쓰고 `--prompt-file` 로 넘긴다. 셸 인용부호 안에 통째로 넣으면 따옴표·백틱·`$` 에서 깨진다. 래퍼는 `$(cat)` 으로 읽어 후행 개행만 제거한 채 인자로 준다.

### 환경은 그대로 물려준다

`env -i`, `env -u`, 화이트리스트 전달 전부 금지. credential 은 환경에서 온다. 우회 모드에서 이것이 유일한 인증 경로다.

## 래퍼 인터페이스

```
codex-run.sh --profile <run|image> --prompt-file <path> --run-dir <path>
             [--cwd <dir>] [--model <id>] [--effort <low|medium|high|xhigh>]
             [--image <file>]... [--schema <path>] [--timeout <초>] [--heartbeat <초>]
             [--bypass-hook-trust]
```

### 이전 래퍼와 다른 점 (시드에서 고칠 곳)

| 이전 래퍼 | 이 래퍼 | 이유 |
|---|---|---|
| `--role develop\|spec\|design\|plan` | `--profile run\|image` | 역할 넷은 파이프라인 것. 타임아웃 기본값만 남긴다 |
| `--model`·`--effort` 필수 | 선택 | `00` D5. 없으면 플래그를 붙이지 않는다 |
| cwd 를 고치지 않고 `git rev-parse` 로 run-dir 과 대조 | `--cwd` 를 받아 `-C` 로 전달, `status.json` 에 `cwd` 기록. 대조 가드는 그대로 둔다(run-dir 이 저장소 밖이면 자동으로 통과) | 위 `-C` 항목 |
| `-i` 없음 | `--image` 반복 가능 → `-i` | `03` 편집 경로 |
| `status.json` 에 `role` | `profile`, `cwd`, `codex_version` 추가 | 버전은 이벤트 이름 계약의 근거(아래) |

그 외(종료 코드, 하트비트, 프로세스 그룹, 원자적 status 쓰기, 검증, 트랩)는 **바꾸지 않는다.**

### 타임아웃 기본값

| profile | timeout | heartbeat | 근거 |
|---|---|---|---|
| run | 2400초 | 900초 | 이전 프로젝트 develop 실측 |
| image | 900초 | 300초 | 이전 프로젝트 design 실측. 1장 실측 1~3분 |

`--heartbeat` 는 `--timeout` 보다 **작아야 한다.** 크거나 같으면 전체 타임아웃이 항상 먼저 발동해 무응답 감지가 영원히 동작하지 않는다. 래퍼가 `exit 2` 로 거절한다.

**하트비트를 줄이지 마라.** 하트비트는 두 스트림의 바이트 합이 자라는지만 본다. codex 는 도구를 다 쓰고 최종 답을 짓는 구간에서 **318초 동안 한 바이트도 안 냈다**(xhigh, 4파일 대조, 총 412초). 도구가 도는 동안 최대 갭은 29초였다. 이벤트 파싱으로 바꿔도 나아지지 않는다 — 마지막 침묵은 진짜 무출력이다. 하트비트는 죽은 프로세스(영원히 0바이트)만 잡으면 되고, 폭주는 전체 타임아웃이 막는다.

`--wait`(포그라운드) 로 돌릴 때는 스킬이 `--timeout 540` 을 준다. Bash 툴 상한 10분보다 짧아야 래퍼가 먼저 끝난다.

## 실행 디렉터리 (`--run-dir`)

`00` D3 위치. 스킬이 만든다.

| 파일 | 내용 |
|---|---|
| `prompt.txt` | 스킬이 쓴 프롬프트 |
| `result.txt` | `-o` 가 쓴 **최종 메시지.** 결과는 여기서 읽는다 |
| `events.jsonl` | stdout. `--json` 이벤트 스트림. **종단 이벤트 판정 근거.** 최종 메시지도 `item.completed` 로 한 번 더 있다 |
| `progress.log` | stderr. `--json` 을 쓰면 거의 비지만 프로세스가 터진 이유는 여기 남는다 |
| `status.json` | 기계 판독 상태. 스칼라만. 아래 |
| `wrapper.out` | 스킬이 래퍼 자체의 stdout/stderr 를 리다이렉트한 것 |

`status.json` 필드: `profile` `state` `pid` `cwd` `codex_version` `started_at` `last_event_at` `silence_secs` `terminal_event` `raw_exit` `exit`.

`state` ∈ `running | ok | failed | timeout | stalled | missing`. `terminal_event` ∈ `"turn.completed" | "turn.failed" | "error" | null`.

jq 없이 읽는다 — codex 의 JSONL 은 공백 없는 compact 형식이라 줄 첫머리가 판정 근거다:

```bash
grep -oE '^\{"type":"(turn\.completed|turn\.failed|error)"' events.jsonl | tail -1
grep -o '"terminal_event": [^,]*' status.json
```

## 종료 코드와 종단 이벤트 — 둘은 다른 사실이다

| 코드 | `state` | 뜻 |
|---|---|---|
| 0 | `ok` | codex 프로세스가 정상 종료. **작업이 성공했다는 뜻이 아니다** |
| 10 | `timeout` | 전체 타임아웃으로 강제 종료 |
| 11 | `stalled` | 하트비트 무응답으로 강제 종료 |
| 12 | `missing` | `codex` 가 PATH 에 없음 |
| 1 | `failed` | 그 외 비정상 종료. 원래 코드는 `raw_exit` |
| 130 | `failed` | 사람이 중단(SIGINT/SIGTERM/SIGHUP). 지휘자의 도구 타임아웃도 여기로 온다 |
| 2 | **없음** | 인자·파일 검증 실패. **`status.json` 을 만들지 않는다.** stderr 를 읽어라 — 엔진 고장이 아니라 호출부 버그다 |

**판정은 종료 코드가 아니라 `terminal_event` 부터 본다.**

| 종료 코드 | `terminal_event` | 판정 |
|---|---|---|
| 0 | `turn.completed` | 정상. `result.txt` 를 읽는다 |
| 0 | `turn.failed` / `error` | 턴이 실패했다. 프로세스만 깨끗이 끝났다. 엔진 고장으로 다룬다 |
| 10 / 11 / 130 | `turn.completed` | **일은 끝났다.** 껍데기만 죽었다. 재시도하지 말고 산출물로 진행한다 |
| 10 / 11 / 130 | `null` | 진짜로 중간에 끊겼다. 재시도 대상 |
| 1 | 무엇이든 | 비정상 종료. `progress.log` 에 이유가 있다 |

exit 130 인데 구현이 완결돼 있던 실측이 있다(인수 조건 17/17 통과). 완성품을 버리고 30분을 다시 태우는 것이 가장 비싼 실수다.

**exit 0 이어도 `result.txt` 를 읽어야 성공 여부를 안다.** codex 가 "구현안을 제시했으니 진행할까요?" 로 끝내면 exit 0, diff 0줄이다. 래퍼는 잡지 못한다. 스킬이 `git status --porcelain` 과 `result.txt` 를 보고 판단한다.

## 어떻게 띄우나

```bash
# Bash 툴 호출 하나. run 프로파일은 run_in_background: true
"${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh" --profile run \
  --cwd "$PWD" --prompt-file "$RUN/prompt.txt" --run-dir "$RUN" \
  > "$RUN/wrapper.out" 2>&1
```

세 가지가 실측으로 확정됐다.

1. **긴 작업은 하네스 백그라운드(`run_in_background: true`)로.** 포그라운드는 Bash 툴 상한 10분에 걸려 래퍼가 SIGTERM 을 받는다(exit 130). 일은 끝나 있을 수 있다.
2. **`nohup … &` 로 떼지 마라.** Bash 툴이 즉시 반환하고 하네스가 추적할 프로세스가 없어 **codex 가 끝나도 아무도 깨우지 않는다.** 하네스 백그라운드는 상한이 없고 종료 알림이 온다.
3. **`--cwd` 를 항상 준다.** 지휘자 셸의 cwd 는 호출 사이에 프로젝트 루트로 초기화된다. 대상이 다른 폴더면 `-C` 만이 안전하다.

도는 동안 `status.json` 의 `last_event_at` 이 갱신되면 살아 있는 것이다.

## 결과 읽기 (스킬 공통 절차)

1. exit 2 면 `status.json` 이 없다. stderr(`wrapper.out`)를 읽고 인자를 고친다.
2. `terminal_event` 를 본다(위 표).
3. `result.txt` 를 읽는다. `run` 은 자유 서술, `image` 는 마지막 줄이 파일 경로다(`03`).
4. `run` 은 `git -C <cwd> status --porcelain` 으로 변경 유무를 함께 본다.
5. 사용자에게 보고할 때 `result.txt` 의 요약을 그대로 옮기고, run-dir 경로를 한 줄 적는다.

재시도 정책(v1): 기계적 실패(`null` 종단, exit 1/12)는 **1회** 재시도. 두 번째도 실패하면 멈추고 사용자에게 `progress.log` 의 결정적 줄을 보여준다. 턴 실패(`turn.failed`)는 재시도하지 않는다 — 프롬프트나 환경 문제다.

## 보안 경계

`--dangerously-bypass-approvals-and-sandbox` 는 샌드박스를 **끈다.** Codex 는 승인 없이 임의 명령을 실행하고 파일시스템·네트워크 전체에 접근한다. `.env` 도 읽을 수 있다. "Codex 를 격리한다" 는 성립하지 않는다. 남는 방어선은 셋이다.

1. **시크릿을 프롬프트에 넣지 않는다.** 프롬프트는 `prompt.txt`·로그·보고로 흘러간다. 값은 env 상속으로만 전달된다. 존재 확인도 `[ -n "$VAR" ]` 로만, echo 하지 않는다.
2. **run-dir 을 커밋하지 않는다.** codex 출력이 그대로 담긴다. 그래서 저장소 밖에 둔다(`00` D3).
3. **래퍼의 플래그와 환경 상속을 바꾸지 않는다.** 회귀 테스트가 argv 와 환경 마커를 단언한다.

README 보안 주의와 `/orca-team:image` 의 첫 실행 보고에 "샌드박스가 꺼진 상태로 돈다" 를 명시한다.

## 통제 밖 변수

- **JSONL 이벤트 이름은 버전 계약이다.** `thread.started` `turn.started` `item.started` `item.completed` `turn.completed` (0.150.1 실측, **0.153.4 에서 2026-09-06 래퍼 스파이크로 재확인** — 10줄, exit 0, 20초). codex 가 이름을 바꾸면 종단 판정이 조용히 `null` 로 떨어진다. `orch doctor` 는 이것을 보지 않는다. `05` §2 스파이크를 손으로 돌려 `events.jsonl` 에 `turn.completed` 가 실재하는지 확인하고, 래퍼 회귀 테스트(`bash orca-team/tests/run.sh --wrapper`)는 가짜 codex 의 같은 이름으로 판정 로직을 지킨다. 래퍼는 `codex --version` 결과를 `status.json` 에 남긴다.
- **사용자의 codex 훅이 안에서 돈다.** 진행 스트림에 `hook: SessionStart` `PreToolUse` `Stop` 이 찍힌다. `~/.codex/config.toml` 과 `~/.codex/hooks.json` 의 훅이 우리 실행에 개입한다. 끄지 않는다(`--ignore-user-config` 는 의도한 설정도 날린다). 원인 불명의 실패에서 첫 용의자다.
- **사용자의 MCP 서버도 안에서 뜬다.** `~/.codex/config.toml` 의 `[mcp_servers.*]` 가 `codex exec` 마다 기동된다. 죽어 있는 서버(예: Figma 데스크톱 미실행 시 `url = http://127.0.0.1:3845/mcp`)는 `progress.log` 에 `ERROR rmcp::transport::worker … Transport channel closed` 를 남기지만 실행은 정상 완료한다(2026-09-06 실측). 훅과 같은 취급 — 기록만 한다.
- **`Reading additional input from stdin...` 은 stdin 이 `/dev/null` 이어도 찍힌다.** EOF 를 즉시 받으므로 멈추지 않는다. 이 줄 하나만 있고 이벤트가 없으면 stdin 이 열려 있는 것이다(`06` #1).
- **매 호출이 새 세션이다.** 이전 호출의 추론은 남지 않는다. 이어가야 할 맥락은 프롬프트에 다시 넣거나 디스크(파일)에 있어야 한다.

## run 프롬프트 뼈대 (orca-team 에 없음)

`run` 스킬 설계의 기록이다. orca-team 의 `orch review` 는 `orca-team/templates/review-prompt.md` 를 쓴다(리뷰 전용 `<execution_policy>` 블록이 같은 비대화 규칙을 건다).

```
<task>
{사용자의 요청 원문}
</task>

<context>
작업 루트: {cwd}
{스킬이 덧붙인 저장소 사실 — 있을 때만}
</context>

<execution_policy>
이것은 비대화 자동 실행이다. 승인을 구하지 마라. 계획을 제시하고 확인을 기다리는 것은 실패다.
지금 파일을 실제로 수정해라. 물어볼 사람이 없다.
요청이 모호하거나 임의로 지어내야 할 결정이 빠져 있으면 추측하지 말고 범위를 넓히지도 마라.
그 공백을 최종 요약에 명확히 적고 멈춰라 — 그때도 확인을 구하지 말고 적은 뒤 종료한다.
저장소의 기존 컨벤션을 따른다. 요청하지 않은 추상화·파일·의존성을 만들지 마라.
</execution_policy>

<verification>
바꾼 것이 실제로 도는지 이 저장소의 표준 명령(테스트·빌드·린트)으로 확인해라.
</verification>

<output_contract>
끝나면 순서대로: 바꾼 파일 목록, 무엇을 왜 바꿨는지, 확인한 명령과 결과, 확신이 서지 않는 가정.
</output_contract>
```

`<execution_policy>` 의 비대화 문장은 **빼지 마라.** 없으면 codex 가 구현안만 내고 "진행해도 될까요?" 로 끝난다(exit 0, diff 0줄). 프롬프트 다듬기의 일반론은 공식 플러그인의 `gpt-5-4-prompting` 스킬(`~/.claude/plugins/marketplaces/openai-codex/plugins/codex/skills/gpt-5-4-prompting/`)에 있다 — XML 블록 이름을 거기서 가져왔다.

## v1.1 메모 — 세션 이어가기

`codex exec resume [SESSION_ID] [PROMPT]`, `--last` 가 있다(0.153.4). `events.jsonl` 첫 줄 `thread.started` 에 세션 id 가 있다. 설계 당시 `run` 스킬에 `--resume <추가 지시>`(래퍼 `--resume <id>` → `exec resume <id>`, 프롬프트는 델타만)를 구상했다. orca-team 에는 해당 없다 — `run` 스킬이 없고 `orch review` 는 매 시도를 새 세션으로 돌린다.
