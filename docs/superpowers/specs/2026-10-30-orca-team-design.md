# orca-team 플러그인 설계

작성일: 2026-10-30
상태: 사용자 승인 대기(설계 검토)

## 1. 목적

새 개발 프로젝트를 시작할 때 매번 보내던 긴 세팅 프롬프트를 명령어 하나로 바꾼다.

```
/orca-team:init <프로젝트 설명>
```

이 명령 하나로 다음이 끝나야 한다.

- 프로젝트에 필요한 스킬·플러그인·프로그램 세팅
- Orca 오케스트레이션 3계층 구성: 메인(사용자 대화) / 코디네이터 / 직무 워커
- 직무별 Claude 모델·effort와 Codex 리뷰 모델·reasoning 고정
- Codex 리뷰 경로 연결
- 디자인 산출물 경로 고정: 앱·웹 화면 디자인은 Claude Design, 래스터 이미지는 Codex
- 사용자 입력이 Orca 알림에 방해받지 않는 구조

핵심 요구는 **일관성**이다. 같은 명령이면 어느 프로젝트에서나 같은 구조, 같은 배치, 같은 절차로 돌아야 한다.

## 2. 현재 문제와 원인

### 2.1 동작이 매번 다르다

Orca 오케스트레이션 가이드(`orca skills get orchestration`의 placement 참조)는 배치를 모델 판단에 맡긴다. 기본은 현재 체크아웃에 새 터미널을 열고, 충돌 우려가 있으면 새 워크트리를 만든다. 같은 프롬프트라도 세션마다 판단이 갈려서, 어떤 프로젝트는 워크트리 방식이 되고 어떤 프로젝트는 패널 여러 개 방식이 된다. Chat UI 설정에 따라 채팅/터미널 형태도 달라진다.

**해결:** 배치·모델·리뷰 절차를 결정적 CLI(`orch`)에 하드코딩한다. 스킬과 에이전트는 `orch`만 호출하고, Orca 플래그를 직접 고르지 않는다.

### 2.2 사용자 입력이 방해받는다

Run 메일함에 대기자(`orca orchestration check --wait`)가 없을 때 메시지가 오면, Orca는 Run을 소유한 터미널 입력란에 "You have N orchestration message" 알림을 Enter와 함께 넣는다. 이 알림은 사용자가 치던 입력과 섞인다. AskUserQuestion 창이 떠 있으면 첫 선택지가 눌린다. sin 프로젝트에서 2026-10-18에 실제로 발생했다(메모리 `orca-nudge-autoselect`).

**해결:** 사용자와 대화하는 메인 세션은 어떤 Run에도 묶이지 않는다. Run은 별도 Orca 터미널의 코디네이터가 소유한다. 그래서 Orca 알림이 메인 입력란으로 들어올 경로가 없다.

### 2.3 Codex 훅 신뢰 프롬프트

Codex TUI는 훅이 바뀌면 신뢰 여부를 묻고 멈춘다. `codex exec`에는 `--dangerously-bypass-hook-trust`가 있다.

**해결:** Codex는 TUI 워커로 띄우지 않는다. orca-team에 내장한 headless 래퍼(`scripts/codex-run.sh`, `codex exec --dangerously-bypass-approvals-and-sandbox`)로만 호출한다. 래퍼의 opt-in 플래그 `--bypass-hook-trust`로 훅 신뢰도 자동 적용한다(§9, §13 "Codex 래퍼 내장").

## 3. 결정 사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 형태 | Claude Code 플러그인 | 스킬만으로는 훅·스크립트·템플릿을 묶어 배포할 수 없다 |
| 위치 | `setup-with-orca` 저장소 = 독립 마켓플레이스 | 사용자 선택 |
| 계층 | 3계층(메인 / 코디네이터 / 직무 워커) | 사용자 선택. 메인이 Run에 묶이지 않아 입력 방해가 구조적으로 불가능하다 |
| 작업 공간 | 단일 체크아웃 + Orca 패널 | 사용자 선택. 설치 1회, 병합 단계 없음 |
| 설치 단계 | 목록 제안 → 승인 1회 → 일괄 설치 | 사용자 선택 |
| Codex 실행 | 내장 Codex 래퍼(headless, yolo) | 사용자 지시("코덱스 스킬 활용"). TUI 프롬프트가 없다. 2026-10-31 래퍼를 플러그인에 넣어 따로 설치할 것이 없다 |
| 앱·웹 화면 디자인 | Claude Design(Artifact 도구의 Design 타입)으로 만든다. Codex 이미지 생성으로 화면을 그리지 않는다 | 사용자 지시 |
| 래스터 이미지 | 일러스트·아이콘·배경·사진 같은 비트맵 에셋만 `/orca-team:image`(Codex)로 생성하고, 요청한 워커(Claude)가 검토 | 사용자 지시. Claude는 래스터 이미지를 생성하지 못한다 |
| Claude effort | 기본값보다 한 단계 높게(xhigh) | 사용자 지시 |

## 4. 아키텍처

```
사용자 ⇄ 메인(Claude, 사용자 터미널, Run 미바인딩)
           │ orch tell ──────────────► 코디네이터 Run 메일함
           │ orch watch ◄── outbox.jsonl ◄── orch notify
           ▼
        코디네이터(Claude, 별도 Orca 터미널, Run 소유)
           │ orch start / fix  ─► orca orchestration worker-start --worktree current
           │ orch review       ─► scripts/codex-run.sh (내장, headless Codex)
           ▼
        직무 워커(Claude, Orca 패널)
           ├─ 앱·웹 화면 디자인 ─► Claude Design(Artifact Design 타입)
           └─ 래스터 에셋        ─► /orca-team:image (Codex)
```

### 4.1 역할과 판별

역할은 CLAUDE.md의 판별 섹션과 UserPromptSubmit 훅이 정한다. 훅은 세션 ID를 `.orchestration/state/sessions/<session_id>`에 역할과 함께 기록한다.

| 역할 | 판별 조건 | 하는 일 | 금지(훅이 거부) |
|---|---|---|---|
| 메인 | 마커도 프리앰블도 없는 세션 | 사용자와 대화, 요청을 코디네이터에 전달, 코디네이터 메시지 중계, 사용자 결정 질문 | 산출물 파일 Edit/Write. `orca orchestration` 직접 호출 |
| 코디네이터 | 첫 프롬프트가 `[orca-team:coordinator]`로 시작 | 태스크 분해, spec 작성, 디스패치, 리뷰 라우팅, 진행 보고 | 산출물 파일 Edit/Write. 사용자에게 직접 질문 |
| 직무 워커 | Orca 디스패치 프리앰블(`Your task ID is:` 등) | spec 하나 수행, 자기 소유 경로만 커밋, 리포트 작성, `worker_done` | 소유 경로 밖 커밋(규칙. `orch`가 리뷰 때 검사) |

메인의 `orca orchestration` 금지는 Bash 명령 문자열에 `orca orchestration`이 직접 들어간 경우만 막는다. `orch tell`처럼 `orch`를 거치는 호출은 허용한다. 코디네이터의 AskUserQuestion 호출은 훅이 거부한다.

"산출물 파일"은 `.claude/`, `.orchestration/`, `CLAUDE.md`, `AGENTS.md`, `.gitignore`, `docs/superpowers/`를 뺀 전부다. 메인은 init과 세팅 작업 중에만 산출물 쓰기 제한이 풀린다(`.orchestration/state/setup-mode` 플래그. `orch scaffold`가 만들고 init 마지막에 지운다).

### 4.2 기본 직무

`planner`(기획), `designer`(디자인), `developer`(개발), `tester`(테스트). 직무 정의는 `.orchestration/roles/<role>.md`에 둔다. 프로젝트 설명상 직무가 더 필요하면 init의 승인 표에 "추가 직무" 행으로 같이 제안한다. 추가 직무도 같은 템플릿 형식을 따른다.

### 4.3 직무별 모델 기본값

| 직무 | Claude 모델 / effort | Codex 리뷰 모델 / reasoning |
|---|---|---|
| 코디네이터 | opus / xhigh | — |
| planner | opus / xhigh | gpt-6.1-sol / high |
| designer | sonnet / xhigh | gpt-6.1-sol / medium |
| developer | opus / xhigh | gpt-6-astra / xhigh |
| tester | sonnet / xhigh | gpt-6.1-sol / high |

값은 프로젝트의 `.orchestration/config.toml`에 들어간다. 프로젝트마다 수정할 수 있다. 우선순위는 `orch` 플래그 > 태스크 front matter > config.toml 직무 값 > config.toml 기본값이다.

## 5. 플러그인 구성

```
setup-with-orca/
  .claude-plugin/marketplace.json
  orca-team/
    .claude-plugin/plugin.json
    skills/
      init/SKILL.md          # /orca-team:init <프로젝트 설명>
      up/SKILL.md            # /orca-team:up  세션 재개(코디네이터 확인, watcher 재무장)
      image/SKILL.md         # /orca-team:image  Codex 내장 image_gen 래스터 에셋(ccc에서 옮김)
    bin/orch                 # 결정적 CLI. Python 3.11+ 표준 라이브러리만 사용
    scripts/codex-run.sh     # 내장 Codex 래퍼(codex exec 감시 실행)
    hooks/
      hooks.json
      role-mark.sh           # UserPromptSubmit: 세션 역할 기록
      guard.sh               # PreToolUse: 역할별 금지 동작 거부
      session-start.sh       # SessionStart: 역할 안내, 코디네이터 생존·미확인 outbox 알림
      stop-watch.sh          # Stop: 메인의 watcher가 없으면 재무장 강제
    templates/
      CLAUDE.section.md  AGENTS.md  coordinator.md  config.toml
      roles/{planner,designer,developer,tester}.md
      task.md  report.md  review-prompt.md  image-prompt.md
    tests/
      run.sh                 # 가짜 orca·codex로 orch와 훅을 검사. --wrapper 면 래퍼 회귀 테스트도
      test-codex-run.sh      # 내장 래퍼 회귀 테스트(가짜 codex, 2분 남짓)
  docs/codex/                # 래퍼·이미지 계약 문서(00·02·03·05·06)
  docs/superpowers/specs/
```

모든 훅은 프로젝트에 `.orchestration/config.toml`이 없으면 아무것도 하지 않고 끝난다. 그래서 플러그인을 전역 설치해도 다른 프로젝트에는 영향이 없다.

### 5.1 프로젝트에 생기는 파일

```
CLAUDE.md                      # 역할 판별 섹션(기존 내용이 있으면 끝에 추가)
AGENTS.md                      # Codex 리뷰어 규칙
.orchestration/
  project.md                   # 사용자가 준 프로젝트 설명 원문
  config.toml                  # 모델·effort·소유 경로·리뷰 라운드
  coordinator.md               # 코디네이터 운영 절차
  roles/*.md                   # 직무 정의
  tasks/T-xxx.md               # 태스크 spec(커밋)
  bin/orch                     # shim: 설치된 플러그인 본체를 찾아 exec
  state/                       # .gitignore 대상
    run.json                   # Run ID, 코디네이터 터미널 handle
    outbox.jsonl  outbox.cursor  watch.pid
    tasks/T-xxx.json           # dispatch ID, 라운드, 워커 handle
    sessions/<session_id>      # 역할 표시
docs/tasks/T-xxx/report.md     # 워커 리포트
docs/reviews/T-xxx/rN.md       # Codex 리뷰
```

shim은 `~/.claude/plugins/installed_plugins.json`에서 `orca-team`의 `installPath`를 읽어 `bin/orch`를 실행한다. 그래서 플러그인을 갱신하면 모든 프로젝트에 바로 반영된다. 찾지 못하면 "플러그인 미설치"를 출력하고 종료 코드 12로 끝난다.

### 5.2 config.toml 형식

```toml
[coordinator]
model = "opus"
effort = "xhigh"

[review]
max_rounds = 3

[defaults]
claude_model = "sonnet"
claude_effort = "xhigh"
codex_model = "gpt-6.1-sol"
codex_effort = "high"

[roles.developer]
title = "개발"
claude_model = "opus"
claude_effort = "xhigh"
codex_model = "gpt-6-astra"
codex_effort = "xhigh"
owns = ["src/**", "package.json"]
```

`owns`는 init 때 프로젝트 성격에 맞춰 승인 표에서 같이 정한다. 기본값은 planner `docs/planning/**`, designer `docs/design/**`·`assets/**`, developer `src/**`, tester `tests/**`·`docs/qa/**`다.

## 6. `orch` 명령

| 명령 | 호출자 | 동작 |
|---|---|---|
| `doctor` | init | orca 실행 여부(`orca status --json`), codex 로그인, 내장 Codex 래퍼(파일·실행 권한·`--bypass-hook-trust` 지원), git, python ≥3.11을 점검한다. codex `image_generation` feature는 경고만 한다. 결과를 표로 출력하고, 실패 항목이 있으면 종료 코드 1 |
| `scaffold --description-file <f>` | init | 템플릿을 복사하고 `project.md`를 저장한다. `.gitignore`를 갱신하고 setup-mode 플래그를 만든다. 이미 있는 파일은 덮어쓰지 않고 목록으로 보고한다 |
| `up` | 메인 | 코디네이터가 살아 있으면 그대로 둔다. 없으면 `orca agent launch --agent claude --surface terminal --model <M> --effort <E> --prompt "[orca-team:coordinator] ..."`로 띄우고 handle을 `run.json`에 기록한다 |
| `status` | 메인·코디네이터 | 코디네이터 생존, 진행 중 태스크와 라운드, 미확인 outbox 수를 출력한다 |
| `tell "<msg>" [--reply <id>]` | 메인 | `orca orchestration send --to run:<id>`로 코디네이터에게 보낸다(§11 검증 항목 1) |
| `watch` | 메인(백그라운드) | `outbox.jsonl`에 새 줄이 생기면 출력하고 종료한다. pid를 `watch.pid`에 쓴다. Orca를 호출하지 않는다 |
| `notify --type progress\|question\|done "<msg>"` | 코디네이터 | `outbox.jsonl`에 한 줄 추가한다. question은 ID를 붙인다 |
| `wait` | 코디네이터 | `orca orchestration check --wait --types worker_done,escalation,question,status --timeout-ms 3600000 --json`을 고정 인자로 감싼다(Bash `run_in_background`) |
| `task new <role> "<title>"` | 코디네이터 | 다음 ID로 `tasks/T-xxx.md`를 템플릿에서 만든다 |
| `start T-xxx` | 코디네이터 | spec 검증 → 소유 경로 잠금 확인 → `worker-start --worktree current --agent claude --model <M> --effort <E> --spec <조립된 spec>` |
| `review T-xxx` | 코디네이터(백그라운드) | 태스크 커밋 범위와 소유 경로 위반을 검사 → 리뷰 프롬프트 조립 → 내장 `scripts/codex-run.sh --profile run --model <CM> --effort <CE> --bypass-hook-trust` → `docs/reviews/T-xxx/rN.md`의 판정 줄을 읽는다 |
| `fix T-xxx` | 코디네이터 | `changes` 또는 `working`(워커 실패 보고·워커 종료) 상태에서 라운드를 올린다. 이전 디스패치를 `worker-release`(최선 노력)한 뒤 `--worktree current --agent claude --model <M> --effort <E>`로 새 워커를 띄운다(`--model`·`--effort` 적용). `max_rounds`를 넘으면 거부하고 escalation을 안내한다 |
| `accept T-xxx` | 코디네이터 | APPROVE 확인 → `worker-release` → 잠금 해제 → 상태를 완료로 바꾼다 |

모든 Orca 호출은 `--json`이다. 종료 코드는 0 성공, 1 실패, 2 인자 오류, 12 의존성 없음으로 통일한다.

### 6.1 소유 경로 잠금

단일 체크아웃이라 같은 파일을 두 워커가 동시에 고치면 안 된다. `start`는 진행 중인 태스크들의 `owns`와 새 태스크의 `owns`가 glob 수준에서 겹치는지 검사하고, 겹치면 거부한다. 워커는 `git add <소유 경로>`로 자기 경로만 커밋하고, 커밋 제목에 `[T-xxx]`를 붙인다. `review`는 `[T-xxx]` 커밋의 변경 파일이 `owns` 밖에 있으면 리뷰 전에 실패로 보고한다.

## 7. 흐름

### 7.1 `/orca-team:init <설명>`

1. `orch doctor`. git 저장소가 아니면 `git init`. 다른 실패 항목은 고칠 방법을 보고하고 멈춘다.
2. 설명을 임시 파일로 쓰고 `orch scaffold --description-file <f>`.
3. **모델 판단 구간.** 설명을 읽고 표 하나로 제안한다. 행은 설치할 스킬, 플러그인, 프로그램, 추가 직무, 직무별 `owns`다. 열은 이름, 이유, 설치 범위(프로젝트/전역), 명령이다. AskUserQuestion으로 승인을 한 번 받고 일괄 설치한다. 설치 결과는 성공/실패로 기록한다.
4. `orch up`으로 코디네이터를 띄운다. 메인은 `orch watch`를 `run_in_background`로 띄운다.
5. setup-mode 플래그를 지우고 세팅 파일을 커밋한다. "준비 완료"와 코디네이터 터미널 위치를 보고한다. 첫 업무는 사용자의 다음 메시지부터 시작한다.

### 7.2 태스크 수명주기

1. 코디네이터가 사용자 요청을 태스크로 나눈다: `orch task new` → spec 작성(Target, Change, Constraints, Ownership, Acceptance) → 커밋.
2. `orch start T-xxx`. 소유 경로가 겹치지 않는 태스크는 같은 웨이브에서 함께 시작한다.
3. 워커는 작업하고, 자기 경로만 커밋하고, `docs/tasks/T-xxx/report.md`를 쓴 뒤 `worker_done --outcome succeeded|failed --report-path`를 보낸다.
4. 코디네이터는 `orch wait`로 받고 `orch review T-xxx`를 백그라운드로 실행한다.
5. 판정에 따라 진행한다.
   - APPROVE → `orch accept`. `accept`는 태스크 제목과 판정을 한 줄로 `orch notify --type progress`에 자동으로 남긴다.
   - REQUEST_CHANGES → `orch fix` → 3으로. `max_rounds`(3)를 넘으면 `orch notify --type question`으로 메인에 판단을 요청한다.
6. 요청 전체가 끝나면 `orch notify --type done`으로 결과, 변경 파일, 리뷰 판정을 요약해 보낸다.

### 7.3 사용자 소통

- 사용자 → 메인 → `orch tell` → 코디네이터.
- 코디네이터 → `orch notify` → `outbox.jsonl` → 메인의 `orch watch`가 종료 → 하네스 완료 알림으로 메인이 깨어나 사용자에게 전달 → 메인이 `orch watch`를 다시 띄운다. 하네스 알림은 사용자 입력란을 건드리지 않는다.
- 워커가 `ask`를 보내면 코디네이터가 먼저 답한다. 사용자 결정이 필요할 때만 question으로 메인에 올린다. 메인은 AskUserQuestion으로 묻고, 답을 `orch tell --reply <id>`로 돌려준다.

### 7.4 디자인 산출물

산출물 종류에 따라 도구가 고정된다. designer 직무 정의(`roles/designer.md`)와 코디네이터 절차에 이 규칙을 그대로 적는다.

| 산출물 | 도구 | 검토 |
|---|---|---|
| 앱·웹 화면 디자인(화면, 레이아웃, UI 목업, 와이어프레임, 프로토타입) | Claude Design: Artifact 도구 `quickstart`(intent `design`)로 Design 타입을 찾고, 그 `type_url`로 Design 아티팩트를 만든다 | Codex 리뷰(§7.2) |
| 래스터 에셋(일러스트, 아이콘 비트맵, 배경, 사진풍 이미지) | `/orca-team:image`(Codex 내장 image_gen) | 요청한 워커(Claude)가 직접 보고 검토 → 이후 Codex 리뷰 |
| 코드로 그리는 그래픽(SVG, CSS, 다이어그램) | 워커가 코드로 직접 작성 | Codex 리뷰 |

- 화면 디자인에 들어갈 래스터 에셋은 먼저 `/orca-team:image`로 만들고 채택한 파일을 `assets/`에 커밋한다. 그다음 Design 아티팩트에서 참조한다.
- Design 아티팩트를 채우는 소스 파일은 `docs/design/<화면>/`에 커밋한다. Codex는 claude.ai 아티팩트에 직접 접근하지 못하므로 커밋된 소스와 리포트를 리뷰한다.
- 리포트에는 Design 아티팩트 URL, 화면 목록, 사용한 디자인 시스템을 적는다. `/orca-team:image`를 썼다면 프롬프트, 생성 수, 채택 이유도 적는다.
- developer는 Design 아티팩트 URL과 `docs/design/` 소스를 입력으로 받아 구현한다.
- `type_url`은 계정별로 다를 수 있다. 그래서 템플릿에 URL을 박지 않고 매번 `quickstart`로 찾는다(2026-10-30 확인 값: `https://claude.ai/artifact/QKN21svewxgyPb6SYRqWnd`).

## 8. 오류 처리

| 상황 | 감지 | 대응 |
|---|---|---|
| 메인 watcher 없음 | Stop 훅이 `watch.pid` 생존 확인 | 턴 종료를 막고 "`orch watch`를 백그라운드로 다시 띄워라"를 돌려준다 |
| 코디네이터 터미널 종료 | 메인 SessionStart 훅, `orch status` | `orch up`으로 재기동한다. 새 코디네이터는 `run.json`의 Run에 `run-use`로 다시 붙고, `state/tasks/`에서 진행 상황을 복원한다 |
| `worker-start` 실패(exit≠0) | `orch start` | 재실행하지 않는다. 영수증의 `failedStage`와 `residualResources`를 상태에 기록하고, 코디네이터가 Orca recovery 참조를 따른다. 해결이 안 되면 메인에 question |
| Codex 실패 | 내장 Codex 래퍼 `status.json` | `docs/codex/02`의 종료 코드·종단 이벤트 판정대로 1회 재시도한다. 두 번째 실패는 escalation |
| 리뷰 판정 줄 없음 | `orch review` | 판정 불명으로 처리하고 1회 재리뷰한다. 그래도 없으면 escalation |
| 소유 경로 위반 커밋 | `orch review` 사전 검사 | 리뷰 없이 REQUEST_CHANGES로 처리하고 `fix`로 되돌린다 |
| Codex가 리뷰 중 파일 수정 | `orch review` 전후 `git status --porcelain` 비교 | 리뷰 파일 외 변경을 보고하고 판정을 무효로 한다. 변경은 되돌리지 않고 코디네이터가 메인에 question으로 올린다 |
| Orca 런타임 멈춤(`starting` 고착) | `orca status` | 상태를 보고만 한다. Orca 재시작은 사용자 승인 후에만 한다(메모리 `orca-runtime-stuck-restart` 절차) |
| 플러그인 미설치·shim 실패 | shim 종료 코드 12 | 설치 명령을 안내한다 |

## 9. 외부 변경: Codex 래퍼 (대체됨)

> 2026-10-31 래퍼를 orca-team에 내장해 이 절은 대체됐다(§13 "Codex 래퍼 내장"). 이제 래퍼는 `orca-team/scripts/codex-run.sh`, 회귀 테스트는 `orca-team/tests/test-codex-run.sh`다. 아래는 당시 기록이다.

당시 플러그인 밖에 있던 Codex 래퍼 `scripts/codex-run.sh`에 opt-in 플래그를 추가한다.

- `--bypass-hook-trust`: 주어졌을 때만 `codex exec`에 `--dangerously-bypass-hook-trust`를 붙인다. 기본 동작은 바꾸지 않는다.
- `test-codex-run.sh`에 플래그가 있을 때와 없을 때 argv를 단언하는 케이스를 추가한다.
- `orch doctor`는 래퍼의 `usage`에 이 플래그가 있는지 확인한다.

## 10. 테스트

- **`orch` 단위 테스트**(`tests/run.sh`, bash): PATH 앞에 가짜 `orca`·`codex`를 두고 호출 argv를 파일로 남긴다. 다음을 단언한다.
  - `start`: `--worktree current --agent claude`와 config의 모델·effort, 우선순위 규칙
  - 잠금: `owns`가 겹치면 거부하고, 겹치지 않으면 허용
  - `review`: Codex 래퍼에 `--bypass-hook-trust`와 Codex 모델·effort 전달, 판정 줄 파싱, 소유 경로 위반 사전 실패
  - `fix`: 라운드 증가, 이전 디스패치 해제 뒤 새 워커 launch, `max_rounds` 초과 거부
  - `notify`/`watch`: 새 줄에서만 종료, cursor 갱신
  - `up`: 코디네이터 생존 시 재기동 안 함, 사망 시 `agent launch` argv
  - shim: 플러그인 경로 해석과 미설치 시 12
- **훅 테스트**: 역할별 JSON 입력으로 `guard.sh`의 허용/거부, `role-mark.sh`의 기록, `stop-watch.sh`의 차단 여부, `.orchestration/` 없는 프로젝트에서의 no-op을 단언한다.
- **수동 E2E 1회**: 임시 프로젝트에서 `/orca-team:init` → 작은 태스크 하나 → Codex 리뷰 → APPROVE까지 진행한다. 이 동안 메인 터미널 입력란에 Orca 알림이 0건 들어왔는지 확인한다.

## 11. 구현 중 검증할 가정

1. Run에 묶이지 않은 메인이 `orca orchestration send --to run:<id>`로 보낼 수 있는지, 그리고 이 호출이 메인을 Run에 바인딩하지 않는지 확인한다. 안 되면 `orca terminal send --terminal <코디네이터 handle> --text ... --enter`로 대체한다(코디네이터는 `--surface terminal`이라 가능하다).
2. 코디네이터의 `orca agent launch` 영수증에서 터미널 handle을 얻는 필드를 확인한다.
3. `codex exec`가 신뢰되지 않은 훅을 만나면 어떻게 동작하는지 확인한다(멈춤, 경고 후 생략, 실패). 결과와 상관없이 플래그는 넣지만, 동작은 문서에 기록한다.
4. Claude 모델 별칭(`opus`, `sonnet`)이 `worker-start --model`과 `agent launch --model`에서 그대로 받아들여지는지 영수증의 `launch.effective`로 확인한다.
5. 플러그인 `bin/`이 PATH에 자동으로 들어가는지 확인한다. 들어가면 shim 없이 `orch`로 호출할 수 있으므로 shim은 대체 경로로만 남긴다.
6. Orca가 띄운 Claude 워커 세션에서 Artifact 도구(Design 타입 생성·발행)를 쓸 수 있는지 확인한다. 쓸 수 없으면 designer는 코디네이터에 `ask`로 보고한다. 대안은 사용자 승인 후 정한다.
7. Design 타입이 로컬 데이터 파일로 채워지는지, 타입 자체 저장소로 채워지는지 확인한다. 저장소 방식이면 Artifact `read`(`path`)로 소스를 받아 `docs/design/`에 커밋한다.

### 검증 결과 (2026-10-30, Orca 1.4.240 / codex-cli 0.163.0)

| 가정 | 결과 | 근거 |
|---|---|---|
| 1. 미바인딩 메인의 send --to run:<id> | 통과 | 메인 터미널이 보낸 `msg_b503938a4006`(to `run:run_99eb4bb17b73`)을 코디네이터가 받아 T-001·T-002를 만들었고, 메인 handle 앞 orchestration inbox는 끝까지 0건이었다 |
| 2. agent launch 영수증 handle | 통과 | handle은 `result.outcome.handle`에 있고, `run.json`의 `coordinator_handle`을 `terminal show`하면 `"agentIdentity": "claude"`, `"connected": true`였다 |
| 3. codex exec 훅 신뢰 | 통과 | `--dangerously-bypass-hook-trust`를 붙인 리뷰 4회가 모두 `"terminal_event": "turn.completed"`(exit 0)로 끝났고 progress.log에 hook·trust 줄이 없었다 |
| 4. 모델 별칭 | 통과 | worker-start 영수증의 `launch.effective.model`이 T-001 `sonnet`, T-002 `opus`로 config 값과 같았다 |
| 5. 플러그인 bin PATH | 해당 없음 | shim 고정(§13-4) |
| 6. 워커 Artifact 도구 | 통과 | designer 워커가 Artifact `quickstart`(design)로 Design 아티팩트 `https://claude.ai/artifact/NdkruYGFQM2viJnshMMQSY`를 만들어 발행했다 |
| 7. Design 소스 방식 | 통과 | designer가 `docs/design/todo/project/*.dc.html`·`canvas.json` 로컬 파일을 Artifact로 발행했고, 이 파일들이 `docs/design/` 아래 9개로 커밋됐다 |
| 입력 방해 | 통과 | 요청 완료 후 `orca terminal read --terminal <MAIN_HANDLE> --json \| grep -c "orchestration message"` 결과가 `0`이었다 |

## 12. 범위 밖

- 직무별 git 워크트리, 브랜치 병합 흐름(단일 체크아웃으로 결정했다)
- 텔레그램 채널 연동(필요하면 별도 작업)
- 원격 Orca 서버(`--on`) 배치
- 기존 프로젝트의 마이그레이션

## 13. 구현 조정 (2026-10-30 구현 계획)

1. 훅은 셸 스크립트 4개 대신 `hooks/orca_team_hook.py <role-mark|guard|session-start|stop>` 하나로 만든다. `jq` 의존이 없고 `orch`와 경로 해석 코드를 공유한다.
2. `orch wait`는 60분 대기(`--timeout-ms 3600000`)이고 코디네이터가 Bash `run_in_background`로 띄운다. Bash 포그라운드 상한(10분) 때문이다. 중복 실행은 `wait.pid`로 막는다.
3. `orch bind`(코디네이터가 Run 생성·재연결)와 `orch which`(Orca task/dispatch ID → T-xxx)를 추가했다. `bind`·`wait`·`task new`·`start`·`fix`·`review`·`accept`는 `ORCA_TERMINAL_HANDLE`이 `run.json`의 코디네이터 handle과 같을 때만 실행된다.
4. 모든 역할이 프로젝트 shim `.orchestration/bin/orch`를 쓴다. §11-5(플러그인 `bin/` PATH)는 확인할 필요가 없어졌다.
5. 리포트 형식은 별도 템플릿 대신 워커 spec(`templates/worker-spec.md`)에 들어 있다.
6. 리뷰 중 Codex 파일 변경 검사는 다른 진행 태스크의 소유 경로·리포트, `.orchestration/**`, `docs/reviews/**`를 뺀 작업 트리 내용 해시로 한다.
7. 리뷰 시작 시 워커 터미널을 `worker-retain`으로 보존한다. `accept`는 `worker-release`로 정리한다(`fix` 의 터미널 재사용은 아래 "최종 리뷰·E2E 반영 조정"에서 폐기).

### 리뷰 반영 조정

- orca: `terminal_alive`/`worker_terminal`은 출력 파싱 실패 시 예외 대신 False/(None, None). 코드 12(orca 없음)는 그대로 전파.
- outbox: `notify`는 `outbox.jsonl.lock` flock 안에서 기존 최대 id+1을 매긴다. 깨진 줄·끝 줄바꿈 없는 줄은 읽을 때 건너뛴다. 커서는 출력·flush 뒤에 원자적으로 저장한다(최소 1회 전달). watch/wait pid 파일은 O_EXCL로 만들고 SIGTERM에도 정리한다.
- tasks: `launch()`는 Orca 호출 전에 상태를 `launch_failed`로 먼저 기록하고 성공 시 `working`으로 덮는다(중간 크래시 시 중복 워커 방지). `fix`도 소유 경로 잠금을 다시 검사한다. `worker-spec.md`는 `{fix_block}## 작업 규칙`.
- up: `LAUNCH_GRACE_SECONDS = 180`. 기동 직후 살아 있음이 확인되기 전 재실행은 `starting`을 돌려주고 다시 띄우지 않는다. Orca가 명시적으로 실패를 알린 경우만 `launched_at`을 지워 즉시 재시도를 허용한다. 핸들은 `result.terminal.handle` 등 명시 경로를 먼저 본다. run.json 쓰기는 모두 `util.update_run`(flock + 재읽기)으로 한다.
- review: OrchError 외 예외도 `working`으로 되돌린다. 경로 목록은 `-z`로 읽는다(한글 경로). `[T] spec:`/`[T] review rN:` 커밋은 제목과 경로(각각 `.orchestration/`, `docs/reviews/T/`)가 모두 맞을 때만 건너뛴다. 시도 중 HEAD가 바뀌면 fast-forward여야 하고, 새 커밋(커밋별 경로 ∪ 순 diff)이 리뷰 제외 경로 밖을 건드리면 무효다.
- hooks: `ORCA_TERMINAL_HANDLE` == run.json `coordinator_handle`이면 표시와 상관없이 코디네이터다(`/clear` 뒤에도 유지). SessionStart는 코디네이터면 조용하고, 그 외엔 "메인 세션에만 해당" 문구를 붙인다. session_id는 `^[A-Za-z0-9._-]+$`만 허용하고 `..`를 거부한다. 잘못된 입력은 조용히 통과(exit 0).
- 알려진 한계: 새 세션 ID의 첫 지시에 워커 표시가 없으면 워커가 메인으로 기록돼 제품 파일 편집이 막힌다(워커는 1회성 디스패치라 드묾). Bash 기반 편집(`sed -i`, 리다이렉트)은 가드하지 않는다.
- Codex 래퍼: 당시 외부 래퍼에 `--bypass-hook-trust`를 더했다(§9). 지금은 §13 "Codex 래퍼 내장"을 따른다.

### 최종 리뷰·E2E 반영 조정

최종 리뷰 1차
- 워커·코디네이터·init 커밋은 공유 인덱스에서 남의 스테이징을 섞지 않도록 모두 경로 지정 커밋(`git add -- <경로> && git commit -m "..." -- <경로>`)으로 한다.
- 리뷰 변조 검사는 추적 파일 내용 변경과 HEAD 이동만 판정 무효로 본다. Codex 가 만든 새 추적 안 된 파일은 결과 `untracked`, 리뷰 커밋 본문, 태스크 이력에 경고로만 남긴다.
- 리뷰 제외 경로는 리뷰 시작 전과 Codex 실행 후 진행 태스크 소유 경로의 합집합으로 계산한다(리뷰 중 시작된 태스크 포함).
- 리뷰의 status·log·diff·diff-tree 경로 목록은 `--no-renames` 로 읽어 이름 바꾸기가 소유 경로 변경을 가리지 못한다.
- Codex 실행 전 소유 경로·리포트 경로에 커밋 안 된 추적 파일 변경이 있으면 리뷰를 거부(exit 2)하고 working 으로 되돌린다.
- `worker-retain` 실패는 리뷰를 막지 않는다(orca 없음 12 만 중단).
- `orch drop T-xxx --reason "<이유>"`(코디네이터 전용)를 추가한다. dropped 로 바꿔 소유 경로 잠금을 풀고, 워커 해제(최선 노력)·이력·메인 progress 알림을 남긴다. 진행 중 리뷰와는 `review.pid` 로 직렬화한다.
- `orch status` 는 태스크마다 `stage` 와 다음 행동 `next` 를 보여 준다(done·dropped 는 null). 코디네이터 재시작 절차는 `next` 를 따르는 것이다.
- `orch review` 는 `state/tasks/T-xxx/review.pid` 로 태스크당 하나만 돌고(중복 exit 3), 리뷰 프로세스가 없는 reviewing 태스크를 다시 리뷰한다.
- launch_failed 태스크의 start 거부 메시지는 Orca 워커 확인(`worker-list`)과 단계별 `--retry` 명령을 안내한다.
- `orch wait` 는 받은 `[main]` 메시지 id 를 `state/main-msg-ids.json` 에 기록하고, PreToolUse 훅이 그 id 로의 `orca orchestration reply` 를 막는다(id 를 읽지 못하면 통과).
- 훅은 모든 역할의 `.orchestration/state/**` 편집을 막고, 코디네이터 편집은 `.orchestration/`(state 제외)로 제한한다. `run.json` 이 dict 가 아니면 빈 상태로 본다.
- `orch up` 은 영수증의 핸들이 메인 자신의 `ORCA_TERMINAL_HANDLE` 이면 코디네이터로 기록하지 않고 불확실 오류로 멈춘다(영수증·`launched_at` 유지).
- 수정 라운드 spec 은 이전 리뷰 파일이 있을 때만 그 경로를 적는다.
- orch 의 읽기 전용 git 호출은 `GIT_OPTIONAL_LOCKS=0` 으로 돈다. orch 기록 커밋은 `--no-verify` 이고, index.lock 충돌은 지터를 둔 지수 대기로 약 30초까지 재시도한다.
- `orch watch` 는 부모 프로세스가 바뀌면(고아) 커서를 두고 빈 결과로 끝난다. `orch tell` 은 `--body=<본문>` 으로 보내 `-` 로 시작하는 본문이 플래그로 읽히지 않는다.
- config.toml 은 up·task·start·fix·review 만 읽는다. watch·tell·notify·wait·which·accept·drop·bind·setup-done 은 설정이 깨져도 돈다.
- init·up 스킬은 `orch up --wait-run 180` 을 Bash `timeout: 240000` 으로 실행한다. 테스트 환경은 `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` 로 사용자 git 설정을 배제한다.
- README 에 보안 주의(Codex bypass 실행·자격 증명 상속, 신뢰 저장소 전용)와 복구 절차를 둔다. 래퍼 주석의 `--bypass-hook-trust` 설명은 "훅 출처 검증"이 아니라 "저장소 Codex 훅을 신뢰할 수 있을 때만 쓴다"는 주의다.

최종 리뷰 2차
- (E2E 반영의 settled 규칙으로 대체됨) `orch drop` 은 working 태스크의 워커 liveness 가 확실히 끝남(exited 등)일 때만 중단한다. live·unverifiable·확인 불가면 exit 2 로 거부하고, `--force` 로만 넘기며 이력(`forced`)과 메인 알림에 강제 중단으로 남긴다.
- 끊긴 리뷰를 다시 돌리는 `orch review` 는 status `next` 와 coordinator.md 모두 Bash `run_in_background: true` 로 실행하라고 안내한다.
- 훅은 코디네이터의 `orca orchestration check` 직접 호출을 막고 `orch wait` 를 안내한다(배달 가로채기와 `[main]` id 기록 누락 방지). 워커는 그대로다.
- `orch status` 는 config.toml 이 깨져도 돌고 `config_error` 를 싣는다. 이때 `next` 는 최대 라운드 검사 없이 만든다.
- 코디네이터는 소유 경로 안 `untracked` 리뷰 경고를 다음 `orch fix --note` 로 넘기고, accept·drop 의 워커 해제 실패(`released: false`·`release_error`)를 메인에 progress 로 알린다.
- 메인은 `-` 로 시작하는 메시지를 `orch tell -- "<메시지>"` 로 보낸다.

E2E 반영 (Orca 1.4.240)
- `terminal_alive` 는 Orca 의 `orphaned` 를 보지 않는다. `orphaned` 는 렌더러 pane 마운트 여부일 뿐이라 살아 있는 코디네이터도 `true` 일 수 있다. 판정은 `ok`, `connected is True`, `agentIdentity == "claude"` 로 한다(오판 시 `orch up` 이 코디네이터를 중복 기동한다).
- `orch fix` 는 워커 터미널 재사용(`worker-start --terminal`)을 하지 않는다. Orca 가 정산된(`settled`, `retained`) 디스패치의 터미널에 `agent_unconfigured` 를 돌려 항상 실패했기 때문이다. 매 fix 라운드는 이전 디스패치를 `worker-release`(최선 노력, 12 가 아닌 실패는 이력의 `previous_released`·`previous_release_error` 에 기록)한 뒤 `--worktree current --agent claude --model <M> --effort <E>` 로 새 워커를 띄운다. `fix --model/--effort` 가 적용된다. 컨텍스트는 fix spec 의 노트와 이전 리뷰 경로가 잇는다. `launch_failed` 선기록과 `--retry` 는 그대로다.
- 전역 제약 변경: "`--terminal <handle>` 은 워커 터미널 재사용 때만" 규칙을 폐기한다. 워커 배치는 항상 `--worktree current --agent claude --model <M> --effort <E>` 다.
- SessionStart 훅은 프로젝트 안에서 매번(컨텍스트를 출력하지 않는 역할에서도) 자신의 플러그인 루트를 `.orchestration/state/plugin-root` 에 원자적으로 적고, 실패해도 통과한다. shim 은 `ORCA_TEAM_PLUGIN_ROOT` > `state/plugin-root`(`bin/orch` 가 있는 디렉터리일 때) > `installed_plugins.json` 순으로 플러그인을 찾아, 디렉터리 소스 마켓플레이스에서 훅·스킬과 CLI 가 다른 코드를 돌지 않게 한다.
- 워커가 아직 일하는지는 한 규칙으로 판단한다(`worker-show`): `stage == "settled"` 면 터미널이 살아 있어도(retained) 끝난 것이고, 그 밖에는 liveness 가 `live`·`unverifiable` 일 때만 일하는 중이며, 조회 실패는 일하는 중으로 취급해 거부한다. `drop`·`fix`(`working` 일 때만, `changes` 는 검사 없음)·`next` 가 이 규칙을 공유한다. `next` 는 settled 이고 state 가 succeeded 면 `orch review`(백그라운드), 실패·종료면 `orch fix --note "워커 종료"` 를 안내한다. 새 fix 워커 spec 은 이전 워커 대화가 없으므로 report.md 와 `[T-xxx]` 커밋을 먼저 읽게 한다. shim 은 `state/plugin-root` 를 등록된 경로일 때만 믿는다. 일하는 중이면 `fix`·`drop` 은 exit 2 로 거부하고 `--force`(이력 `forced`)로만 넘긴다.
- `scaffold` 도 자기 플러그인 루트를 `state/plugin-root` 에 매번 적는다(SessionStart 가 scaffold 보다 먼저라 표시가 없을 때 대비). 표시가 없거나 믿을 수 없을 때 shim 은 `directory` 소스 마켓플레이스의 `<path>/orca-team`(`bin/orch` 가 있을 때)을 `installed_plugins.json` 의 오래된 캐시 `installPath` 보다 먼저 쓴다. 디렉터리 마켓플레이스는 수정이 다음 세션 시작이나 `/reload-plugins` 에 반영되므로 `claude plugin update` 가 필요 없다.
- README 에 "권한 설정"을 둔다. 코디네이터·워커는 무인으로 돌므로 `.orchestration/bin/orch`, `git`, `orca` 실행이 허용돼야 한다(bypass permissions 권장 또는 allowlist).
- config 로딩의 `UnicodeDecodeError`·`ValueError`·`TypeError` 와 정수가 아닌 `max_rounds` 는 `OrchError`(2)가 된다. `status` 의 `soft_config` 는 `max_rounds` 까지 미리 검증해 `config_error` 로 보고한다.

### v0.1 알려진 한계

- 변조 검사는 `.orchestration/**` 와 `docs/reviews/**`, 다른 진행 태스크의 경로를 제외한다. Codex 가 spec·역할·설정 같은 오케스트레이션 파일을 바꿔도 판정은 무효가 되지 않는다.
- 리뷰 중 추적 안 된 파일 변경은 경고뿐이다. Codex 가 이미 수정된 파일을 stage 만 하면 검사가 못 잡고, 계속 읽을 수 없는 파일(`"?"`)의 변경도 못 잡는다.
- `drop --force` 는 `worker-release` 가 워커를 멈춘다는 데 기대며, 해제가 실패하면 결과에 `release_error` 를 남기지만 잠금은 이미 풀린 상태다.
- init 끝에서 Stop 훅이 빨간 "Stop hook error"(코디네이터 준비 완료 메시지를 읽기 전 watcher 가 꺼져 있다는 차단)를 한 번 보인다. 동작은 맞고 메인이 복구하지만 사용자에게 보인다(E2E F4, 미수정).
- 신뢰되지 않은 Codex 훅 경로(`--bypass-hook-trust` 없이 훅을 만나는 경우)는 E2E 에서 실행해 보지 못했다(§11-3 은 bypass 플래그를 붙인 경로만 검증).
- 도는 워커에 대한 `worker-release` 가 워커를 실제로 멈추는지는 검증하지 못했다(E2E 의 해제는 모두 정산된 디스패치에 대한 것).
- 기본 권한 모드의 무인 실행은 검증하지 못했다. E2E 의 모든 세션이 bypass permissions 였다.
- shim 과 `state/plugin-root` 가 `orch` 실행 코드를 정하므로, 프로젝트에 쓸 수 있는 주체는 이를 바꿀 수 있다(신뢰 경계 = 저장소 + 사용자 계정). shim 은 `plugin-root` 를 `installed_plugins.json` 의 `installPath` 또는 `directory` 소스 마켓플레이스의 `<path>/orca-team` 과 일치할 때만 믿어 위험을 줄인다.
- 새 세션 ID 의 첫 지시에 워커 표시가 없으면 워커가 메인으로 기록돼 제품 파일 편집이 막힌다(표시 없는 워커 공백). Bash 기반 편집(`sed -i`, 리다이렉트)은 가드하지 않는다.

### Codex 래퍼 내장 (2026-10-31)

- 래퍼: Codex 래퍼 `orca-team/scripts/codex-run.sh`를 플러그인에 넣었다. 문서 참조(`docs/codex/`)와 codex 부재 안내(`orch doctor`)만 바꾸고 플래그·종료 코드·보안 계약은 그대로다.
- 회귀 테스트: `test-codex-run.sh`를 `orca-team/tests/`에 넣었다. 느려서 기본 `tests/run.sh`에서 빼고 `--wrapper`·`ORCA_TEAM_TEST_WRAPPER=1`로만 돈다. 빠른 Python 테스트(`test_codex_wrapper.py`)가 파일·실행 권한·`bash -n`·필수 플래그를 본다.
- `paths`의 래퍼 위치 조회(installed_plugins.json의 외부 플러그인 조회)를 `codex_runner()`(`PLUGIN_ROOT/scripts/codex-run.sh`, 덮어쓰기 `ORCA_TEAM_CODEX_RUN`)로 바꿨다. 래퍼가 없으면 "플러그인 설치가 깨졌다"로 exit 12.
- `orch doctor`: "Codex 래퍼"(파일·실행 권한·플래그)를 보고, `codex image_generation` feature 점검을 경고로 더했다.
- 이미지: `/orca-team:image`(`skills/image/SKILL.md`, `templates/image-prompt.md`)를 더하고 결과 판정 표(`docs/codex/02`)를 본문에 넣었다. run-dir은 `${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/runs/`다.
- 계약 문서: 래퍼·이미지 계약을 `docs/codex/` 00·02·03·05·06에 둔다.
- 템플릿·README·init 스킬은 `/orca-team:image`를 쓰고 외부 래퍼 설치를 요구하지 않는다. §9는 대체됐다.
- 버전을 0.2.0으로 올렸다(plugin.json, marketplace.json, `paths.VERSION`). `docs/codex/`의 실행 명령·경로(`orch doctor`, `run.sh --wrapper`, `orca-team/runs`, `/orca-team:image`)를 orca-team 기준으로 고치고, 02 계약에 `--bypass-hook-trust`를 적었다.

### 그릴링 기획·병렬 실행 (2026-10-31, 0.3.0)

후속 설계 `2026-10-31-orca-team-grilling-parallel-design.md` 를 반영했다. 구현 계획은 `docs/superpowers/plans/2026-10-31-orca-team-grilling-parallel.md` 다.

- 새 명령: `orch ask-user`(기획 워커 → 메인 직통 질문, 2초 간격으로 답 파일 확인, 540초 뒤 exit 4), `orch answer`(메인. ask 는 `state/answers/<out-id>.json`, question·approval 은 Run 으로), `orch plan apply [--append]`·`orch start-ready`·`orch design-review`(코디네이터), `orch plan check [--file <경로>]`(plan apply 와 같은 규칙의 읽기 전용 검사, 코디네이터 전용이 아니라 기획 워커가 확정 질문 전에 쓴다), `orch notify --type question --json <파일|->`(코디네이터의 구조화 질문. ask-user 와 같은 스키마 검증과 `render`), `orch reask`(메인. 이미 전한 열린 질문을 watch 형식으로 다시 출력). §6 표의 `tell --reply` 는 호환용으로 남고 메인 지침은 `orch answer` 만 쓴다.
- 종료 코드 4(ask-user 답 대기 중)를 더했다.
- 태스크 상태: `pending`(plan apply 가 만들고 잠금 없음, next 는 `orch start-ready`. 선행 태스크가 dropped 면 시작할 수 없으므로 next 는 메인에 묻고 `orch drop` 하라는 안내다), `awaiting_user`(사용자 승인 대기, 잠금 유지). front matter `depends_on` 은 `orch start` 도 검사한다.
- config.toml 최상위 `max_parallel`(기본 4, `working` 만 센다), `user_approval_roles`(키가 없으면 `[]`, 새 프로젝트는 템플릿이 `["designer"]` 를 적는다. `[roles]` 에 없는 직무는 exit 2 설정 오류).
- 구현 조정
  - tasks.toml 검증을 `orch review` 사전 검사에도 둔다. accept 뒤(done)에는 fix 로 기획 태스크를 되돌릴 수 없기 때문이다.
  - `awaiting_user` 의 fix 는 최대 라운드 검사를 하지 않는다(라운드 번호는 올린다). config.toml 이 깨지면 accept 는 승인 대상 직무를 키가 없을 때와 같은 `[]` 로 본다(계획 결정 6 의 designer 기본값을 최종 리뷰에서 바꿨다. 0.2.0 프로젝트는 `user_approval_roles` 를 넣고 템플릿을 바꾸기 전까지 예전 흐름으로 돈다).
  - start-ready 는 deps → lock → limit 순으로 보고, spec 오류는 reason `spec`, 실행 실패가 있으면 exit 1 이다. 단건 `orch start` 는 `max_parallel` 을 보지 않는다.
  - design-review 는 같은 태스크·라운드를 다시 물으면 exit 3 이고, 링크는 report.md 의 마지막 claude.ai 아티팩트 주소다.
  - `state/plan.json` 은 key → T-id 와 지문(digest)을 담아 `--append` 의 기존 key 변경을 막는다.
  - 워커 spec 에 `선행 태스크: …` 줄과 contracts.md 를 먼저 읽으라는 지시를 넣었다.
  - `ask-user --json -` 로 표준 입력을 받는다. 기획 지침은 heredoc 을 쓴다. `--pending` 은 돌려준 답을 읽음으로 기록하고 대기 중 질문 id 를 함께 준다.
  - 질문 스키마에 선택 필드 `context`(마크다운, 4000자 이하)를 더했다. `render.context` 로 실리고, 메인은 그것을 먼저 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부른다. 확정 질문은 요약(기획 핵심, 자동 결정, 태스크 그래프 표)을, design-review 는 화면별 링크 목록을 `context` 에 담는다.

### 최종 리뷰 반영 (2026-10-31, 0.3.0)

- 이어진 승인 대상 태스크: 선행 태스크와 후속 태스크가 모두 `user_approval_roles` 직무면 선행의 `awaiting_user` 도 충족으로 본다(`unmet_deps`, `orch start`, `orch start-ready`). 디자인 시스템 → 화면처럼 이어진 디자인이 한 번의 design-review 로 함께 승인된다. 승인 대상이 아닌 후속 태스크는 그대로 done 을 기다린다(start-ready reason `deps`).
- design-review 가 기다리는 상태는 `ACTIVE` 에서 `awaiting_user` 를 뺀 것(`REVIEW_WAIT_STATUSES`)과 곧 시작할 pending 이다. 선행이 충족되지 않은 pending(예: `--append` 로 더한, 개발 태스크를 기다리는 디자인)과 `awaiting_user` 태스크와 소유 경로가 겹쳐 사용자 승인 전에는 시작할 수 없는 pending 은 기다리지 않는다(교착 방지).
- 사용자가 선행 디자인 수정을 요청하면 코디네이터는 그 디자인을 선행으로 둔 승인 대상 태스크도 함께 `orch fix` 한다(`coordinator.md`).
- 열린 질문: `orch answer` 는 question·approval 에 답한 id 를 `state/answered.json`(`{"since", "ids"}`)에 원자적으로 덧붙이고, 같은 id 에 다시 답하면 exit 3 이다(전송에 실패하면 기록하지 않는다). `orch status` 의 `open_questions` 는 답 파일이 없는 working planner 의 ask 와 answered.json 에 없는 question·approval 이다. `since` 는 이 기록을 처음 쓴 question·approval id 라 그 앞(0.2.0 에서 답 기록 없이 올라온 질문)은 열린 질문으로 보지 않는다. `orch reask` 는 커서 앞(이미 전한) 열린 질문만 다시 출력하고 커서를 바꾸지 않는다. 아직 전하지 않은 질문은 watch 가 준다. `/orca-team:up` 이 reask 하고, SessionStart 안내가 개수를 알린다(세지 못하면 그 줄만 뺀다).
- `user_approval_roles`: 키가 없으면 `[]`(0.2.0 흐름 유지), `[roles]` 에 없는 직무는 exit 2(status 는 `config_error`). config.toml 을 아예 읽지 못하면(파일 없음·TOML 오류) accept 도 `[]` 로 보고, 읽히는데 `user_approval_roles` 가 틀리면 accept 는 워커를 해제하기 전에 exit 2 로 거부한다(게이트 우회 방지).
- 훅: 기획 워커 세션(worker-spec 첫 줄의 `(planner) 워커다`)은 `worker:planner` 로 표시해 AskUserQuestion 거부 안내가 `orch ask-user` 를 가리킨다. 코디네이터 안내는 `orch notify --type question --json -` 다.
- `orch drop` 은 accept 때 이미 해제한 `awaiting_user` 태스크의 워커를 다시 해제하지 않는다.
- `orch plan apply` 는 `state/plan.json` 이 없거나 깨졌는데 `plan_key` 가 있는 태스크 상태가 있으면 exit 2 로 거부한다(태스크 중복 방지). `plan check --file` 의 오류 문구는 검사한 파일 이름을 쓴다.
- `max_parallel` 은 start-ready 만 지킨다. `orch fix` 와 단건 `orch start` 는 상한을 세지 않는다.
- 코디네이터는 `orch wait` 가 타임아웃으로 끝날 때마다 `orch status` 의 `next` 를 따른다(worker_done 없이 죽은 기획 워커를 알아챈다). 기획 워커는 ask-user 가 돌려준 답을 다른 일보다 먼저 interview.md 에 적는다(읽음으로 기록된 답은 다시 오지 않는다).

### 0.3.0 알려진 한계

- design-review 중복 판정은 마지막 질문의 태스크·라운드 묶음과 통째로 비교한다. 이미 물은 디자인에 새 승인 대상 태스크가 합류하면 묶음이 달라져, 앞 질문의 답을 기다리지 않고 이미 물은 디자인까지 다시 묻는 새 승인 질문이 올라간다(M4).
- 올린 질문을 취소하거나 대체하는 방법이 없다. 더는 필요 없는 question·approval 도 답할 때까지 `open_questions` 에 남는다(M5).

### 실환경 확인 반영 (2026-10-31, 0.3.0)

- F1: 메인의 AskUserQuestion 이 `render.context` 가 있는 열린 질문을 물을 때 transcript 끝 assistant 텍스트에 정규화한 context 앞 80자가 없으면 훅이 context 를 담아 거부한다. outbox id 마다 한 번만 거부하고(`state/context-denied.json`) 판단할 수 없으면 통과시킨다.
- F4: launch 의 pending 기록에 orch pid 를 남긴다. pid 가 살아 있고 10분이 지나지 않았으면 실행 중으로 보고 `next` 는 기다리라는 안내, `start`·`fix`(`--retry` 포함)는 exit 2, status 행은 `launching: true` 다(status 는 `launch_failed` 그대로).

### 디자인 디렉터 (2026-10-31, 0.4.0)

설계: `2026-10-31-orca-team-design-director-design.md`. 구현에서 정한 것:
- `orch ask-user` 허용 직무는 `ask.ASK_ROLES = ("planner", "director")` 다. 훅은 정규식 `ASK_WORKER_RE` 로 `worker:planner`·`worker:director` 를 표시하고, 테스트가 둘을 묶는다.
- `plan.director_problems` 는 config 에 director 가 있을 때만 돈다. director 개수는 tasks.toml 전체로 세고, director 의존은 새로 더하는 designer 만 본다(`--append` 는 적용한 태스크를 바꿀 수 없다).
- `lint.design_lint` 는 이모지, 2px 이상 왼쪽 줄 강조(CSS·JSX 인라인·Tailwind), 파랑·보라 그라디언트(색상각 파랑 190~245°, 보라 245~300°, Tailwind from·via·to)를 경고로만 찾는다. 50건에서 자른다.
  - 0.4.1: 이모지는 U+1F000–U+1FAFF, BMP 의 Emoji_Presentation 문자, U+FE0F 가 붙은 공백 아닌 문자만 잡는다(✓ ★ ▶ 같은 텍스트 기호 단독은 뺀다). 왼쪽 줄은 괄호 밖에 홀로 선 transparent·none·hidden 이 있으면 빼고(`var(--none)` 은 뺄 이유가 아니다), 너비만 준 `border-left-width`·`borderLeftWidth`, 너비 낱말 medium(3px)·thick(5px), Tailwind `border-s-N`·`border-l-[…]`·`border-s-[…]` 도 잡는다. `oklch()` 는 채도 0.05 이상에서 색상각 200~280° 파랑, 280~330° 보라로 본다. 그라디언트 줄 번호는 bisect 로 찾는다(1MB 최악 입력이 선형).
- 리뷰 프롬프트는 `{design_section}` 자리에 디자인 직무(director·designer·developer)만 `templates/review-design.md` 를 채운다.
- director 보강 태스크는 코디네이터가 spec front matter `owns:` 로 소유 경로를 좁힌다.
- 디자인 섹션은 직무뿐 아니라 config 에 `[roles.director]` 가 있을 때만 리뷰 프롬프트에 들어간다. director 가 없는 0.3.0 프로젝트의 리뷰는 바뀌지 않는다.
- Design System 타입을 찾지 못하면 director 는 escalation 대신 프리앰블 ask 로 코디네이터에게 알리고, 코디네이터가 메인에 묻는다(설계 §9 와 다르다). 같은 결과를 기존 경로로 얻는다.
- 화면 designer 태스크 owns 는 `docs/design/screens/<화면>/**` 와 `assets/<화면>/**` 다. 설계 §6.3 의 owns 에 에셋 경로를 더했다(리뷰가 owns 밖 커밋을 거부하므로 화면 에셋도 owns 안에 둔다). director 는 래스터 에셋을 만들지 않는다.
- design-review 링크는 리포트의 마지막 claude.ai 아티팩트 주소에서 `docs/design/direction.md` 의 `design_system_url` 을 뺀 것이다(실환경 확인에서 화면 링크가 모두 시스템 링크로 나온 결함을 고쳤다).
- 0.4.1: `orch fix --supplement T-yyy` 는 디자인 시스템 보강(status done 인 director 태스크)을 반영하는 fix 다. 이력에 `supplement` 를 남기고 awaiting_user 에서 해도 `user_requested` 를 남기지 않는다. 없는 ID, director 가 아니거나 done 이 아닌 태스크, `--retry` 와 함께 쓰기는 exit 2 다.
- 0.4.1: 에셋 전용 designer 태스크(owns 가 모두 `assets/` 아래. owns 가 없으면 config 기본 owns 로 판단)는 `director_problems` 에서 뺀다. director 에 의존하지 않아도 되고, 화면 designer 와 섞이면 화면 designer 만 지적한다.

### 문구 규칙 (2026-11-05, 0.5.0)

설계: `2026-11-05-orca-team-text-rules-design.md`. 구현에서 정한 것:
- `textlint.text_lint` 는 태스크 커밋마다 `git show -U0` 의 hunk 안 `+` 줄을 모으고, 내용이 같은 현재 줄만 본다. 파일 머리(`+++`)는 hunk 밖이라 빠지고 `++` 로 시작하는 내용 줄은 남는다. 오탈자 정규식은 긴 표기부터 맞춰 "안되요" 를 "되요" 로 두 번 세지 않는다.
- 이중피동은 되어·보여·쓰여·불려 뒤의 지·진·짐·져·졌을 모두 잡는다(설계 §8.3 표보다 넓다).
- 자동 검사가 실패해도(예외) 리뷰는 막히지 않는다. 프롬프트에 "자동 검사 실패: 예외 이름: 메시지" 한 줄(200자까지)을 싣고, UTF-8 이 아닌 git 출력은 그 파일만 건너뛴다.
- 쉼표 "고," 는 하·되·있·없·않·이·었·았·했·겠 뒤에서만 잡아 "신고," "참고," 같은 명사를 거른다.
- 텍스트 대상에 모바일 문구 파일(`.arb`, `.dart`, `.strings`, `.xcstrings`, `.xml`, `.kt`, `.swift`)을 더했다. `.dart`·`.kt`·`.swift`·`.strings` 는 줄 첫머리 주석을, `.xml` 은 `<!--` 줄을 건너뛴다.
- git pathspec 은 `:(literal)` 로 넘겨 `[id]` 같은 경로가 glob 으로 풀리지 않게 한다.
- "아니요"·"좋아요" 는 해요체 종결로 세지 않고, 영문 `unlock`·`elevate` 는 상투어에서 뺐다(코드 식별자·i18n 키 오탐). 제외 경로에 `**/*.test.*`, `**/*.spec.*`, `**/__tests__/**`, `e2e/**` 를 더했다.
- 높임법 섞임은 줄 끝 한글 낱말(뒤에 따옴표·`<`·`|`·줄 끝만 오는)로 판정한다. "필요·중요·개요·주요·수요" 로 끝나는 낱말은 해요체로 보지 않는다. 두 종결체 줄 수가 같으면 하십시오체 쪽 줄을 적는다.
- 디렉터 산출물(`docs/design/board/**`, `docs/design/pilots/**`, `docs/design/system/**`)은 문구 대상이 아니고, `### 문구 점검` 은 designer·developer 태스크만 적는다(실환경 점검에서 나옴).
- 리뷰 프롬프트 자리는 `{design_section}{text_section}` 순서다. 문구 절은 직무와 무관하게 `.orchestration/text-rules.md` 가 있고 한글이 든 텍스트 대상 파일이 바뀌었을 때만 붙는다. 디자인 절의 "orch 자동 검사" 와 구분하려고 "orch 문구 자동 검사" 라고 부른다.
- 기본 config 의 developer owns 에 `README.md`, `docs/user/**`, `docs/policy/**` 를 더해, owns 밖 커밋을 확인하던 리뷰 테스트는 `NOTES.md` 를 쓴다.
- 업그레이드는 README 가 파일 복사로 안내한다. 설계 §9 의 init 재실행과 결과가 같다(scaffold 는 없는 파일만 만든다).
- 실환경 점검에서 용어집 검사 두 가지를 고쳤다. 금지 표기는 다른 행의 쓰는 표기가 덮어도 건너뛰고(서비스 이름 "오늘할일" 이 "할일" 로 잡히던 오탐), 괄호 조건이 붙은 금지 표기("지우기(라벨)")는 조건을 평가할 수 없어 읽을 때 건너뛴다(떼어 내면 조건부 금지가 전역 금지가 되어 "이 약관" 9줄이 잡혔다). 플래너에게는 조건부 표기를 메모 칸에 적게 한다.
- 텍스트 대상은 검사 대상 줄(주석 줄, Markdown 코드 블록·인라인 코드 제외)에 한글이 하나 이상 있어야 한다. 주석에만 한글이 있는 `playwright.config.js` 에 문구 절이 붙던 일을 막는다.

### 코디네이터 자동 부활 (2026-11-06, 0.5.1)

설계: `2026-11-06-orca-team-auto-up-design.md`. 구현에서 정한 것:
- `orca.terminal_state` 는 `ok: true` 인데 `result.terminal` 이 없거나 비면 unknown 이다(설계 초안보다 좁혀 확실한 경우만 dead).
- `autoup.spawn_up` 은 `/bin/sh -c` 로 `orch up` 을 백그라운드에 띄우고 sh 는 pid 를 출력한 뒤 바로 끝난다. 훅이 시간 제한으로 끊겨도 `orch up` 은 계속 돌고, 남는 Popen 객체가 없어 ResourceWarning 도 없다.
- `autoup` 은 훅의 `auto_up_context` 안에서 늦게 import 한다. guard 훅은 매 도구 호출마다 돈다.
- 세션 시작 안내에 자동 부활 문구가 없으면 기본 줄에 `/orca-team:up` 을 수동 복구로 남긴다.
- 최종 리뷰 반영:
  - `setup.up` 은 run.json 잠금 안에서 시작 유예를 다시 보고 기동을 선점한다. 선점에 진 `orch up` 은 `agent launch` 없이 `starting` 을 돌려줘 코디네이터가 둘 뜨지 않는다.
  - 자동으로 띄운 뒤 10분(`RETRY_WINDOW_SECONDS`) 안에 다시 `dead` 면 띄우지 않고 재사망 문구(`FAILED`)만 넘긴다. 시각은 `auto-up.json` 의 `spawned_at` 이다.
  - `orca.call` 에 `timeout` 을 더했다. `terminal_state` 만 3초(`TERMINAL_STATE_TIMEOUT`)를 넘기고, 넘으면 `unknown` 이다. `terminal_alive` 는 제한이 없다.
  - 같은 문구는 `auto-up.json` 의 `notice` 와 비교해 다시 넘기지 않는다. 세션 시작(force)은 같아도 넘기고, 문구가 없으면 `notice` 를 지운다.
  - 60초(`PID_STALE_SECONDS`)보다 오래된 `auto-up.pid` 는 살아 있는 pid 라도 진행 중으로 보지 않는다.
  - 다시 띄움 문구는 `코디네이터가 꺼져 있어 자동으로 다시 띄우는 중이다.` 로 시작한다. 훅이 돌아올 때 `orch up` 은 아직 도는 중이다.
  - `terminal_state` 는 `result` 가 dict 가 아니거나 `OSError`(실행 권한 없음 등)가 나도 `unknown` 이다.
