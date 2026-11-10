# setup-with-orca

새 개발 프로젝트를 Orca 오케스트레이션으로 시작하는 Claude Code 플러그인 마켓플레이스다. 지금은 `orca-team` 플러그인 하나가 있다.

## orca-team

`/orca-team:init <프로젝트 설명>` 한 번으로 다음을 세팅한다.

- 구조: 메인(사용자와 대화하고 `decide` 를 판단) / 관제("orca-team 관제" 탭의 셸에서 도는 `orch drive`, 절차를 돌리고 Run 소유) / 고정 에이전트 3개(생산·조사·테스트) / 임시 워커(바쁠 때) / Codex 리뷰(리뷰마다 새 실행). 자세한 것은 "고정 에이전트 (0.7.0)"
- 직무별 Claude 모델·effort, Codex 리뷰 모델·reasoning(`.orchestration/config.toml`)
- 워커 결과물 → Codex 리뷰(내장 Codex 래퍼, yolo, 훅 신뢰 자동) → APPROVE / REQUEST_CHANGES 루프(최대 3라운드)
- 디자인: 앱·웹 화면은 Claude Design, 래스터 에셋만 Codex `/orca-team:image`
- 메인이 Run 에 묶이지 않으므로 Orca 알림이 사용자 입력란에 들어오지 않는다

배치(`--worktree current`), 모델, 리뷰 절차는 `orch` CLI 에 고정돼 있어 프로젝트마다 동작이 같다.

## 요구사항

- Orca 1.4 이상(실행 중), Python 3.11 이상
- Codex CLI 로그인(`codex login status` 가 `Logged in`). Codex 리뷰와 이미지 생성은 플러그인에 들어 있는 Codex 래퍼(`orca-team/scripts/codex-run.sh`)로 돌므로 다른 플러그인이 필요 없다.
- 이미지 생성(`/orca-team:image`)을 쓰려면 `codex features list` 의 `image_generation` 이 `true` 여야 한다(꺼져 있으면 `codex features enable image_generation`). `orch doctor` 가 경고로 알린다.

## 설치

Claude Code 에서 GitHub 저장소 [songyori/orca-team](https://github.com/songyori/orca-team) 를 마켓플레이스로 추가하고 플러그인을 설치한다. 마켓플레이스 이름은 `setup-with-orca` 다.

```
/plugin marketplace add songyori/orca-team
/plugin install orca-team@setup-with-orca
```

갱신: `/plugin marketplace update setup-with-orca` 다음 `/plugin update orca-team@setup-with-orca`.

## 사용

Orca 터미널에서 새 프로젝트 폴더의 Claude 세션을 연다.

```
/orca-team:init 이 프로젝트는 XX야. XXX하는 프로젝트야.
```

이후에는 메인에게 평소처럼 요청한다. 세션을 다시 열면 `/orca-team:up`.

## 그릴링 기획과 병렬 실행

- 요청을 받으면 메인이 기획(planner) 태스크부터 띄운다. 기획 워커는 `orch ask-user` 로 메인을 거쳐 사용자를 직접 인터뷰한다. 질문은 한 번에 하나이고, 예시·추천 선택지·추천 사유가 붙는다. 메인은 질문의 `render` 를 그대로 AskUserQuestion 에 넘기고(긴 요약 `render.context` 가 있으면 먼저 평문으로 그대로 보여 준다) 답을 `orch answer <id> "<답>"` 으로 돌려준다. 답을 9분 안에 받지 못하면 `ask-user` 는 exit 4 로 끝나고 기획 워커가 `--wait` 로 이어서 기다린다.
- 메인이 `render.context` 를 출력하지 않고 그 질문을 AskUserQuestion 으로 부르면 훅이 context 를 담아 한 번 거부한다. 메인 transcript 끝부분의 assistant 텍스트에서 context 앞부분(공백·마크다운 기호를 뺀 80자)을 찾는다. 같은 질문의 두 번째 호출과 판단할 수 없는 경우(transcript 없음·깨짐)는 통과시킨다.
- 기획 워커는 마지막에 확정 질문을 하고 `docs/planning/` 에 spec.md(기능), contracts.md(인터페이스 약속), tasks.toml(태스크 그래프), interview.md(질문과 답)를 커밋한다. 확정 질문 전에 `orch plan check [--file <경로>]` 로 tasks.toml 을 검사한다. `plan apply` 와 같은 규칙이고 아무것도 쓰지 않는다(누구나 실행할 수 있다).
- 기획 태스크를 accept 하면 관제가 `orch plan apply` 로 tasks.toml 을 검증해 태스크 spec 과 `pending` 상태를 만든다(증분 기획은 `--append`). `orch start-ready` 는 선행 태스크가 모두 done 이고 소유 경로가 겹치지 않는 태스크를 `max_parallel`(기본 4)까지 한 번에 띄운다. `max_parallel` 은 `orch start-ready` 만 지킨다. `orch fix`(수정 라운드)와 단건 `orch start` 는 상한을 세지 않고 바로 띄우므로 working 이 잠시 상한을 넘을 수 있다.
- `user_approval_roles`(새 프로젝트는 designer) 태스크는 Codex APPROVE 뒤 `awaiting_user` 로 기다린다. 모두 모이면 관제가 `orch design-review` 로 디자인 링크를 모은 승인 질문 하나를 메인에 올린다. 승인하면 done, 수정을 요청하면 새 라운드다. 승인 대상 직무끼리 이어진 태스크(디자인 시스템 → 화면)는 선행이 `awaiting_user` 여도 시작해 한 번의 승인 질문에 함께 들어간다. 승인 대상이 아닌 후속 태스크(개발)는 사용자 승인(done)을 기다린다.
- 메인이 질문에 답하기 전에 세션이 끝나도 질문은 남는다. `orch status` 의 `open_questions` 가 답하지 않은 질문을 보여 주고, `/orca-team:up` 이 `orch reask` 로 다시 받아 묻는다. `orch answer` 는 ask 와 decide 에 한 번만 답한다(두 번째는 exit 3). 디자인 승인 질문은 `orch design-decide` 로 답한다.
- 설정은 `.orchestration/config.toml` 최상위 `max_parallel = 4`, `user_approval_roles = ["designer"]` 다. 새 프로젝트는 scaffold 가 이 두 줄을 적는다. `user_approval_roles` 에는 `[roles]` 에 있는 직무만 쓴다(없는 직무는 exit 2).
- 0.7.0 부터는 `orch upgrade`("0.6.0 이하 프로젝트를 올리기" 참고)가 아래 수동 복사 단계를 대신한다. `coordinator.md` 는 더 이상 없다. 아래 버전별 안내는 그 버전 당시의 기록이다.
- 0.2.0 으로 만든 프로젝트는 업그레이드해도 예전 흐름 그대로 돈다. `user_approval_roles` 키가 없으면 `[]`(사용자 승인 게이트 없음)이라 디자인도 Codex APPROVE 뒤 `orch accept` 하면 바로 done 이다. config.toml 을 아예 읽지 못할 때(파일 없음·TOML 오류) `orch accept` 도 같은 기본값 `[]` 을 쓴다. 읽히는데 `user_approval_roles` 가 틀리면 `orch accept` 는 승인 게이트를 건너뛰지 않고 exit 2 로 거부한다. 새 흐름(기획 인터뷰, 태스크 그래프, 디자인 일괄 승인)을 쓰려면 다음을 한다.
  1. `.orchestration/config.toml` 최상위(첫 `[표]` 위)에 `user_approval_roles = ["designer"]` 를 넣는다. `max_parallel` 은 없으면 4 다.
  2. scaffold 는 있는 파일을 덮어쓰지 않는다. `.orchestration/coordinator.md` 와 `.orchestration/roles/planner.md` 를 이 저장소 `orca-team/templates/` 의 `coordinator.md`, `roles/planner.md` 로 바꾸고, `CLAUDE.md` 의 `<!-- orca-team -->` 절을 `orca-team/templates/CLAUDE.section.md` 로 바꾼다.
  3. 코디네이터는 시작할 때 지침을 읽는다. 코디네이터 터미널을 닫고 메인에서 `/orca-team:up` 으로 다시 띄운다.

## 디자인 디렉터

- 기획이 확정되면 디자인 디렉터(director) 태스크가 화면 디자인보다 먼저 돈다. director 는 기획 워커처럼 `orch ask-user` 로 메인을 거쳐 사용자에게 직접 묻는다.
  1. 레퍼런스: 같은 분야·인접 분야 상용 서비스 6–9개를 조사하고 Orca 내장 브라우저로 실제 화면을 캡처한다(저장소 밖 `~/.cache/orca-team/refs/<프로젝트>/`). Claude Design 레퍼런스 보드에 스타일 묶음 3–4개로 정리하고 사용자가 묶음을 고른다.
  2. 톤 시안: 시안 화면 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면)를 톤 3가지로 그린 9장을 보여 준다. 세부 수정 피드백이면 수정안 1개, 방향을 바꾸는 피드백이면 새 3안으로 확정할 때까지 반복한다.
  3. 디자인 시스템: 확정한 톤으로 Claude Design Design System 문서(토큰, 상태별 컴포넌트 미리보기, 사용 규칙)를 만들어 확인받는다. 원본은 `docs/design/system/`, URL 은 `docs/design/direction.md` 의 `design_system_url:` 줄이다.
- 화면 디자이너는 이 프로젝트 디자인 시스템만 쓴다. 시스템에 없는 값이 필요하면 리포트에 "시스템 추가 제안"을 적고, 관제가 디자인 일괄 승인 전에 모아 director 보강 태스크로 처리한다. 개발은 `tokens.json` 을 CSS 변수·테마로 옮긴 UI 기반 태스크의 토큰과 공통 컴포넌트만 쓴다.
- 디자인 규칙 `.orchestration/design-rules.md` 를 director·designer·developer 가 지키고 리포트 `### 디자인 점검` 에 점검 결과를 적는다. §1 은 AI 티 금지 목록(고정 이모지, 왼쪽 줄 카드, 파랑·보라 그라디언트 등 10개), §2–§7 은 접근성(WCAG 2.2 AA), 색, 글자(한글 어절 줄바꿈 포함), 레이아웃, 표면·동작, 상태 규칙이다. 품질 규칙은 [jakubkrehel/skills](https://github.com/jakubkrehel/skills)(MIT)에서 필요한 것만 가져와 압축하고 한국어·앱 플랫폼 내용을 더했다. developer 는 구현한 상태(빈 상태·로딩·오류·좁은 폭 등)를 실제로 렌더해 확인한다.
- Codex 디자인 리뷰는 이 태스크가 만든 문제만 `(새로 생김)`·`(회귀)`·`(기존)` 으로 나눠 지적하고, diff 의 지운 줄을 읽어 회귀를 찾는다. 접근 가능한 이름이 없는 컨트롤, 포커스 표시 없음, 동작 줄이기 무시, 320px·200% 확대에서 잘림, 대비 미달 같은 즉시 차단 항목은 P1 이다. 고칠 방향은 지우기 → 플랫폼 기본 → 기존 토큰 재사용 → 값 교정 → 새로 추가 순으로 고른다.
- `orch review` 는 Codex 를 부르기 전에 바뀐 파일에서 이모지·왼쪽 줄 강조·파랑·보라 그라디언트 의심 위치를, 태스크 diff 에서 지운 줄로 줄어든 접근성 신호(`aria-label`, 포커스, `prefers-reduced-motion`, `keep-all` 등)와 추가한 줄의 확대 막기·포커스 표시 제거를 찾아 리뷰 프롬프트에 경고로 넣는다(자동 불합격은 아니다).
- config.toml 에 `[roles.director]` 가 있으면 `orch plan check` 가 director 태스크 정확히 1개와, 모든 designer 태스크의 director 의존(직접 또는 간접)을 요구한다. `--append` 는 새로 더하는 designer 만 본다. owns 가 모두 `assets/` 아래인 에셋 전용 designer 태스크는 이 규칙에서 빠진다.
- 0.3.0 으로 만든 프로젝트는 config.toml 에 `[roles.director]` 가 없어 지금 흐름 그대로 돈다(리뷰 프롬프트의 디자인 기준과 orch 자동 검사도 `[roles.director]` 를 넣은 뒤에만 붙는다). 새 흐름을 쓰려면 다음을 한다.
  1. `.orchestration/config.toml` 에 `orca-team/templates/config.toml` 의 `[roles.director]` 블록을 복사해 넣는다.
  2. `orca-team/templates/roles/director.md` 를 `.orchestration/roles/director.md` 로, `orca-team/templates/design-rules.md` 를 `.orchestration/design-rules.md` 로 복사한다.
  3. `.orchestration/roles/planner.md`, `designer.md`, `developer.md` 와 `.orchestration/coordinator.md` 를 `orca-team/templates/` 의 같은 파일로 바꾸고, `CLAUDE.md` 의 `<!-- orca-team -->` 절을 `orca-team/templates/CLAUDE.section.md` 로 바꾼다. 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
  4. 새 흐름은 경로가 바뀐다: 화면 디자인 `docs/design/<화면>/` → `docs/design/screens/<화면>/`, `docs/design/system.md` → `docs/design/system/`(director 가 만든다). 기존 파일은 그대로 두고 새 태스크부터 새 경로를 쓴다.
- 0.5.1 이하로 만든 프로젝트도(`[roles.director]` 가 있는 0.4.0 이후 프로젝트) 플러그인을 업데이트하면 새 리뷰 방식과 자동 검사가 바로 붙는다(리포트의 이전 소절 이름 `### 금지 목록 점검` 도 받는다). 워커가 새 규칙으로 작업하게 하려면 `orca-team/templates/design-rules.md` 를 `.orchestration/design-rules.md` 로, `orca-team/templates/roles/director.md`·`designer.md`·`developer.md` 를 `.orchestration/roles/` 로 복사한다(프로젝트에서 직무 문서를 고쳤다면 비교해 합친다). 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
- 설계: `docs/superpowers/specs/2026-11-06-orca-team-design-rules-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-06-orca-team-design-rules.md`.

## 문구 규칙

- 서비스 내 문구(UI 라벨·버튼·오류·알림), 정책·법률 문서(`docs/policy/`), 사용자용 문서(`README.md`, `docs/user/`)의 한국어 텍스트를 규칙 하나(`.orchestration/text-rules.md`)로 쓰고 리뷰한다. 문구 파일은 웹(`.tsx`·`.json`·`.html` 등), Flutter(`.arb`·`.dart`), iOS(`.strings`·`.xcstrings`·`.swift`), Android(`.xml`·`.kt`)를 지원한다. 규칙은 공개 한국어 윤문 스킬 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)(서법 보존, 실측 AI 신호, 과교정 방지)와 [yoonmoon](https://github.com/amondnet/yoonmoon)(교정·문체 체계, AI 티 분류)에서 개발 텍스트에 필요한 것만 가져와 압축하고(둘 다 MIT), UI 라벨·오류 메시지·i18n 변수 규칙을 더했다.
- planner 가 인터뷰 점검표 "문구 기준"으로 서비스 문체(기본 해요체), 정책 문서 문체(기본 하십시오체), 호칭, 라벨 형태, 용어집을 정해 `docs/planning/voice.md` 에 남긴다. 용어집은 확정 질문 요약에서 함께 승인받는다.
- designer·developer 는 문구를 쓰거나 바꾸면 이 규칙과 voice.md 를 지키고 리포트에 `### 문구 점검` 을 적는다. 정책·사용자 문서는 developer 가 맡는다(기본 owns 에 `README.md`, `docs/user/**`, `docs/policy/**`).
- `orch review` 는 태스크가 한글이 든 서비스 문구·문서 파일을 바꿨으면 리뷰 프롬프트에 "문구 기준" 절을 붙인다. Codex 는 text-rules §5 심각도로 판정하고(정책 문서의 의무·권한 표현 변경, i18n 변수 손상, 사용자에게 보이는 확정 맞춤법 오류는 P1), 이 태스크가 바꾼 텍스트만 지적한다. orch 는 그 전에 태스크가 추가한 줄에서 확정 오탈자, 이중피동, "에 있어서", 상투어, 메타 hedging, 챗봇 잔재, 접속어·연결어미 뒤 쉼표, voice.md 금지 표기, 높임법 섞임의 의심 위치를 찾아 넣는다(자동 불합격은 아니다).
- 0.4.x 로 만든 프로젝트는 `.orchestration/text-rules.md` 가 없어 지금 흐름 그대로 돈다. 새 흐름을 쓰려면 다음을 한다.
  1. `orca-team/templates/text-rules.md` 를 `.orchestration/text-rules.md` 로 복사한다. 이것만으로 Codex 리뷰에 문구 기준 절이 붙는다.
  2. 워커가 작성에도 쓰게 하려면 `.orchestration/roles/planner.md`, `designer.md`, `developer.md` 와 `.orchestration/coordinator.md` 를 `orca-team/templates/` 의 같은 파일로 바꾸고, `CLAUDE.md` 의 `<!-- orca-team -->` 절을 `orca-team/templates/CLAUDE.section.md` 로 바꾼다. 정책·사용자 문서를 developer 가 맡게 하려면 `.orchestration/config.toml` 의 `[roles.developer]` owns 에 `"README.md", "docs/user/**", "docs/policy/**"` 를 더한다. 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
  3. voice.md 는 다음 기획(증분 기획 포함)에서 planner 가 만든다. 없는 동안은 용어집 검사를 건너뛴다.
- 플러그인을 업데이트한 뒤에는 `orca-team/templates/text-rules.md` 를 `.orchestration/text-rules.md` 로 다시 복사해야 새 규칙이 적용된다(scaffold 는 있는 파일을 덮어쓰지 않는다).
- 설계: `docs/superpowers/specs/2026-11-05-orca-team-text-rules-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-05-orca-team-text-rules.md`.

## 묶음 검증

- 0.6.0 부터 테스트와 리뷰를 기능마다 무겁게 반복하지 않고 묶음으로 나눈다.
- planner 가 `docs/planning/tasks.toml` 에 `[test]` 를 쓴다. `quick` 은 바뀐 파일과 관련된 테스트(`{files}` 자리에 파일 목록), `full` 은 전체 테스트다. 선택 키: `timeout_min`(기본 30), `ok_exit`(기본 `[0]`), `exclusive`(기본 false. 테스트가 DB·포트를 같이 쓰면 true). orch 는 커밋된 HEAD 의 tasks.toml 에서 `[test]` 를 읽으므로 `[test]` 는 커밋해야 적용된다.
- 묶음: 함께 끝내는 developer 태스크 3–6개와 그것들에 의존하는 tester 태스크 하나. tester 태스크가 묶음 통합 테스트를 쓰고 `orch test T-xxx --full` 로 전체 테스트를 한 번 돌린다. 다음 묶음 구현은 앞 묶음 테스트를 기다리지 않는다.
- developer 워커는 작업을 커밋한 뒤 `orch test T-xxx`(quick)를 돌리고 worker_done 전에 통과시킨다(커밋 전에 돌리면 orch 가 거부한다). 테스트가 소유 경로에 만드는 산출물(`__pycache__`, 스냅숏 등)은 `.gitignore` 에 넣는다. 무시되지 않은 새 파일이 있으면 orch test 가 거부한다. `orch review` 는 developer 태스크의 Codex 리뷰 전에 quick 을 확인하고(같은 코드면 워커 결과를 다시 쓴다), 실패하면 Codex 없이 REQUEST_CHANGES 를 낸다.
- tester 워커는 묶음 통합 테스트를 커밋한 뒤 `orch test T-xxx --full` 을 한 번 돌리고, 실패를 테스트 코드의 잘못(고친다)과 제품 회귀(QA 리포트에 적는다)로 나눈다. 테스트를 약하게 만들거나 제품 코드를 고치지 않는다. tester 리뷰는 그 full 결과를 묶음 리뷰에 넣고 테스트 결과로 자동 반려하지 않는다. 제품 회귀는 리뷰의 `## 회귀` 를 거쳐 회귀 수정 태스크가 된다. 리뷰어는 전체 테스트를 다시 돌리지 않는다.
- 리뷰 등급: P1(수용 기준 미충족, 동작 버그, 보안, 데이터 손실, 계약 위반, 테스트 실패)만 승인을 막는다. P2·P3 는 `## 후속`, spec 에 없는 요구는 `## 기획 누락` 에 적는다. 라운드 2부터는 이전 지적과 새 커밋만 본다. orch 는 `## 지적 사항` 의 P1 줄과 `## 이전 지적 확인` 의 미해결 줄 수로 판정을 맞춘다. 수정 워커는 P1 만 고친다.
- 관제는 묶음 tester 태스크를 accept 하면 `orch followups T-xxx` 로 회귀·후속·기획 누락을 모아 회귀 수정 태스크, 묶음 정리 태스크, 사용자 질문을 만든다. 모든 태스크가 끝나면 `orch test --full` 을 한 번 더 돌린 뒤 완료를 알린다.
- `[test]` 가 없는 프로젝트는 테스트 관문과 묶음 검사가 꺼진다. 리뷰 등급·라운드 규칙은 모든 프로젝트에 적용된다.
- 0.5.x 로 만든 프로젝트에 적용하기:
  1. 플러그인을 0.6.0 으로 업데이트한다.
  2. 프로젝트 루트에서 `.orchestration/bin/orch upgrade` 를 실행한다. 복사된 규칙 파일(`coordinator.md`, `roles/*.md`, `design-rules.md`, `text-rules.md`, `bin/orch`)을 새 템플릿으로 바꾸고 커밋하지 않는다. `config.toml` 은 건드리지 않고, 커밋 안 된 변경이 있는 파일은 건너뛴다.
  3. `git diff -- .orchestration` 으로 확인하고 커밋한다.
  4. `docs/planning/tasks.toml` 에 `[test]` 를 넣고 커밋한다. 직접 쓰거나 메인에 "테스트 명령을 정해 달라"고 증분 기획을 요청한다.
  5. 코디네이터 터미널을 닫고 메인에서 `/orca-team:up` 으로 다시 띄운다.
- 설계: `docs/superpowers/specs/2026-11-08-orca-team-batch-verify-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-08-orca-team-batch-verify.md`.

## 고정 에이전트 (0.7.0)

- 생산(planner·designer·developer), 조사(director), 테스트(tester) 에이전트가 각자 Orca 탭 하나에 뜬다. 왼쪽은 작업 세션, 오른쪽은 그 에이전트 태스크의 Codex 리뷰 진행(`orch review-view`)이다.
- 같은 세션이 여러 태스크와 수정 라운드를 이어 맡는다. 묶음(tester 태스크)이 끝나면 `docs/handoff/<에이전트>.md` 에 인수인계를 쓰고 세션을 비운다. 직접 비우려면 `orch agent reset <에이전트>`.
- 에이전트가 바쁠 때 준비된 독립 태스크는 임시 워커로 돈다(`[overflow] enabled`).
- 코디네이터는 없다. 관제(`orch drive`, "orca-team 관제" 탭)가 절차를 돌리고 판단할 일만 메인에 decide 로 올린다.

### 0.6.0 이하 프로젝트를 올리기
0. 플러그인을 올리기 전에 진행 중인 태스크(working·reviewing)를 끝낸다. 이미 플러그인을 올렸다면 옛 코디네이터의 `orch` 명령이 설정 오류로 실패해 태스크를 끝낼 수 없다. 그때는 코디네이터 탭을 닫고 `.orchestration/bin/orch upgrade --force` 를 쓴다(진행 중 태스크 거부만 건너뛴다. 코디네이터가 떠 있으면 `--force` 도 거부한다. `run_id` 는 그대로라 새 관제가 같은 Run 을 이어받아 진행 중인 태스크를 다시 살핀다).
1. 코디네이터 탭을 닫는다.
2. 메인에서 `.orchestration/bin/orch upgrade` 를 실행하고 출력의 바뀐 파일을 확인한다. `config` 가 `skipped` 면 `.orchestration/config.toml` 을 출력 설명대로 직접 고치거나 커밋한 뒤 다시 실행한다.
3. `.orchestration/` 과 `CLAUDE.md` 를 경로 지정 커밋한다.
4. 메인 세션을 새로 연다(`/clear` 또는 새 세션). 새 `CLAUDE.md` 규칙은 세션을 시작할 때만 읽으므로, 이전 세션은 옛 코디네이터 규칙으로 동작한다.
5. 새 세션에서 `/orca-team:up` 으로 관제를 띄운다.

## 보안 주의

Codex 리뷰는 내장 Codex 래퍼의 bypass 모드(`--profile run`: 샌드박스·승인 없음, `--bypass-hook-trust`: 저장소 Codex 훅 자동 신뢰)로 돈다. `/orca-team:image` 도 같은 래퍼로 샌드박스·승인 없이 돈다(`--profile image`, 훅 신뢰 자동 없음). Codex 는 워커가 만든 코드를 읽고 그 테스트·빌드를 실행하며, 사용자의 자격 증명(셸 환경 변수, git·클라우드 인증, Codex 로그인)을 그대로 물려받는다. 신뢰할 수 있는 저장소에서만 쓴다.

## 권한 설정

관제와 고정 에이전트·임시 워커는 사람 없이 돈다. 이 세션들의 도구 권한이 프로젝트의 `.orchestration/bin/orch`, `git`, `orca` 실행을 허용해야 한다. 허용되지 않으면 권한 프롬프트에서 멈춰 Run 이 정지한다. bypass permissions 로 돌리거나, 이 명령들을 허용 목록(allowlist)에 넣는다. 실환경 검증은 모든 세션이 bypass permissions 였던 환경에서만 했고, 기본 권한 모드의 무인 실행은 검증하지 못했다.

## 복구

- `.orchestration/bin/orch status` 는 태스크마다 다음 행동 `next` 를 보여 준다. `next` 는 메인이 읽는 안내 문장이다. 재시작한 관제(`orch drive`)는 `next` 를 따르지 않고 태스크마다 `reconcile_task` 로 상태를 다시 맞춘다. 아래 표의 "관제가 …" 는 관제가 스스로 하는 일이고, 메인은 `start`·`fix`·`accept`·`drop`·`design-decide`·`scope` 처럼 큐를 거치는 명령과 `answer`·`status` 만 쓴다.

  | status | next |
  |---|---|
  | `pending` | 선행 태스크가 dropped 면 `orch drop T-xxx --reason "<이유>"` 또는 `depends_on` 을 고쳐 커밋한 뒤 `orch start T-xxx`. 아니면 선행 태스크와 에이전트가 비면 관제가 띄운다(`orch start-ready`) |
  | `working` | 워커가 살아 있으면 worker_done 대기. 생존 확인이 안 되면 대기하되 오래 이어지면 관제가 decide 로 알린다. worker_done 없이 정상 종료하면 관제가 리뷰를 시작하고, 실패·종료면 관제가 이어서 하도록 다시 보낸다(최대 라운드면 사용자에게 묻고 `orch fix T-xxx --extra-round --note "<지시>"` 또는 `orch drop`) |
  | `reviewing` | 리뷰가 진행 중이면 끝나기를 기다리고, 끊겼으면 관제가 다시 리뷰한다 |
  | `approved` | 관제가 accept 한다 |
  | `awaiting_user` | 아직 묻지 않았으면 승인 대상 태스크가 모두 리뷰를 지난 뒤 관제가 디자인 승인 질문을 올린다. 물었으면 답이 오면 `orch design-decide <질문 id> --approve T-xxx,… --fix 'T-yyy=<요청>'` 으로 화면별 결정을 넘긴다 |
  | `changes` | 관제가 리뷰 지적대로 수정 라운드를 띄운다. 최대 라운드면 사용자에게 묻고 `orch fix T-xxx --extra-round --note "<지시>"` 또는 `orch drop` |
  | `launch_failed` | 관제가 Orca 에 남은 워커를 확인하고 escalation decide 를 한 번 올린다. 메인은 `orch start T-xxx --retry` 또는 `orch fix T-xxx --retry`, 포기하면 `orch drop` |
  | `launching` | 워커를 띄우는 중이다(상태 파일은 `launch_failed` 그대로이고 행에 `launching: true` 가 붙는다). 끝날 때까지 기다린다. 그동안 `--retry` 는 exit 2 |
  | `done`, `dropped` | 없음(`null`) |

- `orch drop T-xxx --reason "<이유>"`(메인이 관제 큐로 보낸다): 최대 라운드에서 사용자가 중단을 고르거나 `launch_failed` 를 복구할 수 없을 때 태스크를 중단하고 소유 경로 잠금을 푼다. progress 는 올리지 않는다. 워커가 일하는 중인(settled 가 아닌) `working` 태스크는 거부하고, `--force` 로만 강제 중단한다.
- 관제가 죽었으면 메인 세션의 훅이 자동으로 다시 띄운다. 세션을 열 때와 프롬프트를 칠 때(60초 간격) 관제 pid 와 heartbeat 를 확인하고, 프로세스가 없는 것이 확실할 때만 `orch up` 을 분리 프로세스로 실행한다. 메인은 답변 첫 줄에 한 줄로 알린다. 관제가 꺼져 있는 동안 낸 요청(`start`, `fix` 등)은 큐에 쌓이지 않고 "관제가 꺼져 있다" 로 거부되므로, `/orca-team:up` 으로 관제를 띄운 뒤 다시 한다. 끄려면 `.orchestration/config.toml` 의 `[drive]` 에 `auto_up = false` 를 넣는다. 실행 기록은 `.orchestration/state/auto-up.log` 에 남는다. 자동으로 띄운 관제가 10분 안에 다시 꺼지면 그 10분 동안은 다시 띄우지 않고 /orca-team:up 을 안내한다. 같은 안내는 바뀔 때와 세션 시작 때만 한다. 프로세스는 살아 있는데 heartbeat 가 2분 넘게 멈춘 관제(권한 창 대기 등)는 `hung` 으로 감지해 띄우지 않고 관제 탭을 확인하라고 알린다. 설계: `docs/superpowers/specs/2026-11-06-orca-team-auto-up-design.md`.
- 수동 복구는 메인에서 `/orca-team:up` 을 실행한다. 관제가 살아 있으면 그대로 두고, 없으면 새로 띄운다. 새 관제는 기존 Run 에 다시 붙고(`run-use`) `orch status` 의 `next` 로 이어 간다. 프로세스는 있는데 응답이 없으면(`hung`) `up` 도 띄우지 않고 관제 탭을 확인하라고 한다.

## 개발

- 테스트: `bash orca-team/tests/run.sh`(가짜 orca·codex·Codex 래퍼 사용, 실제 Orca 상태를 건드리지 않는다). 내장 Codex 래퍼 회귀 테스트(2분 남짓)까지 돌리려면 `bash orca-team/tests/run.sh --wrapper`(또는 `ORCA_TEAM_TEST_WRAPPER=1`).
- Codex 래퍼 계약: `docs/codex/`(`02-codex-invocation.md` 가 호출 계약)
- 플러그인을 고치며 시험하려면 저장소를 clone 한 뒤 `/plugin marketplace add <clone 한 폴더>` 로 추가한다. 디렉터리 소스 마켓플레이스는 폴더를 직접 읽으므로 수정이 다음 세션 시작이나 `/reload-plugins` 에 반영된다. `/plugin update` 는 필요 없다.
- 설계: `docs/superpowers/specs/2026-10-30-orca-team-design.md`, 그릴링 기획·병렬 실행 `docs/superpowers/specs/2026-10-31-orca-team-grilling-parallel-design.md`, 디자인 디렉터 `docs/superpowers/specs/2026-10-31-orca-team-design-director-design.md`
- 구현 계획: `docs/superpowers/plans/2026-10-30-orca-team.md`, `docs/superpowers/plans/2026-10-31-orca-team-grilling-parallel.md`, `docs/superpowers/plans/2026-10-31-orca-team-design-director.md`
