# orca-team 현재 상태 (2026-11-06, 0.5.2)

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

## 검증

- 테스트: `bash orca-team/tests/run.sh`(576개, 2026-11-06 통과). 내장 Codex 래퍼 회귀 테스트까지: `bash orca-team/tests/run.sh --wrapper`.
- 실환경 확인: 0.3.0, 0.4.0, 0.5.0 을 실제 Orca·Codex·Claude Design 으로 한 바퀴씩 돌렸다. 0.5.1 은 코디네이터 부활만, 0.5.2 는 Codex 디자인 리뷰만 실제로 확인했다. 결과는 0.3.0·0.4.0 설계 문서 §12, 0.5.0 설계 문서 §14, 0.5.1 설계 문서 §13, 0.5.2 설계 문서 §13.1 에 있다.
- 실환경 확인 방법:
  1. 버리는 시험 프로젝트 폴더를 만들고 Orca 터미널에서 Claude 세션을 연다.
  2. `/orca-team:init <프로젝트 설명>` 을 실행한다. 기획·디렉터 질문에는 사용자가 직접 답한다.
  3. 끝나면 사용자 승인을 받아 정리한다. 시험 프로젝트의 Orca 터미널을 닫고, 폴더와 `~/.cache/orca-team/refs/<프로젝트>/` 를 휴지통으로 옮긴다. Orca 사이드바의 프로젝트 항목은 CLI 로 지울 수 없어 Orca 화면에서 지운다.

## 실환경에서 확인하지 않은 흐름

- 톤 시안 피드백 분기: 세부 수정이면 수정안 1개, 방향 전환이면 새 3안. 확인 때 추천안을 골라 실행되지 않았다(단위 테스트는 있다).
- 기본 권한 모드의 무인 실행. 모든 확인이 bypass permissions 세션이었다.
- 0.5.2 디자인 규칙의 워커 쪽: 실제 워커가 쓰는 `### 디자인 점검`·§7 상태 확인, director 의 §3·§4 반영. Codex 리뷰 판정과 diff 자동 검사는 실환경에서 확인했다(설계 §13.1).

## 알려진 사소한 문제 (고치지 않음)

- 자동 검사(`lib/orch/lint.py`): U+FE0F 가 두 번 겹치면 보이지 않는 발췌로 보고된다. 여러 줄에 걸친 Tailwind className 은 검사하지 않는다.
- `tests/test_lint.py`: 그라디언트 줄 번호 테스트가 `str.count` 호출 0회를 요구한다. 선형 비용보다 엄격하다.
- 코디네이터가 `orch notify --type question` 으로 올린 질문은 직무 정보가 없어 header 기본값이 "기획"이다.
- 0.5.2 디자인 diff 검사(`lib/orch/designdiff.py`): 추가한 줄과 내용이 같은 기존 줄도 함께 잡힌다. `focus-visible:outline-none` 만 있어도 대체 포커스로 본다. CSS `.a:focus:not(...)` 도 대체로 본다. 리뷰마다 파일×커밋 수만큼 `git show` 를 문구 검사와 디자인 검사가 따로 부른다(캐시 없음).
- 0.5.2 지운 줄 검사의 두 단계 판정: 한 파일에서 실제로 줄어든 신호도 같은 태스크가 다른 파일에 같은 신호를 더하면 가려진다(태스크 전체 순감소가 0).
- 0.5.2 추가한 줄 검사: 지운 줄과 내용이 같은 줄을 다른 맥락에 추가하면 추가한 줄로 보지 않는다(예: `outline: none;` 줄).
- 0.5.2 파일 이동: 옮기면서 신호가 있는 줄을 고친 파일은 고친 줄만 남아 그 파일의 줄어든 수가 나온다(옮긴 줄은 양쪽에서 뺀다).
- 0.5.2 파일 이동: 옮긴 두 경로 중 하나만 대상이면(확장자가 대상 밖으로 바뀜 등) 예전처럼 파일별로 판정해 경고가 더 나온다.
- 0.5.2 신호 낱말: `const role = 'admin'`, `const lang = 'ko'`, `function t(lang = 'ko')` 는 아직 role·lang 신호로 센다.
- 그 밖의 한계: 기본 설계 §13 "v0.1 알려진 한계", "0.3.0 알려진 한계".

## 저장소 규칙

- 공개 저장소다. 개인 컴퓨터 경로(홈 디렉터리 절대 경로, 작업 폴더 경로, 임시 폴더 경로), 개인 전역 설정, 외부 조직의 내부 저장소·플러그인 이름을 넣지 않는다. 커밋 전에 확인한다: `git grep -n -I -P '/Users/|/private/tmp|~/Workspace'`.
- macOS 의 `git grep -E` 는 `\b` 를 지원하지 않는다. 단어 경계 검사는 `-P` 로 한다.
- Markdown 에서 범위를 `~` 로 쓰지 않는다(`6~9개`). GitHub 에서 두 개가 짝을 이뤄 취소선이 된다. `6–9개` 로 쓰거나 코드 표기 안에 둔다.
- `.superpowers/`(에이전트 작업 기록)는 커밋하지 않는다(`.gitignore`).
- 버전은 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다. `tests/test_cli.py` 가 셋이 같은지 검사한다.

## 개발 흐름

브레인스토밍 → 설계(`docs/superpowers/specs/`) → 구현 계획(`docs/superpowers/plans/`) → 태스크마다 서브에이전트 구현과 리뷰 → 브랜치 전체 최종 리뷰 → 실환경 확인 → main 병합. 작업 브랜치에서 하고 main 에 직접 커밋하지 않는다.
