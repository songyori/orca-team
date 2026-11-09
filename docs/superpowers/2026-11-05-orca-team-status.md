# orca-team 현재 상태 (2026-11-09, 0.6.1)

다음 작업을 시작할 때 먼저 읽는 문서다. 세부 결정은 각 설계 문서와 기본 설계 §13 에 있다.

## 버전

| 버전 | 내용 | 문서 |
|---|---|---|
| 0.1.0 | 3계층(메인·코디네이터·직무 워커), `orch` CLI, Codex 리뷰 루프 | `specs/2026-10-30-orca-team-design.md`, `plans/2026-10-30-orca-team.md` |
| 0.2.0 | Codex 래퍼 내장(`orca-team/scripts/codex-run.sh`), 이미지 생성 `/orca-team:image` | 기본 설계 §13 "Codex 래퍼 내장", `docs/codex/` |
| 0.3.0 | 기획 인터뷰(`orch ask-user`), 태스크 그래프 `tasks.toml` 과 병렬 시작(`orch start-ready`), 디자인 일괄 승인(`orch design-review`) | `specs/2026-10-31-orca-team-grilling-parallel-design.md`, `plans/2026-10-31-orca-team-grilling-parallel.md` |
| 0.4.0 | 디자인 디렉터: 레퍼런스 조사 → 톤 시안 3안 → Claude Design Design System, AI 티 금지 목록과 자동 검사, 시스템 추가 제안 보강 | `specs/2026-10-31-orca-team-design-director-design.md`, `plans/2026-10-31-orca-team-design-director.md` |
| 0.4.1 | 사소한 문제 수정: `orch fix --supplement`, director 질문 header "디자인", 예시 앞 "예:" 제거, 자동 검사 정밀도와 속도, 에셋 전용 designer 예외 | 기본 설계 §13 "디자인 디렉터" |
| 0.5.0 | 문구 규칙: 작성·리뷰 공통 text-rules.md, planner 문구 기준 voice.md, Codex 리뷰 문구 절, 추가된 줄만 보는 자동 검사(textlint) | `specs/2026-11-05-orca-team-text-rules-design.md`, `plans/2026-11-05-orca-team-text-rules.md` |
| 0.5.1 | 코디네이터 자동 부활: 메인 세션 훅이 확실히 죽은 코디네이터를 orch up 분리 실행으로 살림(config.toml auto_up) | `specs/2026-11-06-orca-team-auto-up-design.md`, `plans/2026-11-06-orca-team-auto-up.md` |
| 0.5.2 | 디자인 규칙: design-rules.md 를 §0–§7(접근성·색·글자·레이아웃·표면·동작·상태)로 넓힘, Codex 디자인 리뷰를 변경분 분류·지운 줄 읽기·즉시 차단 항목 기준으로, 지운 줄 접근성 신호 자동 검사(designdiff) | `specs/2026-11-06-orca-team-design-rules-design.md`, `plans/2026-11-06-orca-team-design-rules.md` |
| 0.6.0 | 묶음 검증: `[test]` quick·full 을 orch 가 돌리는 테스트 관문(`orch test`), 묶음(tester 태스크) 단위 전체 테스트와 묶음 리뷰, P1 만 막는 수렴형 리뷰와 판정 맞추기, 후속 모으기(`orch followups`), 기존 프로젝트 갱신(`orch upgrade`) | `specs/2026-11-08-orca-team-batch-verify-design.md`, `plans/2026-11-08-orca-team-batch-verify.md` |
| 0.6.1 | Orca 1.4.240 응답에 맞춘다: worker-show 를 --full 로 읽고 간략형도 읽는다(바쁨 판정이 늘 확인 불가였다), 지금 Orca 에 없는 worker-retain 호출을 지운다 | — |

## 검증

- 테스트: `bash orca-team/tests/run.sh`(678개, 2026-11-09 통과). 내장 Codex 래퍼 회귀 테스트까지: `bash orca-team/tests/run.sh --wrapper`.
- 실환경 확인: 0.3.0, 0.4.0, 0.5.0 을 실제 Orca·Codex·Claude Design 으로 한 바퀴씩 돌렸다. 0.5.1 은 코디네이터 부활만, 0.5.2 는 Codex 디자인 리뷰만 실제로 확인했다. 결과는 0.3.0·0.4.0 설계 문서 §12, 0.5.0 설계 문서 §14, 0.5.1 설계 문서 §13, 0.5.2 설계 문서 §13.1 에 있다.
- 실환경 확인 방법:
  1. 버리는 시험 프로젝트 폴더를 만들고 Orca 터미널에서 Claude 세션을 연다.
  2. `/orca-team:init <프로젝트 설명>` 을 실행한다. 기획·디렉터 질문에는 사용자가 직접 답한다.
  3. 끝나면 사용자 승인을 받아 정리한다. 시험 프로젝트의 Orca 터미널을 닫고, 폴더와 `~/.cache/orca-team/refs/<프로젝트>/` 를 휴지통으로 옮긴다. Orca 사이드바의 프로젝트 항목은 CLI 로 지울 수 없어 Orca 화면에서 지운다.
- 리뷰만 확인하는 방법(Orca 없이, 0.5.2 에서 썼다):
  1. 빈 git 저장소에서 이 저장소의 `orca-team/bin/orch scaffold --description-file <파일>` 로 규칙을 설치하고, 기본 앱을 커밋한 뒤 태스크 spec 을 `[T-001] spec: …` 으로 커밋한다.
  2. 워커 대신 `[T-001] …` 작업 커밋과 `docs/tasks/T-001/report.md` 를 직접 만든다(확인할 회귀를 일부러 넣는다).
  3. `.orchestration/state/tasks/T-001.json` 을 `status: working`, `base: <spec 커밋>`, `dispatch_id: null` 로 쓰고, `.orchestration/state/run.json` 에 `coordinator_handle` 을 아무 값으로 둔다.
  4. `ORCA_TERMINAL_HANDLE=<그 값> .orchestration/bin/orch review T-001` 을 실행하면 실제 Codex 리뷰가 돈다. 결과는 `docs/reviews/T-001/r1.md`, 프롬프트는 `~/.cache/orca-team/runs/<프로젝트>/` 에 있다. 끝나면 승인을 받아 두 폴더를 휴지통으로 옮긴다.

## 실환경에서 확인하지 않은 흐름

- 톤 시안 피드백 분기: 세부 수정이면 수정안 1개, 방향 전환이면 새 3안. 확인 때 추천안을 골라 실행되지 않았다(단위 테스트는 있다).
- 기본 권한 모드의 무인 실행. 모든 확인이 bypass permissions 세션이었다.
- 0.5.1 코디네이터 자동 부활을 Orca 재시작 뒤에 하는 경우. 코디네이터 터미널을 닫은 경우만 확인했다.
- 0.5.2 디자인 규칙의 워커 쪽: 실제 워커가 쓰는 `### 디자인 점검`·§7 상태 확인, director 의 §3·§4 반영. Codex 리뷰 판정과 diff 자동 검사는 실환경에서 확인했다(설계 §13.1).
- 0.6.0 묶음 검증 전체: 실제 Codex 의 라운드 2 좁혀 보기와 `## 후속`·`## 기획 누락` 분류, 실제 테스트 도구(vitest)의 quick·full, 묶음 겹쳐 진행, 회귀 수정 태스크, `orch upgrade`. 확인 방법은 설계 §12 다.

## 알려진 사소한 문제 (고치지 않음)

- 0.6.0 리뷰의 추적 안 된 소유 파일 검사는 quick 을 다시 돌릴 때만 한다(같은 head 의 통과 기록을 다시 쓰면 건너뛴다). 추적 중인 파일의 미커밋 변경 검사는 항상 한다.
- 0.6.0 후속 모으기: id 없이 "없음 " 으로 시작하는 진짜 항목(예: "없음 상태 화면이 깨진다")은 빈 항목으로 빠진다. 프롬프트가 모든 항목에 `rN-Fk` id 를 붙이게 해 영향은 작다.
- 0.6.0 판정 맞추기: REQUEST_CHANGES 리뷰의 `## 지적 사항` 에서 P2 문장이 `P1` 을 언급하면 APPROVE 로 내리지 않는다(라운드 하나를 더 쓴다. 안전한 쪽이다).
- 자동 검사(`lib/orch/lint.py`): U+FE0F 가 두 번 겹치면 보이지 않는 발췌로 보고된다. 여러 줄에 걸친 Tailwind className 은 검사하지 않는다.
- `tests/test_lint.py`: 그라디언트 줄 번호 테스트가 `str.count` 호출 0회를 요구한다. 선형 비용보다 엄격하다.
- 코디네이터가 `orch notify --type question` 으로 올린 질문은 직무 정보가 없어 header 기본값이 "기획"이다.
- 0.5.2 디자인 diff 검사(`lib/orch/designdiff.py`): 추가한 줄과 내용이 같은 기존 줄도 함께 잡힌다. `focus-visible:outline-none` 만 있어도 대체 포커스로 본다. CSS `.a:focus:not(...)` 도 대체로 본다. 리뷰마다 파일×커밋 수만큼 `git show` 를 문구 검사와 디자인 검사가 따로 부른다(캐시 없음).
- 0.5.2 지운 줄 검사의 두 단계 판정: 한 파일에서 실제로 줄어든 신호도 같은 태스크가 다른 파일에 같은 신호를 더하면 가려진다(태스크 전체 순감소가 0).
- 0.5.2 추가한 줄 검사: 지운 줄과 내용이 같은 줄을 다른 맥락에 추가하면 추가한 줄로 보지 않는다(예: `outline: none;` 줄).
- 0.5.2 파일 이동: 옮기면서 신호가 있는 줄을 고친 파일은 고친 줄만 남아 그 파일의 줄어든 수가 나온다(옮긴 줄은 양쪽에서 뺀다).
- 0.5.2 파일 이동: 옮긴 두 경로 중 하나만 대상이면(확장자가 대상 밖으로 바뀜 등) 예전처럼 파일별로 판정해 경고가 더 나온다.
- 0.5.2 신호 낱말: `const role = 'admin'`, `const lang = 'ko'`, `function t(lang = 'ko')` 는 아직 role·lang 신호로 센다.
- 0.6.0 quick: 바뀐 파일이 아주 많으면 `{files}` 로 이은 명령이 인자 길이 제한(Linux 는 인자 하나 128 KB, 경로 약 3천 개)에 걸릴 수 있다.
- 0.6.0 quick: `-` 로 시작하는 파일 이름은 셸 인용만 하므로 테스트 도구가 옵션으로 읽을 수 있다.
- 0.6.0 테스트 로그 이름은 초 단위 시각이라 같은 태스크·종류를 1초 안에 두 번 돌리면 로그 파일이 겹친다.
- 0.6.0 종료 코드 126·127 을 `ok_exit` 보다 먼저 보므로 `ok_exit` 에 넣어도 실행 불가로 본다.
- 0.6.0 `{files}` 에 코드가 아닌 파일(md, json, 잠금 파일)도 들어간다. vitest·jest 는 괜찮지만 pytest 는 경로를 거르거나 바꾸는 감싸는 스크립트가 필요하다.
- 0.6.0 같은 태스크의 테스트를 동시에 돌리면(예: 백그라운드 `--full` 과 quick) 기록 파일을 잠그지 않고 읽고 써서 한 종류의 기록을 잃을 수 있다.
- 0.6.0 dropped 된 tester 태스크의 developer 태스크는 묶음에 든 것으로 보여 `orch followups --rest` 에서 빠진다.
- 0.6.0 회귀 수정 태스크와 묶음 정리 태스크가 같은 `출처: T-xxx followups` 형식이라, 회귀 수정 태스크만 만든 뒤 코디네이터가 다시 시작하면 묶음 정리 태스크를 만들지 않고 넘어갈 수 있다.
- 0.6.0 후속이 태스크를 하나도 만들지 않은 묶음(예: 사용자가 기획 누락을 모두 뺐다)은 `출처:` 줄이 없어 코디네이터가 다시 시작한 뒤 다시 처리할 수 있다.
- 그 밖의 한계: 기본 설계 §13 "v0.1 알려진 한계", "0.3.0 알려진 한계".

## 저장소 규칙

- 공개 저장소다. 개인 컴퓨터 경로(홈 디렉터리 절대 경로, 작업 폴더 경로, 임시 폴더 경로), 개인 전역 설정, 외부 조직의 내부 저장소·플러그인 이름을 넣지 않는다. 커밋 전에 확인한다: `git grep -n -I -P '/Users/|/private/tmp|~/Workspace'`.
- macOS 의 `git grep -E` 는 `\b` 를 지원하지 않는다. 단어 경계 검사는 `-P` 로 한다.
- `git grep` 은 추적 중인 파일만 본다. 새 파일은 `git add` 한 뒤, 커밋하기 전에 확인한다.
- Markdown 에서 범위를 `~` 로 쓰지 않는다(`6~9개`). GitHub 에서 두 개가 짝을 이뤄 취소선이 된다. `6–9개` 로 쓰거나 코드 표기 안에 둔다.
- `.superpowers/`(에이전트 작업 기록)는 커밋하지 않는다(`.gitignore`).
- 버전은 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다. `tests/test_cli.py` 가 셋이 같은지 검사한다.

## 다음 작업 후보

- 0.6.0 실환경 확인(설계 §12): 리뷰만 확인 → Orca 한 바퀴 → 실제 사용 프로젝트에 `orch upgrade`. 태스크별 라운드 수와 전체 시간을 설계 §14 에 적는다.
- 0.5.2 디자인 규칙의 워커 쪽 실환경 확인: 기획→디렉터→디자인→구현 한 바퀴에서 워커가 `### 디자인 점검`·§7 상태 확인을 쓰는지, director 가 §3·§4(램프·역할 토큰·크기 단계·한글 줄바꿈)를 시스템에 넣는지 본다. 파일 이동과 `aria-label` 제거 수정 라운드를 함께 넣는다.
- 0.5.1 Orca 재시작 뒤 코디네이터 자동 부활 확인.
- 디자인 diff 검사 정밀도: `const role = 'admin'` 같은 문자열 할당을 신호에서 빼기, 리뷰 한 번에 `git show` 결과 공유(문구·디자인 검사).
- 템플릿에 남은 물결표 범위(`orca-team/templates/roles/director.md`, `roles/planner.md`, `coordinator.md` 의 `6~9`, `2~4` 등)를 en dash 로 정리하기.
- jakubkrehel/skills 의 better-writing 중 문구 규칙에 보탤 것("확인 창보다 되돌리기", "설정은 켜진 상태로 설명", "입력란 안내는 예시로")은 0.5.2 범위 밖으로 남겼다.

## 개발 흐름

브레인스토밍 → 설계(`docs/superpowers/specs/`) → 구현 계획(`docs/superpowers/plans/`) → 태스크마다 서브에이전트 구현과 리뷰 → 브랜치 전체 최종 리뷰 → 실환경 확인 → main 병합. 작업 브랜치에서 하고 main 에 직접 커밋하지 않는다.
