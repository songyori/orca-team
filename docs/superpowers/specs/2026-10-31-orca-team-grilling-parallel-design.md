# orca-team 그릴링 기획·병렬 실행 설계

- 날짜: 2026-10-31
- 대상: orca-team 0.2.0(`orca-team/`). 기존 설계 `2026-10-30-orca-team-design.md`의 후속이다.
- 상태: 사용자가 승인했다(브레인스토밍 결정 6개).

## 1. 목적

기획 단계에서 시간을 들여 의문점을 남기지 않는다. 그 뒤 디자인·개발·테스트는 끊기지 않고 병렬로 빠르게 진행한다.

1. 기획 워커가 사용자를 그릴링 인터뷰한다. 질문은 한 번에 하나씩, 짧게 한다. 질문마다 예시와 추천 선택지, 추천 사유를 붙인다.
2. 기획 워커는 제품 기획과 기술 설계(인터페이스 약속)를 같은 인터뷰에서 확정하고, 태스크 그래프를 낸다.
3. orch는 태스크 그래프에서 시작 가능한 태스크를 동시 상한까지 한 번에 띄운다.
4. 디자인은 모아서 한 번에 사용자 승인을 받는다. 그 뒤로는 막혔을 때만 사용자에게 묻는다.

## 2. 사용자 결정

| # | 질문 | 결정 |
|---|---|---|
| 1 | 인터뷰 질문 경로 | 기획 워커 → 메인 직통(`orch ask-user`). 코디네이터를 거치지 않는다 |
| 2 | 병렬화 방식 | 기획 워커가 `docs/planning/tasks.toml` 태스크 그래프를 쓰고, orch가 결정적으로 띄운다 |
| 3 | 동시 상한 | 4(`config.toml`의 `max_parallel`). 프로젝트별로 바꿀 수 있다 |
| 4 | 기술 설계 담당 | 기획 워커가 같은 인터뷰에서 함께 정한다(`docs/planning/contracts.md`) |
| 5 | 이후 사용자 확인 시점 | 기획 확정 1회, 디자인 완료 후 승인 1회. 그 뒤로는 막혔을 때만 |
| 6 | 디자인 승인 단위 | 모아서 한 번. Codex 리뷰를 통과한 디자인을 한꺼번에 묻는다 |

## 3. 전체 흐름

```
사용자 요청 → 메인 orch tell → 코디네이터: 기획 태스크 생성·시작
기획 워커 ⇄ (orch ask-user / orch answer) ⇄ 메인 ⇄ 사용자   … 질문 반복
기획 워커: 최종 요약 확인 질문 → 확정 → docs/planning/ 산출물 커밋 → worker_done
코디네이터: orch review(Codex) → accept → orch plan apply → orch start-ready
디자이너·개발자·테스터 워커 병렬(최대 4) → 리뷰 → accept → orch start-ready …
디자인 태스크 전부 awaiting_user → orch design-review → 메인 → 사용자 일괄 승인
승인된 디자인 done → 의존 개발 시작 … 전부 done → 메인에 완료 알림
```

## 4. 그릴링 인터뷰

### 4.1 `orch ask-user` (기획 워커)

```
orch ask-user --task T-001 --json <질문파일>   # 새 질문을 올리고 답을 기다린다
orch ask-user --task T-001 --wait out-NNNN     # 이미 올린 질문의 답을 이어서 기다린다
orch ask-user --task T-001 --pending           # 답은 왔지만 아직 읽지 않은 질문 목록
```

허용 조건: 태스크 `T-001`의 직무가 `planner`이고 상태가 `working`이어야 한다. 아니면 exit 2로 끝난다.

질문 파일은 JSON이다. orch가 스키마를 검증하고, 어기면 exit 2와 함께 어긴 필드를 알린다.

| 필드 | 필수 | 규칙 |
|---|---|---|
| `question` | 예 | 한 문장, 300자 이하 |
| `example` | 예 | 질문을 구체화하는 예시, 300자 이하 |
| `options` | 아니오 | 없거나 2~4개. 각 항목은 `{label(30자 이하), description}` |
| `recommended` | options가 있으면 예 | `options`의 인덱스(0부터) |
| `reason` | options가 있으면 예 | 추천 사유 |
| `header` | 아니오 | 12자 이하 꼬리표. 없으면 "기획" |

orch가 하는 일:

1. outbox에 `type: "ask"` 항목을 쓴다. 항목에는 `task`와, 메인이 손대지 않고 쓰는 `render`를 넣는다.
2. `render`는 이렇게 만든다.
   - 질문 문장 뒤에 줄을 바꿔 `예: <example>`를 붙인다.
   - 추천 선택지를 맨 앞으로 옮긴다. 그 label 끝에 ` (추천)`을 붙이고, description 앞에 `추천 사유: <reason>. `를 붙인다.
   - `options`가 없으면 `render.options`도 두지 않는다.
3. 답 파일 `.orchestration/state/answers/<out-id>.json`이 생길 때까지 2초 간격으로 확인한다.
4. 답이 오면 `{"id": "<out-id>", "answer": "<텍스트>"}`를 출력하고 exit 0으로 끝난다. 이때 그 질문을 "읽음"으로 기록한다.
5. 540초(9분)가 지나도 답이 없으면 `{"id": "<out-id>", "status": "waiting"}`를 출력하고 exit 4로 끝난다. Bash 포그라운드 실행이 10분으로 제한되기 때문이다. 기획 워커는 `--wait <out-id>`로 다시 기다린다.

같은 태스크에 답을 기다리는 질문이 이미 있으면 새 질문은 거부한다(exit 3). 질문은 한 번에 하나다.

### 4.2 `orch answer` (메인)

```
orch answer out-NNNN "<답>"
```

outbox 항목의 `type`에 따라 처리가 갈린다.

- `ask`: 답 파일을 원자적으로 쓴다. 파일이 이미 있으면 exit 3.
- `question`, `approval`(코디네이터가 올린 질문): 지금의 `orch tell --reply out-NNNN "<답>"`처럼 Run으로 보낸다.

호환을 위해 `orch tell --reply`는 그대로 둔다. 메인 지침은 `orch answer` 하나만 쓰도록 바꾼다.

### 4.3 메인 표시 규칙 (`templates/CLAUDE.section.md`)

- `ask`와 `approval`은 `render`를 그대로 AskUserQuestion에 넘긴다. 질문은 1개, 단일 선택이고 header는 `render.header`다.
- `render.options`가 없으면 질문과 예시를 평문으로 묻는다. 사용자가 채팅으로 답하면 그 내용을 그대로 전달한다.
- 사용자가 Other로 자유 입력하면 그 내용도 그대로 `orch answer`로 넘긴다.
- 메인은 질문을 바꾸거나 합치지 않고, 설명을 덧붙이지 않는다.

### 4.4 기획 역할 지침 (`templates/roles/planner.md` 다시 쓰기)

인터뷰 방식:

- 한 번에 한 질문만 한다. 질문은 짧고 분명하게 쓰고, 길게 설명하지 않는다.
- 선택형 질문에는 항상 예시, 추천, 추천 사유를 넣는다.
- 답을 받으면 다음 질문으로 넘어간다. 앞선 답과 모순되면 그 모순을 짚는 질문을 한다.

점검표의 모든 항목이 "결정됨" 또는 "범위 밖"이 될 때까지 인터뷰를 이어 간다. 질문 수에는 상한이 없다.

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

기술적으로 답이 자명한 항목은 묻지 않는다. 추천안으로 정하고, 최종 요약에 "자동 결정"으로 표시한다.

마지막 질문은 확정 질문이다. 요약에는 기획 핵심, 자동 결정 목록, 태스크 그래프(태스크, 직무, 선행 태스크)를 담는다. 선택지는 "확정 (추천)"과 "수정할 부분 있음" 두 가지다.

산출물은 `docs/planning/`에 둔다.

- `spec.md`: 기능마다 목적, 범위, 흐름, 수용 기준, 범위 밖을 적는다.
- `contracts.md`: API(경로, 요청, 응답, 오류), 공유 데이터 형태, 모듈 경계와 소유 경로를 적는다. 병렬로 개발하는 워커들이 이 문서만 보고 서로의 결과를 가정할 수 있어야 한다.
- `tasks.toml`: 5절 형식을 따른다. 서로 의존하지 않는 태스크에는 의존을 걸지 않는다. 병렬 실행이 기본이다.
- `interview.md`: 질문과 답을 기록한다(질문 번호, 질문, 답). 코디네이터가 워커 질문에 답할 때 근거로 쓴다.

기획 워커는 AskUserQuestion을 쓰지 않는다(지금처럼 훅이 막는다). 질문은 `orch ask-user`로만 한다.

## 5. 태스크 그래프

### 5.1 `docs/planning/tasks.toml`

```toml
[[task]]
key = "login-design"            # 영문 소문자·숫자·하이픈, 파일 안에서 유일
role = "designer"
title = "로그인 화면 디자인"
owns = ["docs/design/login/**"]
depends_on = []
target = "..."                  # spec Target
change = "..."                  # spec Change
constraints = "..."             # spec Constraints (contracts.md 절 참조 포함)
acceptance = ["...", "..."]     # spec Acceptance, 한 줄씩

[[task]]
key = "login-ui"
role = "developer"
title = "로그인 화면 구현"
owns = ["src/ui/login/**"]
depends_on = ["login-design", "auth-api"]
# target · change · constraints · acceptance 는 위와 같다
```

### 5.2 `orch plan apply` (코디네이터)

먼저 검증한다. 하나라도 실패하면 exit 2로 끝나고 아무것도 만들지 않는다.

- TOML 문법이 맞고 필수 필드가 있다. `role`은 config에 있는 직무다.
- `key`가 유일하다.
- `depends_on`에 적힌 key가 모두 존재한다.
- 의존 관계에 순환이 없다.
- `owns`가 비어 있지 않다.
- 의존 관계로 이어지지 않아 동시에 돌 수 있는 두 태스크는 `owns`가 겹치지 않는다(`globs.overlaps`로 판정).

검증을 통과하면:

- 태스크마다 `orch task new`와 같은 규칙으로 `.orchestration/tasks/T-xxx.md`를 만든다. front matter에는 `owns`, `depends_on: T-aaa, T-bbb`, `plan_key`를 넣는다.
- spec 파일들을 경로를 지정한 커밋 하나로 묶는다. 커밋 메시지는 `[plan] apply: N tasks`다.
- `state/plan.json`에 `{key: T-id}` 매핑을 남긴다.
- 각 태스크의 상태 파일을 만들고 `pending`으로 둔다. 잠금은 잡지 않는다.

적용은 한 번만 한다. 다시 적용하려면 `--append`를 붙여야 한다. `--append`는 새 key만 추가하고, 기존 key를 바꾸려 하면 거부한다.

### 5.3 `orch start-ready` (코디네이터)

`pending` 태스크 중 아래 세 조건을 모두 만족하는 것을 고른다. 고르는 순서는 `tasks.toml`의 순서다.

- 선행 태스크가 모두 `done`이다.
- `check_lock`을 통과한다.
- 지금 `working`인 태스크 수가 `max_parallel`보다 적다.

고른 태스크는 지금의 `start`와 같은 배치로 띄운다.

결과로 `{"started": [...], "waiting": [{id, reason}], "running": n, "max_parallel": 4}`를 낸다. `reason`은 `deps`, `lock`, `limit` 중 하나다.

어느 태스크의 실행이 실패해도 나머지는 계속 띄운다. 실패한 태스크는 `launch_failed` 상태로 결과에 넣는다.

`pending`은 `ACTIVE`에 넣지 않으므로 잠금을 잡지 않는다. `pending` 태스크의 `next_action`은 `orch start-ready`다.

`orch start`로 `pending` 태스크를 하나씩 띄울 수도 있다. 이 경우에도 선행 태스크가 끝나지 않았으면 거부한다.

### 5.4 `max_parallel`

`config.toml` 최상위에 `max_parallel = 4`를 둔다. 값이 없으면 4로 본다.

세는 대상은 `working` 상태인 태스크다. Codex 리뷰 중(`reviewing`)이거나 사용자 승인을 기다리는(`awaiting_user`) 태스크는 세지 않는다.

## 6. 디자인 일괄 승인

config에 `user_approval_roles = ["designer"]`를 둔다. 이 직무의 태스크는 Codex가 승인한 뒤 `orch accept`를 해도 `done`이 되지 않고 `awaiting_user`가 된다.

- `awaiting_user`는 `ACTIVE`에 넣어 소유 경로 잠금을 유지한다. 수정 라운드가 이어질 수 있기 때문이다.
- `awaiting_user`가 될 때 워커를 해제하고, 메인에 progress를 알린다.

`orch design-review`(코디네이터):

- 조건이 맞을 때만 실행된다.
  - 승인 대상 직무의 태스크 중 `pending`, `working`, `reviewing`, `changes`, `approved`, `launch_failed` 상태가 하나도 없다.
  - `awaiting_user` 상태가 하나 이상 있다.
  - 조건이 맞지 않으면 exit 2로 끝나고, 무엇을 기다리는지 알린다.
- 조건이 맞으면 outbox에 `type: "approval"` 질문 하나를 쓴다. 4.3의 `render` 규칙을 따른다.
  - 질문: "디자인 N개를 확인해 주세요"
  - 예시 자리: 화면마다 `T-id 제목: <Design 링크>`를 한 줄씩 적는다. 링크는 `docs/tasks/T-xxx/report.md`에서 `https://claude.ai/...artifact/...` 형태의 주소를 찾아 쓴다.
  - 선택지: "전부 승인 (추천)", "수정 요청(화면과 내용을 적어 주세요)"
- 질문 id를 `state/design-review.json`에 남긴다.

메인이 `orch answer`로 답하면 그 답은 Run을 거쳐 코디네이터에게 간다(4.2). 코디네이터는 답을 해석해 다음처럼 처리한다.

- 승인된 화면: `orch accept T-xxx`로 `awaiting_user`에서 `done`이 된다. 이 경우만 사용자 승인으로 인정한다.
- 수정을 요청한 화면: `orch fix T-xxx --note "<사용자 요청>"`를 한다. `fix`는 `awaiting_user` 상태를 허용하고, 라운드 수를 세고, 새 워커를 띄운다.
- 수정한 화면이 다시 `awaiting_user`가 되면 `orch design-review`는 그 화면들만 묻는다.
- 처리가 끝나면 `orch start-ready`를 한다.

디자인에 의존하지 않는 태스크는 이 승인을 기다리지 않고 그래프의 의존만 따른다.

## 7. 자율 진행 규칙 (`templates/coordinator.md`)

- 사용자 요청을 받으면 먼저 기획 태스크 하나를 만들어 시작한다. `docs/planning/tasks.toml`이 이미 있고 사용자가 추가 요청만 했다면, 기획 워커에게 증분 기획을 맡긴다.
- 기획 태스크를 accept한 뒤 `orch plan apply`, 이어서 `orch start-ready`를 한다.
- accept, fix, drop을 하거나 design-review 답을 처리할 때마다 `orch start-ready`를 한다.
- 워커의 질문(ask)에는 먼저 `docs/planning/spec.md`, `contracts.md`, `interview.md`로 답한다. 다음 경우에만 `orch notify --type question`으로 사용자에게 묻는다.
  - 문서가 다루지 않는 제품 결정이 필요할 때
  - 문서끼리 모순될 때
- 사용자에게 묻는 경우는 이 셋뿐이다. 질문은 모두 4.1 형식(예시, 추천, 사유)을 따른다.
  - 디자인 일괄 승인
  - 리뷰가 최대 라운드를 넘었을 때
  - 위 문서로 답할 수 없는 문제
- 모든 태스크가 `done` 또는 `dropped`가 되면 메인에 `done` 알림을 보낸다.

## 8. 바뀌는 것

| 파일 | 변경 |
|---|---|
| `lib/orch/ask.py` (신규) | `ask_user`, `answer`, 질문 스키마 검증, `render` 생성 |
| `lib/orch/plan.py` (신규) | `tasks.toml` 검증, `plan apply`, `start-ready`, `design-review` |
| `lib/orch/tasks.py` | `pending`·`awaiting_user` 상태, `depends_on` front matter, `accept`의 승인 게이트, `fix`의 `awaiting_user` 허용, `next_action` 확장 |
| `lib/orch/comm.py` | outbox 항목에 `ask`·`approval` 타입 추가. 메인의 `watch`는 그대로 전달한다 |
| `lib/orch/config.py` | `max_parallel`, `user_approval_roles` |
| `lib/orch/cli.py` | `ask-user`, `answer`, `plan apply`, `start-ready`, `design-review` |
| `hooks/orca_team_hook.py` | 변경 없을 것으로 본다. 기획 워커의 AskUserQuestion 차단은 유지한다 |
| `templates/roles/planner.md` | 4.4대로 다시 쓴다 |
| `templates/coordinator.md` | 7절 규칙 |
| `templates/CLAUDE.section.md` | 4.3 표시 규칙, `orch answer` |
| `templates/config.toml` | `max_parallel = 4`, `user_approval_roles = ["designer"]` |
| `templates/worker-spec.md` | `depends_on`과 `contracts.md`를 먼저 읽으라는 한 줄 |
| `README.md`, 기존 설계 §13 | 요약 반영, 버전 0.3.0 |

## 9. 오류 처리

- `ask-user`로 답을 기다리던 기획 워커가 죽은 경우:
  - 질문은 outbox에 남는다. 메인이 답하면 답 파일만 남는다.
  - 새 기획 워커(`orch fix`)는 `interview.md`와 `orch ask-user --pending`(답은 왔지만 읽지 않은 질문)을 보고 이어서 진행한다.
- `plan apply`가 검증에 실패하면 코디네이터가 `orch fix <기획 태스크> --note "<orch 오류>"`로 기획 워커에게 고치게 한다. 사용자에게는 묻지 않는다.
- `start-ready`에서 일부 태스크만 실행에 실패하면, 실패한 태스크는 기존 `launch_failed` 복구 규칙을 따른다.
- `design-review` 답이 모호하면(어느 화면인지 알 수 없을 때) 코디네이터가 확인 질문을 하나 한다(4.1 형식).

## 10. 테스트

- **ask**
  - 스키마 검증: 필드 누락, 선택지 수, `recommended` 범위
  - `render`: 추천 선택지를 맨 앞으로 옮기기, 꼬리표, 사유 접두
  - planner·working 조건, 한 번에 한 질문(exit 3)
  - 답 대기와 540초 시간 초과(시계는 모의 객체로)
  - `--pending`
  - `answer`의 타입별 분기(답 파일 쓰기와 tell)
- **plan**
  - 검증: 순환, 없는 의존, 동시에 돌 수 있는 태스크의 owns 겹침, 의존으로 이어진 태스크의 owns 겹침은 허용
  - spec 생성과 커밋, `plan.json`, `--append`
  - `start-ready`: deps, lock, limit, 일부 실패, 고르는 순서
- **승인 게이트**
  - accept가 `awaiting_user`로 가는지
  - design-review 조건과 링크 추출
  - `awaiting_user` 상태의 fix와 재승인
  - `next_action`
- **템플릿**: planner 지침의 점검표와 형식 규칙, 메인 표시 규칙, 코디네이터 규칙의 문구를 테스트한다.
- **실환경 확인**: 작은 프로젝트로 인터뷰 질문 3~5개를 주고받는지, 독립 태스크 2개 이상이 동시에 시작되는지 본다. 구현 계획의 마지막 태스크에서 한다.

## 11. 범위 밖

- 디자인 외 직무의 사용자 승인. config로 직무를 추가할 수는 있지만 기본값은 designer뿐이다.
- 기획 워커 여러 명이 동시에 인터뷰하는 것. 한 프로젝트에서는 한 번에 한 기획 인터뷰만 한다.
- 메인 이외의 경로로 사용자에게 묻는 것.

## 12. 실환경 확인 결과 (2026-10-31, Orca 1.4.245 / codex-cli 0.163.0)

검증 프로젝트는 할 일 웹앱(정적 HTML)이다. 13:44–14:40 KST 에 init 부터 마지막 done 까지 진행했고 태스크 4개가 모두 done 이 됐다. outbox 마지막 줄은 `type: "done"`(out-0012)이다.

| 항목 | 결과 | 근거 |
|---|---|---|
| 0.3.0 scaffold(planner 지침, config 기본값) | 통과 | `orch --version` 은 `orca-team 0.3.0` 이다. `roles/planner.md` 에 `orch ask-user` 가 5번 나온다. config 에 `max_parallel = 4`, `user_approval_roles = ["designer"]` 가 있다. |
| 인터뷰 왕복 3~5회(ask → 메인 AskUserQuestion → answer) | 통과 | 왕복은 3회다(out-0002 삭제 동작, out-0003 디자인 톤, out-0004 확정). 모두 `task: T-001` 이고 header 는 5자다. 확정 질문을 빼면 2개라 3~5개 기대보다 1개 적다. 사용자가 "꼭 필요한 것만 5개 이내"를 요청했고, 나머지는 확정 질문의 "자동 결정"으로 처리됐다(`templates/roles/planner.md` 점검표·자동 결정 기준). Q1·Q3 은 검증자가 아닌 외부 입력(Orca 창을 보던 사용자로 추정)으로 답해졌다. 고른 답은 모두 추천안이다. |
| 확정 질문 `render.context` 를 메인이 먼저 그대로 표시 | 실패 | out-0004 에는 context(기획 핵심, 자동 결정, 태스크 그래프 표)가 있었다. 메인 transcript 에서 watch 출력을 읽은 뒤 thinking 만 거쳐 바로 AskUserQuestion 을 불렀고, 그 사이 text 출력이 없다. 같은 세션의 디자인 approval(out-0009)에서는 context 를 그대로 출력했다. 관련 위치는 `templates/CLAUDE.section.md` "질문 표시 규칙"이다. 기계적 보강 후보는 `hooks/orca_team_hook.py` 의 AskUserQuestion main 분기와 `lib/orch/comm.py:watch` 출력이다. |
| ↳ 위 실패의 수정 | 이후 수정 | 1565d87: 메인 AskUserQuestion 훅이 context 를 출력하지 않은 질문을 context 를 담아 한 번 거부한다. `CLAUDE.section.md` 질문 처리 항목에 순서((1) context 출력 (2) AskUserQuestion (3) `orch answer`)를 적었다. |
| render 그대로 표시(추천 꼬리표, 예시 줄) | 통과 | 질문 4개(ask 3, approval 1) 모두 AskUserQuestion 입력의 question(`예:` 줄 포함)·header·options 의 label·description·순서가 render 와 같다. 첫 label 은 ` (추천)` 으로 끝나고 description 은 `추천 사유:` 로 시작한다. |
| 답 파일·읽음 기록, exit 3·4 동작 | 통과 | `answers/` 에 out-0002·0003·0004 마다 `.json` 과 `.read` 가 있다. answer 뒤 1~2초 안에 `.read` 가 생겼다. 기획 워커의 `ask-user` 3회는 모두 exit 0 이고 `timeout: 600000` 이다. exit 3 거부와 exit 4 는 없었다. |
| 기획 워커 장시간 대기 중 liveness | 미확인 | 답이 10~40초 안에 와서 9분 대기나 exit 4 가 생기지 않았다. 작업 중(13:54) `worker-show` liveness 는 `live` 였다. |
| docs/planning 산출물과 [plan] apply 커밋 | 통과 | `spec.md`, `contracts.md`, `tasks.toml`, `interview.md`(Q1~Q3 와 답)가 있다. 커밋은 `[T-001] review r1: APPROVE`(ddb0fef) 다음에 `[plan] apply: 3 tasks`(46a7ab1) 다. plan.json 은 todo-design→T-002, todo-store→T-003, todo-ui→T-004 다. T-002·T-003 은 `depends_on:` 이 비어 있고, T-004 는 `depends_on: T-002, T-003` 이다. |
| 독립 태스크 2개 이상 동시 시작 | 통과 | `start-ready` 한 번의 출력에서 `started` 는 T-002·T-003(working)이고, `waiting` 은 T-004 `reason: deps` 다. work 시각은 14:02:45 와 14:02:48 이다. 14:02:50 status 에 둘이 함께 `working` 이고, T-004 의 `next` 는 `orch start-ready` 다. 시작 직후 한 폴링(14:02:45)에 T-002 가 `launch_failed` 로 보였다. `lib/orch/tasks.py:start` 가 Orca 호출 전에 미리 기록하기 때문이다(4초 뒤 working). |
| design-review 링크와 일괄 승인 | 통과 | T-002 는 r1 REQUEST_CHANGES 뒤 r2 APPROVE 를 받고 `awaiting_user` 가 됐다. approval out-0009 의 context 는 `- T-002 할 일 화면 디자인(미니멀 라이트): https://claude.ai/artifact/KyoWYtt7qyag4CzeWq6K9z` 이다. 메인이 이 줄을 먼저 평문으로 보여 줬다. "전부 승인 (추천)" 답 뒤 14:22:32 에 T-002 는 `done`, T-004 는 `working` 이 됐다. design-review.json 에 out-0009 와 `{"T-002": 2}` 가 기록됐다. |
| 입력 방해 0건 | 통과 | 메인 터미널 read(`--limit 5000` 포함)와 메인 transcript 에서 `orchestration message` 가 0건이다. |
