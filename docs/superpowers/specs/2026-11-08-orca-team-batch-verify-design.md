# orca-team 묶음 검증 설계

- 날짜: 2026-11-08
- 대상: orca-team 0.5.2(`orca-team/`). 선행 설계는 `2026-10-30-orca-team-design.md`(§8 리뷰 루프), `2026-10-31-orca-team-grilling-parallel-design.md`(태스크 그래프와 병렬 시작)다.
- 상태: 사용자가 브레인스토밍 결정과 설계 1–4절(묶음 구조, 테스트 관문, 수렴형 리뷰, 코디네이터 흐름·기존 프로젝트·검증)을 승인했다. 버전은 0.6.0 으로 정했다.

## 1. 목적

실제 프로젝트에서 기능 하나를 끝내는 데 몇 시간씩 걸린다. 대부분의 태스크가 리뷰 3라운드까지 가고, 4라운드 이상으로 넘어가 사용자에게 다시 승인받는 일도 많다. 테스트가 수천 개로 늘어난 뒤에는 그 전체를 기능마다 3–4번씩 다시 돌린다. 시간을 가장 많이 쓰는 것은 테스트 실행과 수정 라운드 반복이고, 테스트 시간은 하나하나 느린 테스트보다 단위·통합 테스트의 수에서 나온다.

세 가지를 함께 바꾼다.
1. 테스트는 태스크마다 관련된 것만 돌리고, 전체 테스트는 묶음마다 한 번 돌린다.
2. 테스트는 Claude·Codex 가 아니라 orch 가 정해진 명령으로 돌린다. 실패하면 Codex 리뷰 전에 돌려보낸다.
3. 리뷰가 라운드마다 수렴한다. 라운드 2부터는 이전 지적과 새 변경만 보고, P1 만 승인을 막는다.

### 1.1 지금 구조에서 시간이 드는 곳

- 전체 테스트를 기능마다 최대 4번 돌린다. 개발 워커(`roles/developer.md` "Acceptance 를 실제로 실행"), 개발 리뷰어(`review-prompt.md` 기준 1 "가능하면 직접 실행"), tester 워커, tester 리뷰어가 각자 돌린다. 테스트 수는 기능 수에 비례해 늘어나므로 기능 N개의 총 시간은 N² 에 가깝게 는다.
- 수정 라운드도 처음부터 다시 리뷰한다. `lib/orch/review.py` `task_commits` 는 라운드와 상관없이 `base..HEAD` 의 작업 커밋 전체를 리뷰 대상으로 준다. 리뷰 기준 3번("버그, 보안, 회귀 위험")은 끝이 없어서 리뷰어가 라운드마다 앞에서 보고 넘어간 코드에서 새 지적을 찾는다.
- 막는 기준이 정해져 있지 않다. `review-prompt.md` 는 "P1 이 하나라도 있으면 REQUEST_CHANGES" 만 정하고, P2·P3 만 있을 때의 판정은 리뷰어에게 맡긴다.
- spec 에 없던 요구(엣지 케이스, 오류 상태)를 리뷰어가 코드 결함으로 지적한다. 구현 범위가 넓어진 것처럼 보이지만 실제로는 기획의 빈칸이다.
- tester 직무의 기본 `owns` 가 `tests/**` 라, tester 태스크마다 그대로 두면 소유 경로가 겹쳐 의존으로 이어야 하고 테스트 태스크가 한 줄로 선다.
- 수정 라운드 상한은 `[review] max_rounds = 3` 이다. 이 설계는 상한을 바꾸지 않고 라운드 수 자체를 줄인다.

## 2. 사용자 결정

| # | 질문 | 결정 |
|---|---|---|
| 1 | 범위 | 수렴형 리뷰, orch 테스트 관문, 묶음 단위 전체 테스트를 0.6.0 에 함께 낸다 |
| 2 | 묶는 대상 | 테스트는 묶음으로 돌린다. 리뷰는 두 층이다: 태스크 리뷰는 남기되 변경분만 가볍게, 묶음 리뷰를 새로 둔다. 리뷰를 묶음 끝 한 번으로만 하지 않는다 |
| 3 | 묶음 정의 | 새 필드 없이 tester 태스크 하나와 그 `depends_on` 의 developer 태스크들 |
| 4 | 테스트 명령 | planner 가 `docs/planning/tasks.toml` 의 `[test]` 에 `quick`(관련 테스트)·`full`(전체)을 쓴다 |
| 5 | 실행 위치 | 지금처럼 같은 체크아웃에서 돌린다. 별도 worktree 를 만들지 않는다 |
| 6 | 막는 기준 | P1 만 막는다. P2·P3 는 `## 후속`, spec 에 없는 요구는 `## 기획 누락` 으로 따로 적고 막지 않는다 |
| 7 | 기존 프로젝트 | `orch upgrade` 로 복사된 규칙 파일을 새 템플릿으로 바꾼다. `[test]` 를 넣기 전까지 관문은 꺼져 있다 |
| 8 | 버전 | 0.6.0 |

## 3. 묶음 (`templates/roles/planner.md`, `lib/orch/plan.py`)

묶음은 함께 끝내고 함께 검증하는 기능 단위다. tasks.toml 에 새 필드를 두지 않는다. tester 태스크 하나가 묶음 하나이고, 그 태스크의 `depends_on` 에 있는 developer 태스크들이 묶음의 기능이다. 묶음 이름은 tester 태스크의 key 다.

### 3.1 planner 규칙

- developer 태스크 3–6개를 한 묶음으로 둔다(권장. 개수는 검사하지 않는다). 묶음 안의 developer 태스크끼리는 의존해도 된다.
- 묶음마다 tester 태스크를 하나 둔다. `depends_on` 에 그 묶음의 developer 태스크를 모두 적고, `owns` 는 `["tests/<key>/**", "docs/qa/<key>.md"]` 로 쓴다(`<key>` 는 그 tester 태스크의 key).
- 다음 묶음의 developer 태스크는 tester 태스크에 의존하지 않는다. 실제로 필요한 developer 태스크에만 의존한다. 그래서 묶음 N 을 테스트하는 동안 묶음 N+1 구현이 돈다.
- 단위 테스트는 developer 가 자기 소유 경로 안(예: `src/**/*.test.ts`)에 쓴다. tester 는 묶음의 통합 테스트와 QA 리포트를 쓴다.
- 테스트 도구를 설치하는 첫 developer 태스크의 `acceptance` 에 "`[test]` 의 quick·full 명령이 실행된다"를 넣는다.

### 3.2 `orch plan check` 추가 검사

`[test]` 가 있을 때만 검사한다. `--append` 면 새 태스크만 본다(적용한 태스크는 바꿀 수 없다).

- 문서 전용이 아닌 developer 태스크는 정확히 하나의 tester 태스크 `depends_on` 에 직접 들어 있다. 문서 전용은 `owns` 가 모두 `README.md` 이거나 `docs/` 아래인 태스크다.
- tester 태스크의 `owns` 는 정확히 `tests/<key>/**` 와 `docs/qa/<key>.md` 다.
- tester 태스크의 `depends_on` 에는 developer 태스크만 있다.
- developer 태스크는 tester 태스크에 의존하지 않는다.

오류 문구는 지금 검사처럼 태스크 key 와 고칠 방향을 적는다.

## 4. 테스트 관문

### 4.1 `[test]` 형식

```toml
[test]
quick = "npx vitest related --run --passWithNoTests {files}"
full = "npx vitest run"
timeout_min = 30      # 선택, 기본 30. 한 번 실행의 상한(분)
ok_exit = [0]         # 선택, 기본 [0]. pytest 는 [0, 5](모은 테스트 없음)
exclusive = false     # 선택, 기본 false. true 면 테스트 실행을 한 번에 하나로 잠근다
```

- `quick` 은 `{files}` 를 정확히 한 번 담는다. `full` 은 담지 않는다. 관련 테스트가 없을 때 성공으로 끝나야 한다(vitest `--passWithNoTests`, pytest 는 `ok_exit = [0, 5]`).
- `exclusive` 는 테스트가 DB·포트·파일을 같이 써서 동시에 돌면 서로 깨지는 프로젝트에서 켠다.
- `orch plan check` 가 형식을 검사한다: 두 명령이 비지 않은 문자열인지, `{files}` 규칙, `timeout_min` 1–240 정수, `ok_exit` 정수 목록, `exclusive` 불리언. 모르는 키는 거부한다.
- `[test]` 는 태스크가 아니므로 증분 기획(`--append`)에서도 고칠 수 있다.
- orch 는 실행할 때마다 커밋된 HEAD 의 `docs/planning/tasks.toml`(`git show HEAD:docs/planning/tasks.toml`)에서 `[test]` 를 읽는다. `[test]` 는 커밋해야 적용된다. 증분 기획 워커가 작업 트리에서 고치는 중인 tasks.toml 은 관문을 바꾸지 않는다. HEAD 에 파일이 없거나(커밋이 없을 때 포함) 표가 없으면 관문이 꺼진다. 커밋된 파일의 TOML·형식 오류는 실행 불가(exit 2)다.
- `orch plan check` 는 지금처럼 작업 트리의 tasks.toml 을 읽는다. 기획 워커가 커밋하기 전에 검사한다.

### 4.2 `orch test` (`lib/orch/testgate.py`, 새 파일)

| 형식 | 하는 일 |
|---|---|
| `orch test T-xxx` | `quick`. `{files}` 는 그 태스크 작업 커밋(`task_commits`)이 바꾼 파일 중 작업 트리에 있는 것이다. `docs/tasks/T-xxx/` 는 뺀다. 각 경로를 셸 인용해 공백으로 잇는다 |
| `orch test T-xxx --full` | `full` |
| `orch test --full` | 태스크 없이 `full`(마지막 확인용) |

- 프로젝트 루트에서 `/bin/sh -c` 로 실행한다. 코디네이터·워커 누구나 실행할 수 있다(코디네이터 확인을 하지 않는다).
- `quick` 은 커밋된 변경만 본다. 커밋 전에 돌린 quick 이 통과로 보이지 않게 다음 경우는 실행하지 않고 exit 2 로 끝낸다.
  - 작업 커밋이 하나도 없다: "아직 `[T-xxx]` 작업 커밋이 없다. 작업을 먼저 커밋한 뒤 orch test 를 돌린다(quick 은 커밋된 변경만 본다)".
  - 소유 경로(리포트 경로 `docs/tasks/T-xxx/` 는 뺀다)에 커밋하지 않은 추적 파일 변경이나 추적되지 않은 새 파일이 있다. 오류 문구에 그 파일 목록을 적는다. 리뷰 관문이 quick 을 돌릴 때도 같다.
- 리포트만 바꾼 커밋뿐이라 `quick` 의 대상 파일이 없으면 실행하지 않고 `skipped` 로 끝낸다(통과로 본다).
- `quick` 이 실패하면 바로 한 번 더 돌린다. 두 번째에 통과하면 `flaky` 다(통과로 보고 리뷰에 "불안정 의심"을 적는다). `full` 은 다시 돌리지 않는다.
- 종료 코드가 `ok_exit` 에 있으면 통과다. `timeout_min` 을 넘기면 프로세스 그룹을 끝내고 `timeout`(실패)이다. 그룹에 SIGTERM 을 보내고 5초까지 기다린 뒤, 남았든 아니든 SIGKILL 을 보낸다(SIGTERM 을 무시하는 브라우저·e2e 도우미가 남지 않게).
- `exclusive = true` 면 `.orchestration/state/tests/.lock` 을 `flock` 으로 잡고 실행한다. `timeout_min` 안에 잠금을 얻지 못하면 실행 불가 오류다.
- 표준 출력은 요약 JSON 이다: `task`, `kind`(quick|full), `status`(passed|flaky|skipped|failed|timeout), `exit`, `seconds`, `head`, `log`, `tail`(출력 마지막 60줄).
- orch 종료 코드: 0 = passed·flaky·skipped, 1 = failed·timeout, 2 = 실행 불가(`[test]` 없음, 명령 종료 코드 126·127, 잠금 대기 초과, quick 인데 작업 커밋이 없거나 커밋 안 된 소유 파일이 있음).

### 4.3 결과 기록

- 태스크 결과는 `.orchestration/state/tests/T-xxx.json` 에 `quick`·`full` 종류별로 마지막 것 하나씩 남긴다: `round`, `head`, `status`, `exit`, `seconds`, `log`, `tail`, `dirty`, `at`.
- `head` 는 리포트(`docs/tasks/T-xxx/`)만 바꾼 커밋을 뺀 태스크의 마지막 `[T-xxx]` 작업 커밋이다. 워커는 테스트를 돌린 뒤 리포트를 커밋하므로, 리포트 커밋이 결과 재사용을 막지 않게 한다. 태스크 없는 실행은 `HEAD` 를 적고 `.orchestration/state/tests/final.json` 에 남긴다.
- `dirty` 는 실행 시점에 이 태스크 소유 경로 밖에서 커밋되지 않은 변경 파일 목록이다. 다른 워커의 작업 중 파일 탓인 실패를 리뷰어가 구분하는 데 쓴다.
- 전체 로그는 `~/.cache/orca-team/runs/<프로젝트>/T-xxx-r<round>-<kind>-<시각>.log` 다(`XDG_CACHE_HOME` 을 따른다. 리뷰 run-dir 과 같은 규칙).

### 4.4 워커 규칙 (`templates/worker-spec.md`)

`[test]` 가 있고 직무가 developer·tester 일 때만 `{test_block}` 을 채운다. 워커 spec 은 플러그인에서 바로 읽으므로 기존 프로젝트에도 바로 적용된다.

- developer: 순서는 작업을 `[T-xxx]` 커밋으로 먼저 커밋 → `.orchestration/bin/orch test T-xxx` → 리포트를 따로 커밋이다. quick 은 커밋된 변경만 보므로 커밋 전에 돌리면 orch 가 거부한다(§4.2). worker_done 전에 quick 이 통과해야 한다. 리포트만 바꾼 커밋은 결과 재사용을 막지 않는다(§4.3). 전체 테스트는 돌리지 않는다. 리포트의 검증 출력에 orch test 요약(status, seconds, 실패했으면 tail)을 붙인다.
- tester: 묶음 통합 테스트를 커밋한 뒤 `.orchestration/bin/orch test T-xxx --full` 을 한 번 돌린다. `full` 은 다시 돌리지 않는다. 실패를 QA 리포트에서 테스트 잘못과 제품 회귀로 나눈다. 테스트 잘못은 고친다(고친 테스트를 커밋한 뒤 `orch test T-xxx`(quick)로 확인할 수 있다). 제품 회귀는 실패한 테스트 이름, 원인으로 보이는 선행 태스크 ID, 근거를 적는다. 테스트를 통과시키려고 테스트를 약하게 만들거나 건너뛰거나 지우지 않는다. 제품 코드는 고치지 않는다. quick 통과는 요구하지 않는다. 묶음 통합 테스트가 제품 회귀를 제대로 잡으면 tester 는 그 테스트를 통과시킬 수 없기 때문이다.

### 4.5 리뷰 관문 (`lib/orch/review.py` `run_review`)

사전 검사 순서: 작업 커밋 없음 → 소유 경로 위반 → 커밋 안 된 소유 파일 → tasks.toml 검증 → **테스트 관문** → Codex 리뷰. 관문은 `[test]` 가 있고 직무가 developer·tester 일 때만 돈다.

- `quick`(developer 만): 같은 `head` 의 통과 기록(passed·flaky·skipped)이 있으면 그것을 쓴다. 없거나 실패 기록이면 orch 가 돌린다.
  - 실패·시간 초과면 Codex 를 부르지 않고 `auto_review` 로 REQUEST_CHANGES 를 쓴다(리뷰 파일에 명령, 종료 코드, tail, 로그 경로). 한 라운드로 센다.
  - 통과면 결과를 리뷰 프롬프트 `{test_section}` 에 넣는다.
- `full`(tester 만. tester 는 quick 을 돌리지 않는다): 같은 라운드의 기록이 있으면 그것을 쓴다. 없으면 orch 가 한 번 돌린다. 실패·시간 초과여도 자동으로 돌려보내지 않고 Codex 를 부른다. 판정은 리뷰 파일을 따른다. 결과를 묶음 리뷰(§5.4)에 넣는다.
  - 묶음 통합 테스트는 제품 회귀를 잡는 것이 일이다. 테스트 결과로 tester 를 자동 반려하면 `## 회귀` 가 쓰이지 않아 회귀 수정 태스크가 생기지 않고, tester 는 테스트를 약하게 만드는 것 말고는 통과시킬 방법이 없다.
  - `full` 뒤에 tester 가 고친 테스트는 리뷰어가 그 테스트 하나만 직접 돌려 확인한다(`{verify_rule}`).
- 실행 불가(종료 코드 2)면 리뷰를 멈춘다. 상태를 `working` 으로 되돌리고(지금의 예외 처리) exit 2 로 끝낸다. 라운드를 쓰지 않는다. 오류 문구: "`[test]` 명령을 실행할 수 없다(<원인>). 기획 태스크로 `docs/planning/tasks.toml` 의 `[test]` 를 고치거나, 사용자가 직접 고친다."

### 4.6 오류 처리 요약

| 경우 | 처리 |
|---|---|
| `quick` 실패(developer 리뷰) | 한 번 더 실행. 또 실패하면 orch REQUEST_CHANGES |
| `quick` 두 번째 통과 | `flaky`. 통과로 보고 리뷰에 "불안정 의심" |
| `full` 실패(tester 리뷰) | 다시 돌리지 않는다. 자동 반려하지 않고 묶음 리뷰(Codex)가 원인을 나눈다 |
| 시간 초과 | 실패로 본다. quick 시간 초과는 다시 돌리지 않는다(긴 대기를 두 번 하지 않는다) |
| `orch test T-xxx`(quick) 인데 작업 커밋이 없거나 소유 경로에 커밋 안 된 변경·새 파일이 있음 | exit 2. 먼저 커밋한 뒤 다시 돌린다(§4.2) |
| 명령 종료 코드 126·127, `[test]` 형식 오류, 잠금 대기 초과 | 실행 불가. 리뷰 중단, exit 2, 라운드 안 씀 |
| tester 리뷰인데 같은 라운드 `full` 기록 없음 | orch 가 한 번 돌린다 |
| `[test]` 없음 | 관문 꺼짐. 리뷰 프롬프트는 지금처럼 "가능하면 직접 실행" |

## 5. 수렴형 리뷰 (`templates/review-prompt.md`, `lib/orch/review.py`)

리뷰 프롬프트는 플러그인에서 바로 읽으므로 이 절의 규칙은 `[test]` 가 없는 기존 프로젝트에도 적용된다.

### 5.1 등급

- P1(막음): 수용 기준 미충족, 재현되는 동작 버그, 보안 문제, 데이터 손실, `contracts.md` 위반, orch 테스트 실패, 디자인·문구 절이 P1 으로 정한 것(즉시 차단 항목, 이 태스크가 만든 §1 위반, text-rules §5 의 P1).
- P2·P3(막지 않음): 고치면 좋은 것, 유지보수, 스타일, 문서. 판정은 APPROVE 이고 `## 후속` 에 적는다.
- 기획 누락(막지 않음): spec·`contracts.md`·Acceptance 에 없는 요구. `## 기획 누락` 에 무엇이 없고 왜 필요한지 적는다. 고치라고 돌려보내지 않는다.
- 판정: P1 이 있으면 REQUEST_CHANGES, 없으면 APPROVE.

수정 라운드 워커 spec(`lib/orch/tasks.py` `REVIEW_LINE`)은 리뷰의 막는 지적(`## 지적 사항` 의 P1, orch 사전 검사 사유)만 고치게 한다. `## 후속`·`## 기획 누락`·`## 회귀` 는 고치지 않는다. 수정 워커가 P2 까지 고치면 새 diff 가 생겨 다음 라운드가 다시 넓어진다.

### 5.2 라운드 1

지금처럼 태스크 전체를 본다. 지적은 이번 라운드에 모두 낸다. 지적마다 `r1-F1` 처럼 라운드와 번호를 붙인다.

### 5.3 라운드 2부터

- 리뷰 기록(`history` 의 review 항목)에 `head`(그 리뷰가 본 태스크 마지막 작업 커밋)를 남긴다.
- 다음 라운드 프롬프트의 `{round_section}` 에 "지난 리뷰(r{N}) 이후 새 커밋" 목록을 따로 준다. 지난 리뷰 항목에 `head` 가 없으면(0.6.0 이전에 시작한 태스크) 목록 없이 전체 커밋만 준다.
- 좁혀 보는 규칙은 앞 라운드에 Codex 리뷰(source codex)가 한 번이라도 있을 때만 쓴다. 앞 라운드가 모두 orch 사전 검사(테스트 실패, 소유 경로 위반 등)였으면 이번 라운드는 §5.2 처럼 전체를 본다. 기준 커밋은 마지막 Codex 리뷰의 `head` 다.
- 리뷰어는 세 가지만 본다.
  1. 지난 P1 마다 해결했는지: `## 이전 지적 확인` 에 `r1-F1: 해결` 또는 `r1-F1: 미해결 — 근거`. 미해결은 이번 라운드 P1 으로 다시 적는다. 다시 적지 않아도 orch 는 미해결 줄을 막는 지적으로 센다(§5.6).
  2. 새 커밋이 새 P1 을 만들었는지.
  3. 지난 라운드 이후 바뀌지 않은 코드에서 새로 찾은 문제: 수용 기준 미충족·보안·데이터 손실만 P1 으로 적고 "r{N}에서 놓침"을 붙인다. 나머지는 `## 후속` 이다.
- 지난 P2·P3 를 고치지 않은 것은 지적하지 않는다.

### 5.4 묶음 리뷰 (tester 태스크)

tester 태스크 리뷰 프롬프트에 `{group_section}` 을 더한다. tester 리뷰에는 quick 이 없으므로 `{test_section}` 은 비고, 이 절이 `full` 결과를 보여 준다. tester 리뷰는 테스트 결과로 자동 반려하지 않는다(§4.5).

- 묶음의 developer 태스크 목록(ID, 제목, 작업 커밋, 바꾼 파일)과 `full` 결과(status, seconds, tail, 로그 경로, dirty).
- 통합 관점으로 본다: 기능끼리의 계약(`contracts.md`) 일관성, 서로 충돌하는 동작, 빠진 연결.
- `full` 실패를 나눈다.
  - 테스트 코드의 잘못(tester 산출물): tester 태스크의 P1.
  - 제품 회귀: `## 회귀` 에 실패한 테스트 이름과 원인으로 보이는 태스크 ID·근거를 적는다. tester 태스크 판정을 막지 않는다. 코디네이터가 `orch followups` 로 모아 회귀 수정 태스크를 만든다(§7).
  - dirty 파일 탓으로 보이는 실패: `## 회귀` 에 "작업 중 파일 의심"으로 적는다.
  - `## 회귀` 항목에도 다른 지적처럼 `rN-Fk` 번호를 이어 붙인다(§6 의 `id`).

### 5.5 리뷰 파일 형식

```markdown
# T-xxx rN 리뷰
## 판정 근거
## 이전 지적 확인
- r1-F1: 해결 (라운드 1이면 "해당 없음")
## 지적 사항
- rN-F1 [P1] 파일:줄 — 문제. 고칠 방향. (없으면 "없음")
## 후속
- rN-F2 [P2] 파일:줄 — 문제. 고칠 방향. (없으면 "없음")
## 기획 누락
- rN-F3 무엇이 spec·contracts·Acceptance 에 없는가. 왜 필요한가. (없으면 "없음")
## 회귀
- rN-F4 실패한 테스트 — 원인으로 보이는 태스크 ID. 근거. (묶음 리뷰만. 아니면 "해당 없음")
## 직접 확인한 것
- 실행한 명령과 결과 요약

VERDICT: APPROVE
```

### 5.6 판정 맞추기

리뷰 파일을 읽은 뒤 orch 가 막는 지적을 센다. `## 지적 사항` 절에서 `[P1]` 이 든 줄(`- `, `* `, `1.`, `###` 등 줄 모양과 상관없다)과 `## 이전 지적 확인` 절에서 `미해결` 이 든 줄이다. REQUEST_CHANGES 인데 그 수가 0이면 APPROVE 로, APPROVE 인데 하나라도 있으면 REQUEST_CHANGES 로 바꾼다. RC→APPROVE 쪽이 위험하다(orch 가 유일한 강제 지점이라 막아야 할 P1 이 그대로 통과한다). 그래서 넓게 센다. P2 문장이 `[P1]` 을 언급해 RC 로 남는 것은 한 라운드를 더 쓸 뿐이다. 바꾼 경우 리뷰 커밋 메시지와 `orch review` 출력, history 항목에 `verdict_adjusted: "<원래>→<바꾼 것>"` 을 남긴다. `## 지적 사항` 절을 찾지 못하면(이전 형식) 바꾸지 않는다.

## 6. 후속 모으기 (`orch followups`, `lib/orch/followups.py`, 새 파일)

| 형식 | 대상 |
|---|---|
| `orch followups T-xxx` | T-xxx 가 tester 태스크면 그 묶음(developer 태스크들과 tester 태스크), 아니면 그 태스크만 |
| `orch followups --rest` | 어떤 묶음에도 들지 않은 done developer 태스크 전부(마지막 정리용) |

- 태스크마다 마지막 라운드 리뷰 파일에서 `## 후속`·`## 기획 누락`·`## 회귀` 의 항목 줄을 모은다. "없음"·"해당 없음" 은 뺀다. 그 뒤에 공백·괄호·구두점과 사유가 붙은 항목(예: `해당 없음(묶음 리뷰 아님)`)도 뺀다.
- 후속 처리로 만든 태스크(spec Change 첫 줄이 `출처: T-xxx followups`, `출처: rest followups`, `출처: final followups` 중 하나)는 `## 후속` 을 모으지 않는다. `## 회귀`·`## 기획 누락` 만 모은다. 정리 태스크가 다시 정리 태스크를 낳아 끝나지 않는 것을 막는다. spec 을 읽지 못하면 후속 처리 태스크가 아닌 것으로 본다(spec 하나가 깨져도 전체가 멈추지 않게).
- planner·director·designer 태스크는 모으지 않는다. 디자인은 사용자 일괄 승인에서 사람이 본다.
- 출력 JSON: `regressions`, `followups`, `planning_gaps`. 항목마다 `task`, `round`, `id`(rN-Fk), `text`.
- 읽기만 한다. 같은 묶음을 다시 실행하면 같은 결과가 나온다.

## 7. 코디네이터 흐름 (`templates/coordinator.md`)

- worker_done 뒤 `orch review T-xxx` 를 백그라운드로 실행하는 것은 그대로다. 관문은 그 안에서 돈다. 리뷰 exit 2 가 `[test]` 탓(명령 실행 불가, `[test]` 검증 실패, tasks.toml 읽기 실패)이면 오류 문구대로 기획 태스크를 만들거나 `orch notify --type question --json -` 로 사용자에게 올린다. 테스트 잠금 대기 초과면 고칠 것 없이 다시 리뷰한다.
- 같은 테스트 실패로 orch 사전 검사(source orch) 반려가 두 라운드 이어지면 `orch fix` 하지 않고 사용자에게 묻는다. 실패가 그 태스크 탓이 아닐 수 있다(소유 경로 밖 코드, 다른 워커의 커밋 안 된 파일). 수정 워커도 실패가 자기 태스크 탓이 아니면 코드를 고치지 않고 근거를 리포트에 적은 뒤 코디네이터에게 묻는다(`review.gate_failure` 끝 줄).
- 묶음 tester 태스크를 accept 하면 `orch followups T-xxx` 를 실행한다.
  - `regressions`: 원인 태스크마다 회귀 수정 developer 태스크를 바로 만든다(`orch task new developer "회귀 수정: <요약>"`). front matter `owns:` 는 원인 태스크와 같게, Acceptance 는 "실패한 테스트 <이름>이 `orch test T-yyy` 에서 통과한다", Change 첫 줄은 `출처: T-xxx followups`. 원인을 알 수 없으면 사용자에게 묻는다.
  - `followups`: 묶음 정리 developer 태스크 하나로 모은다. `owns` 는 지적이 가리키는 파일을 소유한 태스크들의 owns 합집합이다. Change 에 항목 id 와 원문을 옮기고 첫 줄은 `출처: T-xxx followups`.
  - `planning_gaps`: 문서(`docs/planning/`)로 답할 수 있으면 정리 태스크에 넣는다. 제품 결정이 필요하면 항목들을 질문 하나로 묶어 `orch notify --type question --json -` 로 올린다. 답에 따라 증분 기획 태스크를 만들거나 버린다.
- 다시 시작한 코디네이터는 `.orchestration/tasks/` 의 `출처: T-xxx followups` 줄로 이미 처리한 묶음을 확인하고 다시 만들지 않는다.
- 모든 태스크가 done 또는 dropped 면:
  1. `orch followups --rest` 로 묶음 밖 태스크의 후속을 같은 기준으로 처리한다. 만든 태스크의 Change 첫 줄은 `출처: rest followups` 다. 이 단계는 한 번만 한다. `.orchestration/tasks/` 에 `출처: rest followups` 태스크가 이미 있으면 건너뛴다(같은 항목을 다시 만들지 않는다).
  2. `[test]` 가 있으면 `orch test --full` 을 백그라운드로 돌린다. 실패하면 `final.json` 의 tail 로 원인 태스크를 찾아 회귀 수정 태스크를 만들고(Change 첫 줄 `출처: final followups`), 그 태스크가 끝나면 다시 돌린다. exit 2 면 `[test]` 를 고치는 기획 태스크를 만들고(사용자가 직접 넣은 `[test]` 면 사용자에게 묻는다), 고친 뒤 다시 돌린다.
  3. 통과하면 `orch notify --type done` (결과 요약에 태스크별 라운드 수와 마지막 full 결과를 넣는다).
- 최대 라운드 초과 처리, 디자인 일괄 승인, 시스템 추가 제안 흐름은 바꾸지 않는다.

## 8. 기존 프로젝트

### 8.1 `orch upgrade` (`lib/orch/setup.py`)

- 대상: `SCAFFOLD_FILES` 중 `config.toml` 을 뺀 파일(`coordinator.md`, `design-rules.md`, `text-rules.md`, `roles/*.md`, `.orchestration/bin/orch`). `config.toml` 은 사용자 설정이라 건드리지 않는다.
- 파일이 git 에 커밋돼 있고 커밋 안 된 변경이 없을 때만 새 템플릿으로 덮어쓴다. 변경이 있는 파일과 추적되지 않은 파일은 건너뛴다. 없는 파일은 만든다.
- 출력: `updated`, `skipped`(이유와 함께), `created`, 그리고 바꾸거나 만든 파일만의 `git diff --stat` 원문(건너뛴 파일의 사용자 변경은 넣지 않는다. 바뀐 파일이 없으면 빈 문자열). 커밋하지 않는다. 사용자가 diff 를 보고 커밋하거나 `git checkout -- <파일>` 로 되돌린다.
- 코디네이터는 `coordinator.md` 를 시작할 때만 읽는다. upgrade 를 커밋한 뒤 코디네이터를 다시 띄워야 새 흐름을 따른다. 출력 끝에 이 안내를 붙인다.
- 사용자가 직접 실행하거나 메인 세션에 부탁한다. 코디네이터는 실행하지 않는다.
- `CLAUDE.md`·`AGENTS.md` 의 orca-team 절은 0.6.0 에서 바뀌지 않으므로 대상이 아니다.

### 8.2 호환

- `[test]` 가 없으면 테스트 관문, `{test_block}`, 묶음 검사(§3.2)가 모두 꺼진다.
- 리뷰 등급·라운드 규칙(§5)은 모든 프로젝트에 적용된다. 0.6.0 이전에 시작한 태스크의 다음 라운드는 `head` 가 없어 전체 커밋을 받는다.
- 이전 형식 리뷰 파일은 `orch followups` 가 빈 결과로 읽고, §5.6 판정 맞추기도 하지 않는다.

## 9. 바꾸는 문서와 템플릿

| 파일 | 바꿀 것 |
|---|---|
| `templates/review-prompt.md` | §5 등급·판정, `{verify_rule}`(관문이 있으면 "orch 테스트 결과를 믿는다. 전체 테스트를 돌리지 않는다. 의심 가는 테스트만 하나씩 돌린다", 없으면 지금 문장), `{test_section}`, `{round_section}`, `{group_section}`, §5.5 형식 |
| `templates/worker-spec.md` | `{test_block}` |
| `lib/orch/tasks.py` | `REVIEW_LINE`: 수정 워커는 막는 지적만 고친다(§5) |
| `templates/roles/planner.md` | §3.1 묶음 규칙, §4.1 `[test]` 쓰는 법, tasks.toml 예시에 tester 묶음 태스크와 `[test]` 추가 |
| `templates/roles/tester.md` | 묶음 통합 테스트, `orch test --full` 한 번, 실패 분류, `tests/<key>/**`·`docs/qa/<key>.md` |
| `templates/roles/developer.md` | 전체 테스트를 돌리지 않는다(`[test]` 가 있으면), orch test 요약을 리포트에 붙인다 |
| `templates/coordinator.md` | §7 |
| `README.md` | 새 절 "묶음 검증"(`[test]`, 묶음 규칙, 리뷰 등급)과 0.6.0 업그레이드 절차: `orch upgrade` → diff 확인·커밋 → tasks.toml 에 `[test]` 추가 → 코디네이터 다시 띄우기 |
| 기본 설계 §13 | 0.6.0 항목과 한계 |
| `docs/superpowers/2026-11-05-orca-team-status.md` | 0.6.0 행, 검증, 다음 작업 후보 |

## 10. 한계

- 테스트는 다른 워커가 일하는 같은 체크아웃에서 돈다. 커밋되지 않은 다른 파일이 결과에 섞일 수 있다. `dirty` 목록으로 구분만 한다.
- `quick` 의 "관련 테스트"는 테스트 도구의 판단(import 관계)에 기댄다. 동적 import, 설정 파일, 테스트 데이터 변경은 놓칠 수 있고 묶음 `full` 이 잡는다.
- 회귀는 늦으면 한 묶음 뒤에 발견된다.
- 묶음 정리·회귀 수정 태스크의 `owns` 가 진행 중인 다음 묶음 태스크와 겹치면 잠금이 풀릴 때까지 기다린다.
- 회귀의 원인 태스크와 기획 누락 여부는 Codex 판단이다.
- 라운드 2 규칙(§5.3)은 리뷰어 지시다. orch 가 강제하는 것은 판정과 P1 수를 맞추는 것(§5.6)뿐이다.
- 후속 처리 도중 코디네이터가 다시 시작하면 `출처:` 줄을 쓰기 전에 만든 태스크가 중복될 수 있다.
- planner·director·designer 리뷰의 `## 후속`·`## 기획 누락` 은 모으지 않는다. 리뷰 파일에만 남는다.

## 11. 테스트

- `test_plan.py`: `[test]` 형식 검사(키, 타입, `{files}` 규칙, 범위, 모르는 키). 묶음 검사 네 가지와 문서 전용 예외, `--append` 에서 새 태스크만 보는 것. `[test]` 가 없으면 묶음 검사를 하지 않는 것.
- `test_testgate.py`(새): 가짜 명령으로 passed, failed, flaky(첫 실패 뒤 통과), skipped(대상 없음), timeout(프로세스 그룹 종료), 126·127 실행 불가, `ok_exit`, `{files}` 인용(공백·따옴표 경로), 지운 파일 제외, `exclusive` 잠금 대기와 초과, 기록 파일 내용(`head`, `dirty`), 태스크 없는 `--full` 의 `final.json`. 첫 작업 커밋 전 quick 과 커밋 안 된 소유 파일이 있는 quick 의 exit 2, 커밋된 HEAD 의 `[test]` 만 읽는 것(작업 트리만 바꾼 `[test]` 는 무시), SIGTERM 을 무시한 그룹 프로세스의 SIGKILL.
- `test_review.py`:
  - developer 리뷰에서 quick 실패면 Codex 를 부르지 않고 REQUEST_CHANGES(source orch)이고 리뷰 파일에 tail 이 있다.
  - 같은 head 의 통과 기록을 다시 쓰고 명령을 다시 실행하지 않는다.
  - 실행 불가면 exit 2, 상태 working, 라운드 그대로다.
  - tester 리뷰는 quick 을 돌리지 않고 `full` 만 돈다(같은 라운드 기록이면 다시 쓴다). `{group_section}` 과 `full` 결과가 들어가고, full 실패가 자동 판정을 만들지 않는다(Codex 판정을 따른다).
  - 라운드 2 프롬프트에 지난 리뷰 이후 새 커밋만 따로 나온다. `head` 없는 이전 기록이면 전체 목록이다.
  - history 의 review 항목에 `head` 가 남는다.
  - §5.6 판정 맞추기 두 방향과 이전 형식 무시.
  - `[test]` 가 없으면 지금 프롬프트 문장(`가능하면 직접 실행`)이 그대로다.
- `test_followups.py`(새): 묶음 대상 계산, `--rest`, 세 절 파싱, "없음" 제외, 이전 형식 빈 결과.
- `test_setup.py`: `orch upgrade` 덮어쓰기, 변경 있는 파일·추적 안 된 파일 건너뛰기, `config.toml` 제외, 없는 파일 생성, 커밋하지 않는 것.
- `test_tasks.py`: `{test_block}` 이 `[test]` 와 직무 조건에서만 나온다.
- `test_templates.py`: 리뷰 프롬프트 형식 절, coordinator·planner·tester 문구의 핵심 명령(`orch followups`, `orch test --full`, `tests/<key>/**`).
- `test_cli.py`: 새 명령 `test`, `followups`, `upgrade` 인자와 버전 세 곳 일치.

## 12. 버전과 실환경 확인

- 버전: `lib/orch/paths.py`, `.claude-plugin/plugin.json`, 저장소 루트 `.claude-plugin/marketplace.json` 을 0.6.0 으로 함께 올린다.
- 리뷰만 확인(Orca 없이, 상태 문서의 방법): r1 에서 P1 하나와 P2 하나를 내게 한다. r2 에서 P1 만 고치고, 그대로 둔 코드에 새 P2 거리를 미리 심어 둔다. r2 가 `## 이전 지적 확인` 에서 r1-F1 해결을 적고 APPROVE 와 `## 후속` 으로 끝나는지 본다. `[test]` 를 넣고 quick 이 실패하는 커밋으로 Codex 없이 REQUEST_CHANGES 가 나오는지 본다.
- Orca 한 바퀴: vitest 를 쓰는 작은 웹앱을 묶음 2개 × developer 태스크 2–3개로 기획한다. 확인할 것:
  - 묶음 2 구현이 묶음 1 tester 와 겹쳐 도는지
  - `full` 이 묶음마다 한 번만 도는지(`state/tests/*.json` 과 로그)
  - 묶음 1 기능을 깨는 회귀를 묶음 2 태스크에 일부러 넣었을 때 묶음 2 리뷰의 `## 회귀` 와 회귀 수정 태스크가 생기는지
  - 묶음 안 developer 태스크의 버그를 그 묶음 tester 의 통합 테스트가 잡을 때, tester 가 자동 반려되지 않고 tester 리뷰의 `## 회귀` 와 회귀 수정 태스크가 생기는지
  - developer 워커가 첫 커밋 전에 `orch test T-xxx` 를 돌리면 exit 2 로 막히고, 워커가 커밋한 뒤 다시 돌리는지
  - 마지막 `orch test --full` 뒤에 `notify done` 이 오는지
  - 태스크별 라운드 수와 전체 시간을 기록해 설계 문서 §14 에 적는다
- `orch upgrade`: 0.5.2 로 만든 시험 프로젝트에서 실행해 diff 와 건너뛰기를 본다.

## 13. 범위 밖

- 테스트용 별도 worktree(깨끗한 복사본에서 실행).
- 수정 라운드에서 이전 워커를 다시 쓰기(지금처럼 새 워커를 띄운다).
- 리뷰 effort 자동 조정, 위험도에 따른 리뷰 생략.
- orch 가 직접 하는 테스트 영향 분석. 관련 테스트 판단은 테스트 도구에 맡긴다.
- 테스트 도구 자체의 병렬 실행 설정. 프로젝트가 `full` 명령에 직접 넣는다.
