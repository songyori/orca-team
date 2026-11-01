# orca-team 디자인 디렉터 설계

- 날짜: 2026-10-31
- 대상: orca-team 0.3.0(`orca-team/`). 선행 설계는 `2026-10-30-orca-team-design.md`와 `2026-10-31-orca-team-grilling-parallel-design.md`이다.
- 상태: 사용자가 브레인스토밍 결정 6개와 설계 1~5절을 승인했다.

## 1. 목적

AI가 만든 티가 나지 않는, 지금 서비스되는 상용 제품 수준의 디자인을 만든다. 그리고 그 디자인을 서비스 전체에 같은 규칙으로 적용한다.

1. 디자인을 시작하기 전에 서비스에 맞는 레퍼런스를 조사해 사용자에게 화면으로 보여 주고, 원하는 스타일을 확인한다.
2. 대표 화면 몇 개를 서로 다른 톤 3가지로 시안을 만들어 보여 주고, 사용자가 고르거나 피드백을 준다.
3. 확정한 방향으로 디자인 시스템을 만들어 사용자가 직접 보고 확인한 뒤, 모든 화면 디자인과 UI 코드에 그 시스템만 쓴다.
4. AI 디자인의 전형적인 패턴(고정 이모지, 왼쪽 줄 카드, 파랑·보라 그라디언트 등)을 금지하고, 작업자 자체 점검, 리뷰, 자동 검사로 막는다.

## 2. 사용자 결정

| # | 질문 | 결정 |
|---|---|---|
| 1 | 방향 잡기 단계 | 전용 방향 태스크 하나가 맨 앞에서 한다. 화면 디자인 태스크들은 그 뒤에 병렬로 시스템을 적용한다 |
| 2 | 시안 화면 수 | 3개(대표 화면, 정보가 빽빽한 화면, 입력 화면). 톤 3가지라 모두 9장 |
| 3 | 피드백 뒤 다음 라운드 | 피드백 내용에 따른다. 세부 수정이면 수정안 1개, 방향을 바꾸는 피드백이면 새 3안 |
| 4 | 디자인 시스템 확인 | 전체 화면 작업 전에 한 번 확인받는다. 디자인 시스템은 Claude Design으로 만든 문서여야 하고, 반드시 시각적으로 확인할 수 있어야 한다 |
| 5 | 방향 태스크 담당 | 새 직무 `director`(디자인 디렉터) |
| 6 | 설계 1~5절 | 승인(흐름, 보여 주는 형태, 금지 규칙, 일관 적용 규칙, 코드 변경 범위) |

마지막 화면 일괄 승인(0.3.0의 `user_approval_roles = ["designer"]`)은 그대로 둔다.

## 3. 전체 흐름

```
기획 확정 → orch plan apply → orch start-ready
  director: 디자인 방향 (태스크 1개, opus)
    ① 레퍼런스 조사 → 레퍼런스 보드(Claude Design) → 사용자: 스타일 묶음 선택
    ② 시안 화면 3개 × 톤 3가지 = 9장(Claude Design) → 사용자: 선택 또는 피드백 (반복)
    ③ 디자인 시스템(Claude Design, Design System 타입) → 사용자: 확인
    → worker_done → Codex 리뷰 → accept → done (일괄 승인 대상 아님)
  ├─ designer: 화면 디자인 N개 (병렬, 시스템 적용) → Codex 리뷰 → awaiting_user → 마지막 일괄 승인
  ├─ developer: UI 기반 (tokens.json → CSS 변수·테마, 공통 컴포넌트)  ← director done 뒤 바로 시작
  └─ developer: 화면 구현 (해당 화면 디자인 + UI 기반에 의존)
```

- 사용자에게 가는 질문은 레퍼런스 1회, 시안 1회 이상, 디자인 시스템 확인 1회, 마지막 화면 일괄 승인 1회다.
- director 질문은 기획 질문과 같은 `orch ask-user` 경로와 형식을 쓴다. 짧은 질문, `예:` 예시, 추천 선택지를 맨 앞에 두고 추천 사유를 붙인다. 보드·시안·시스템 링크와 요약은 `context`에 담는다.
- 메인은 `render.context`를 먼저 그대로 보여 준 뒤 묻는다(0.3.0 F1 가드가 강제한다).
- 디자인에 의존하지 않는 태스크(예: 저장 모듈, API)는 director를 기다리지 않는다.

## 4. director 직무

### 4.1 정의와 설정

- `templates/config.toml`에 `[roles.director]`를 추가한다.
  - `title = "디자인 디렉터"`
  - `claude_model = "opus"`, `claude_effort = "xhigh"`
  - `codex_model = "gpt-6.1-sol"`, `codex_effort = "high"`
  - `owns = ["docs/design/**", "assets/**"]`
- `user_approval_roles`는 `["designer"]` 그대로다. director는 사용자가 진행 중에 확정하므로, Codex APPROVE 뒤 `orch accept`에서 바로 `done`이 된다.
- 직무 지침은 새 파일 `templates/roles/director.md`다. scaffold가 `.orchestration/roles/director.md`로 복사한다.

### 4.2 입력

- `docs/planning/spec.md`(화면 목록, 사용자 흐름), `contracts.md`, `interview.md`(브랜드 자산, 사용자가 아는 레퍼런스)
- 태스크 spec에 기획이 지정한 시안 화면 3개(대표, 고밀도, 입력)
- `.orchestration/design-rules.md`(5절 금지 목록)

### 4.3 단계 ① 레퍼런스

1. **조사.** 같은 분야의 상용 서비스와, 인접 분야에서 완성도가 높은 서비스 6~9개를 고른다. WebSearch와 WebFetch를 쓴다. 고르는 기준은 서비스 성격, 사용자층, 정보 밀도, 플랫폼(웹·모바일)이다. 국내 서비스면 국내 레퍼런스를 포함한다.
2. **캡처.** Orca 내장 브라우저로 각 서비스의 실제 화면을 캡처한다.
   - `orca tab create --url <URL> --json` → `orca screenshot --format png` → `orca tab close --page <자기가 연 page id>`
   - 자기가 연 탭만 닫는다. 캡처가 막히면(로그인 필요, 차단) 공개된 대표 이미지나 링크만 쓰고, 보드에 "캡처 불가"라고 적는다.
   - 캡처 파일은 저장소 밖 `${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/refs/<프로젝트 이름>/`에 둔다. 저장소에는 커밋하지 않는다.
3. **레퍼런스 보드.** Claude Design 캔버스(Design 타입) "레퍼런스 보드"를 만든다.
   - 캡처를 스타일 묶음 3~4개로 나눠 배치한다. 묶음마다 이름(예: "차분한 모노톤·넓은 여백"), 특징 3줄, 이 서비스에 맞는 이유를 쓴다.
   - 각 레퍼런스에는 서비스 이름, 링크, 가져올 점(색, 글꼴, 레이아웃 밀도, 컴포넌트)을 붙인다.
4. **기록.** `docs/design/references.md`에 레퍼런스 목록(이름, URL, 묶음, 가져올 점, 캡처 여부)과 보드 URL을 적는다. Codex는 claude.ai 문서를 볼 수 없으므로 이 파일과 보드 원본(`docs/design/board/`)을 리뷰한다.
5. **질문.** `orch ask-user`로 묻는다.
   - question: "어느 스타일 묶음이 이 서비스에 맞나요?" + `예:` 줄
   - options: 스타일 묶음(2~4개). 추천 묶음을 맨 앞에 두고 추천 사유를 쓴다.
   - context: 보드 링크와 묶음별 한 줄 요약
   - 여러 묶음을 섞거나 직접 레퍼런스를 주는 답은 Other로 온다. 그대로 따른다.

### 4.4 단계 ② 톤 시안

1. 선택한 묶음(또는 사용자가 준 레퍼런스)을 바탕으로 서로 분명히 다른 톤&매너 3가지(A·B·C)를 정한다. 셋 다 선택한 스타일 안에 있어야 하고, 색·글꼴·밀도·모서리·그림자 중 둘 이상이 달라야 한다.
2. Claude Design 캔버스 하나에 톤 한 줄씩, 시안 화면 3개를 나란히 놓는다(9장). 내용은 그 서비스에 맞는 현실적인 데이터와 문구로 채운다.
3. 원본은 `docs/design/pilots/`에 커밋한다.
4. **질문.** question: "어느 톤으로 갈까요?" options: A·B·C(추천 맨 앞, 사유 포함). context: 캔버스 링크와 톤별 한 줄 특징.
5. **답 처리**
   - A·B·C 중 하나: 그 톤으로 확정하고 ③으로 간다.
   - Other(피드백): 내용을 판단한다.
     - 세부 수정(예: "B인데 색만 A처럼 차분하게"): 수정안 1개(시안 화면 3개)를 만들어 "이 수정안으로 확정할까요?"를 묻는다. 선택지는 "확정"과 "다시 수정"이고, 다시 수정할 내용은 Other로 받는다.
     - 방향 전환(예: "다 별로다, 더 대담하게"): 새 3안을 만들어 4번 질문을 다시 한다.
   - 확정할 때까지 반복한다. 라운드마다 캔버스를 새 버전으로 갱신하고 `docs/design/direction.md`에 피드백과 판단을 적는다.

### 4.5 단계 ③ 디자인 시스템

1. Claude Design의 **Design System 타입**으로 프로젝트 디자인 시스템을 만든다. 확정한 시안에서 값을 뽑아 정리한다.
   - 토큰: 색(라이트 테마 필수, 다크는 기획이 요구하면), 글꼴 패밀리와 크기 단계, 간격, 모서리, 그림자
   - 컴포넌트: 기획 화면에 필요한 것 전부. 최소한 버튼, 입력, 선택, 카드, 목록 항목, 내비게이션, 모달, 토스트, 빈 상태, 로딩, 오류다. 각 컴포넌트는 상태(기본, hover, focus, disabled, error)별 미리보기와 사용 규칙을 갖는다.
   - README: 브랜드 원칙, 시각 기초, 아이콘 세트(Lucide·Phosphor·Tabler 중 하나), 사용 규칙
   - Design System 타입의 지침(대비 4.5:1, 토큰 이름 규칙, tokens.json 목록 형태)을 따른다.
2. 시스템 원본(`project/` 아래 파일)을 저장소 `docs/design/system/`에 그대로 커밋한다. 개발자와 Codex는 이 파일을 읽는다. 특히 `docs/design/system/tokens.json`과 `README.md`를 읽는다.
3. **질문.** question: "이 디자인 시스템으로 전체 화면을 만들까요?" options: "확정"과 "수정 요청"(내용은 Other). context: 시스템 링크, 핵심 토큰 요약(주색, 본문 글꼴과 크기, 기본 모서리), 컴포넌트 목록.
4. 수정 요청이면 반영하고 다시 묻는다. 확정하면 `docs/design/direction.md`에 시스템 URL을 `design_system_url: <URL>` 한 줄로 적고, 리포트를 쓴 뒤 worker_done을 보낸다.
5. Codex 리뷰가 REQUEST_CHANGES를 내면 수정 라운드의 director는 확정 방향 안에서 고친다(예: 대비 부족, 금지 패턴, 빠진 컴포넌트 상태). 사용자가 확정한 모습이 눈에 띄게 바뀌는 수정일 때만 `ask-user`로 다시 확인받는다.

### 4.6 산출물

| 경로 | 내용 |
|---|---|
| `docs/design/direction.md` | 질문과 답 기록, 피드백 판단, 확정 근거, 보드·시안·시스템 URL(`design_system_url:` 줄) |
| `docs/design/references.md` | 레퍼런스 목록 |
| `docs/design/board/` | 레퍼런스 보드 원본 |
| `docs/design/pilots/` | 확정 시안 원본 |
| `docs/design/system/` | Design System 원본(`tokens.json`, `README.md`, `components/…`) |
| `docs/tasks/T-xxx/report.md` | 리포트(링크, 라운드 수, 금지 목록 점검 결과) |

사용자 답은 `ask-user`가 돌려준 즉시 `docs/design/direction.md`에 적는다. 기획 워커의 interview.md 규칙과 같다. orch는 돌려준 답을 읽음으로 기록해 다시 주지 않는다.

## 5. AI 티 금지 규칙

### 5.1 목록 (`templates/design-rules.md` → `.orchestration/design-rules.md`)

한 파일에 두고 director, designer, developer 지침과 리뷰 프롬프트가 함께 참조한다.

- 이모지를 아이콘, 제목 장식, 글머리, 상태 표시로 쓰지 않는다. 아이콘은 디자인 시스템이 정한 아이콘 세트 하나만 쓴다.
- 왼쪽에 굵은 색 줄이 있는 카드·콜아웃(`border-left` 강조)을 쓰지 않는다.
- 파랑→보라(인디고·바이올렛) 그라디언트, 그라디언트 글자를 쓰지 않는다. 이유 없는 네온 빛, 글래스모피즘, 블롭 배경도 쓰지 않는다.
- 모든 카드에 같은 큰 둥근 모서리와 부드러운 그림자를 반복하지 않는다. 모서리와 그림자는 시스템 토큰의 위계를 따른다.
- 정해진 틀 같은 랜딩(큰 그라디언트 제목, 버튼 2개, "아이콘·제목·두 줄 설명" 3열 카드)을 쓰지 않는다.
- 모든 섹션을 가운데 정렬하고 같은 간격만 반복하지 않는다. 정보 위계에 맞는 정렬과 리듬을 쓴다.
- 근거 없이 Inter나 시스템 글꼴 기본값을 쓰지 않는다. 한국어 서비스는 한글 글꼴을 정한다.
- 회색 글자를 남발하지 않는다. 본문 대비는 4.5:1, 24px 이상과 아이콘·테두리는 3:1 이상이다.
- 뻔한 문구("Unlock the power of", "Seamless", "Elevate", "혁신적인")와 의미 없는 배지("New", "AI-powered")를 쓰지 않는다.
- 시안과 화면은 그 서비스에 맞는 현실적인 데이터와 문구로 채운다. lorem ipsum이나 "사용자 1" 같은 더미를 쓰지 않는다.

### 5.2 강제 방법

1. **작업자 자체 점검.** director, designer, developer(UI 작업) 리포트에 "금지 목록 점검" 절을 둔다. 항목마다 지켰는지와 예외가 있으면 그 근거를 적는다.
2. **Codex 리뷰.** 리뷰 프롬프트가 디자인·UI 태스크(직무 director, designer, developer)에 `.orchestration/design-rules.md`를 읽게 한다. 위반은 REQUEST_CHANGES 사유다.
3. **orch 자동 검사.** `orch review`가 Codex를 부르기 전에 태스크가 바꾼 파일을 검사해 경고를 리뷰 프롬프트에 넣는다.
   - 대상 파일: `.html .css .scss .js .jsx .ts .tsx .vue .svelte .md .json`. `docs/design/references.md`, `docs/planning/`, `docs/tasks/`, `docs/reviews/`는 뺀다.
   - 검사 1, 이모지: Extended_Pictographic 범위 문자(U+1F300–1FAFF, U+2600–27BF 등). 위 대상 파일 전체에서 찾는다.
   - 검사 2, 왼쪽 줄 강조: `border-left` 또는 `border-inline-start`의 너비가 2px 이상이고 색이 있는 선언. Tailwind `border-l-[2-8]`과 `border-l-<색>` 조합도 찾는다.
   - 검사 3, 파랑·보라 그라디언트: `linear-gradient`·`radial-gradient` 안에 파랑 계열과 보라 계열 색이 함께 있는 경우. Tailwind `from-blue-*`·`from-indigo-*`와 `to-purple-*`·`to-violet-*` 조합도 찾는다.
   - 결과는 "파일:줄: 패턴" 목록이다. 블록 인용처럼 정당한 쓰임이 있으므로 자동 불합격이 아니라 경고로 넘기고 Codex가 판단한다. 경고가 없으면 "없음"이라고 적는다.
   - 검사는 표준 라이브러리 정규식만 쓰고 빨라야 한다. 파일당 1MB 넘는 파일은 건너뛴다.

## 6. 일관 적용 규칙

### 6.1 화면 designer (`templates/roles/designer.md`)

- 입력에 `docs/design/direction.md`와 `docs/design/system/`을 더한다. 시작할 때 Design System의 `project/README.md`를 읽는다(Artifact `read`, `path: project/README.md`).
- Claude Design 캔버스를 만들 때 계정 기본 디자인 시스템이 아니라 `direction.md`의 `design_system_url` 시스템을 쓴다.
- 시스템에 없는 색, 글꼴 크기, 간격, 컴포넌트를 새로 만들지 않는다. 꼭 필요하면 리포트에 "시스템 추가 제안"(무엇을, 왜, 어느 화면에서)을 적고, 그 화면에는 가장 가까운 기존 토큰을 쓴다.
- 빈 상태, 로딩, 오류, 반응형(모바일·데스크톱 기준폭)을 모든 화면에 넣는다. 지금 규칙을 유지한다.
- 금지 목록을 지키고 리포트에 점검 결과를 적는다.

### 6.2 developer (`templates/roles/developer.md`)

- UI 기반 태스크: `docs/design/system/tokens.json`을 프로젝트의 CSS 변수나 프레임워크 테마로 옮기고, 시스템 컴포넌트를 공통 컴포넌트로 만든다.
- 화면 구현은 토큰과 공통 컴포넌트만 쓴다. 색, 크기, 간격, 모서리, 그림자를 하드코딩하지 않는다. Codex 리뷰가 확인한다.
- 금지 목록을 지킨다.

### 6.3 기획 (`templates/roles/planner.md`)

- 점검표의 "디자인 방향(톤, 참고 자료)"을 "브랜드 자산(로고, 색, 글꼴)과 사용자가 아는 레퍼런스. 없으면 없다고 적는다"로 바꾼다. 레퍼런스 조사는 director가 한다.
- 화면이 있는 서비스면 tasks.toml에 다음을 넣는다.
  - director 태스크 1개를 맨 앞에 둔다. owns는 `["docs/design/direction.md", "docs/design/references.md", "docs/design/board/**", "docs/design/pilots/**", "docs/design/system/**"]`이다. `change`에 시안 화면 3개(대표, 고밀도, 입력)를 spec.md 화면 이름으로 적는다.
  - 화면 designer 태스크는 모두 director 태스크에 의존한다. owns는 `docs/design/screens/<화면>/**`이다.
  - UI 기반 developer 태스크는 director 태스크에 의존한다.
  - 화면 구현 developer 태스크는 해당 화면 designer 태스크와 UI 기반 태스크에 의존한다.
- 0.3.0과 같이 확정 질문의 태스크 그래프 표에 director가 보인다.

### 6.4 코디네이터 (`templates/coordinator.md`)

- 직무 목록에 director를 추가한다.
- **시스템 추가 제안**
  - 화면 designer 리포트에 "시스템 추가 제안"이 있으면 곧바로 처리하지 않는다. 모든 화면 designer 태스크가 `awaiting_user`가 되면, `orch design-review` 전에 제안을 모은다.
  - 모은 제안은 `orch task new director "디자인 시스템 보강: <요약>"`과 `orch start`로 director 보강 태스크 1개에서 처리한다. 보강 태스크의 owns는 `docs/design/system/**`와 `docs/design/direction.md`다.
  - 보강이 done이 되면 제안을 낸 화면을 `orch fix T-xxx --note "디자인 시스템 보강(T-yyy)을 반영한다"` 한다. 그다음 `orch design-review`를 한다.
  - UI 기반 developer 태스크가 이미 done이면 `orch task new developer "토큰 갱신 반영: <요약>"`을 만든다.
- 보강 director는 확정 방향과 충돌하는 제안만 사용자에게 묻는다(`orch ask-user`). 나머지는 확정 방향 안에서 스스로 정한다.

## 7. orch·훅 변경

### 7.1 `orch ask-user` 허용 직무 (`lib/orch/ask.py`)

- `ASK_ROLE = "planner"`를 `ASK_ROLES = ("planner", "director")`로 바꾼다.
- `require_planner`는 `require_ask_role`이 된다. `working` 상태의 `ASK_ROLES` 태스크만 `ask-user`를 쓴다. 오류 문구도 바꾼다.
- `open_questions`의 ask 항목 조건, `reask`, `--pending`도 `ASK_ROLES`로 일반화한다.
- 나머지(JSON 스키마, render, 540초 대기와 exit 4, `--wait`)는 그대로다.

### 7.2 훅 (`hooks/orca_team_hook.py`)

- 워커 spec 첫 줄로 기획 워커를 알아보는 `PLANNER_RE`를 ask 직무 전체(planner, director)로 넓힌다. 세션 표시는 `worker:planner`, `worker:director`다.
- 이 워커들이 AskUserQuestion을 부르면 거부하고 `orch ask-user` 사용법을 안내한다. 기획 워커와 같은 문구에서 직무 이름만 바뀐다.
- 그 밖의 워커와 코디네이터 규칙은 그대로다.

### 7.3 `orch plan check` (`lib/orch/plan.py`)

- config에 `director` 직무가 있고 tasks.toml에 designer 태스크가 하나라도 있으면 다음을 요구한다.
  - director 태스크가 정확히 1개다.
  - 모든 designer 태스크가 director 태스크에 직접 또는 간접으로 의존한다.
- 위반은 exit 2이고, 문구가 고칠 방법(어느 태스크의 `depends_on`에 무엇을 넣을지)을 알려 준다.
- config에 director가 없으면(0.3.0 프로젝트) 검사하지 않는다.
- 코디네이터가 나중에 `orch task new director`로 만드는 보강 태스크는 tasks.toml 밖이라 이 검사와 무관하다.

### 7.4 `orch review` 자동 검사 (`lib/orch/review.py`, `templates/review-prompt.md`)

- 새 함수 `design_lint(root, files) -> list[str]`이 5.2의 3번 검사를 한다.
- 태스크 직무가 director, designer, developer면 `compose_prompt`가 결과를 `{design_lint}` 자리에 넣는다. 다른 직무면 이 절을 넣지 않는다.
- 리뷰 프롬프트에 디자인·UI 태스크용 기준 한 단락을 더한다. 내용은 `.orchestration/design-rules.md`를 읽고 위반을 REQUEST_CHANGES 사유로 다루라는 것, 그리고 developer는 토큰과 공통 컴포넌트만 쓰는지 확인하라는 것이다.

### 7.5 scaffold와 버전

- scaffold가 `templates/design-rules.md`를 `.orchestration/design-rules.md`로, `templates/roles/director.md`를 `.orchestration/roles/director.md`로 복사한다.
- `VERSION`과 `plugin.json`을 0.4.0으로 올린다.

## 8. 업그레이드

- 0.3.0 프로젝트는 config에 `[roles.director]`가 없으므로 지금 흐름 그대로 돈다. plan check의 director 검사도 꺼진다.
- 새 흐름을 쓰려면 다음 셋을 한다. README에 이 절차를 적는다.
  1. `.orchestration/config.toml`에 `[roles.director]` 블록을 추가한다.
  2. `.orchestration/roles/director.md`, `.orchestration/design-rules.md`를 플러그인 템플릿에서 복사한다.
  3. 바뀐 `roles/planner.md`, `designer.md`, `developer.md`, `coordinator.md`를 갱신한다.

## 9. 오류 처리

| 상황 | 처리 |
|---|---|
| 레퍼런스 사이트 캡처 실패(로그인, 차단) | 공개 대표 이미지나 링크만 쓰고 보드에 "캡처 불가"라고 적는다. 멈추지 않는다 |
| Orca 브라우저 명령 실패 | 링크와 설명만으로 보드를 만들고 리포트에 적는다 |
| 사용자가 9분 안에 답하지 않음 | `ask-user` exit 4. `--wait <id>`로 이어서 기다린다(기획과 같다) |
| director 워커가 질문 대기 중 끝남 | 코디네이터가 `orch status`의 `next`대로 fix한다. 새 워커는 `direction.md`를 읽고 `ask-user --pending`부터 확인한다 |
| Design System 타입을 찾지 못함 | `Artifact list scope=types`로 다시 찾는다. 그래도 없으면 worker_done을 escalation으로 보내 코디네이터가 메인에 묻는다 |
| 화면 designer가 프로젝트 시스템 URL을 못 찾음 | `direction.md`의 `design_system_url` 줄이 없으면 작업하지 않고 코디네이터에게 ask한다 |

## 10. 테스트

- **config:** 템플릿에 `[roles.director]`가 있고, `user_approval_roles`에 director가 없다.
- **ask:** director의 working 태스크는 `ask-user`를 쓸 수 있다. designer와 developer는 exit 2로 거부된다. director 질문도 `open_questions`, `reask`, `--pending`에 나온다.
- **훅:** director 워커 spec 첫 줄로 `worker:director`가 표시된다. AskUserQuestion을 거부하고 `orch ask-user`를 안내한다. 일반 워커와 코디네이터의 문구는 바뀌지 않는다.
- **plan check:**
  - director가 있는 config에서 designer가 director에 의존하지 않으면 exit 2다.
  - director가 2개여도 exit 2다.
  - 간접 의존은 통과한다.
  - director가 없는 config에서는 검사를 건너뛴다.
- **design_lint:**
  - 이모지, `border-left: 4px solid`, Tailwind `border-l-4 border-blue-500`, 파랑·보라 그라디언트를 찾는다.
  - 정상 CSS(`border-left: 1px solid` 표 구분선 등 1px)는 찾지 않는다.
  - 제외 경로는 건너뛰고, 1MB 넘는 파일도 건너뛴다.
- **리뷰 프롬프트:** director, designer, developer 태스크에만 검사 결과와 디자인 기준 절이 들어간다.
- **scaffold:** `design-rules.md`와 `roles/director.md`를 복사한다.
- **템플릿 문구:**
  - director.md에 단계 ①②③, Orca 캡처 명령, "자기가 연 탭만 닫는다", `design_system_url`이 있다.
  - designer.md에 프로젝트 시스템 사용과 "시스템 추가 제안"이 있다.
  - planner.md에 director 그래프 규칙이 있다.
  - coordinator.md에 보강 처리가 있다.
- **전체:** 가짜 orca로 한 바퀴를 돈다. plan apply → director 시작 → ask-user → answer → accept(done) → 화면 designer와 UI 기반이 함께 시작된다.

## 11. 범위 밖

- 레퍼런스 캡처의 자동 품질 판정(가려진 화면, 쿠키 배너 등). director가 직접 보고 고른다.
- Design System의 코드 동기화(`/design-sync`). 이번에는 tokens.json 원본 커밋과 개발자 변환까지만 한다.
- 다크 테마 강제. 기획이 요구할 때만 만든다.
- 시안 화면 수와 톤 수를 설정으로 바꾸는 기능. 3과 3으로 고정한다.

## 12. 실환경 확인 결과 (2026-10-31, 시험 프로젝트 "솔빛 마을도서관" 좌석 예약 웹앱)

| 확인 | 결과 |
|---|---|
| init 이 director 설정·직무 문서·`design-rules.md` 를 깔고 owns 를 새 구조로 제안 | 통과 |
| 기획 그래프(11태스크): director 맨 앞, 화면 designer·UI 기반이 director 에 의존, 디자인과 무관한 저장 모듈은 함께 시작, `plan check` 통과 | 통과 |
| director 세션 `worker:director`, 레퍼런스 9곳 캡처(저장소 밖), 막힌 1곳은 "링크만", 연 탭 정리 | 통과 |
| 스타일 묶음 → 톤 시안 9장 → 디자인 시스템 확정 질문 3개(사용자 직접 답), 메인이 context 를 먼저 보여 줌, `design_system_url` 기록 | 통과 |
| Codex 리뷰가 금지 목록의 대비 기준 위반을 잡고, director 가 다시 묻지 않고 고침. accept 뒤 바로 done | 통과 |
| director done 직후 화면 designer 3개와 UI 기반 동시 시작. 화면 designer 가 프로젝트 시스템을 쓰고 라운드별 `### 시스템 추가 제안` 작성 | 통과 |
| 일괄 승인 전 제안 18건을 director 보강 1건으로 처리, 출처 기록, 화면 3개 재수정과 토큰 갱신 태스크 시작, 마지막 라운드 제안 확인 뒤 design-review | 통과 |
| design-review 화면 링크 | 실패 → 수정(F1) |
| 사용자 일괄 승인 답 → 화면 디자인 3개 done, 앱 틀 구현 시작 | 통과 |

- F1: 화면 링크가 모두 디자인 시스템 URL 로 나왔다. 디자이너 리포트가 화면 링크 다음에 시스템 URL 을 적기 때문이다. `design_link` 가 `design_system_url` 을 후보에서 빼도록 고쳤다(f0e5a13). 코디네이터는 스스로 정정 progress 를 보냈다.
- 알려진 Minor: 보강 반영 fix 가 이력에 `user_requested` 로 남는다. 기획 질문 example 에 "예:" 가 겹쳐 찍힌다(0.3.0 부터).
- 확인하지 않은 것: 톤 시안 피드백 분기(수정안 1개·새 3안), 일괄 승인 이후 화면 구현(0.3.0 과 같은 흐름).
