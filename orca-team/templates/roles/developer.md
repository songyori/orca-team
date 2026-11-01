# developer — 개발

## 책임
- 기획·디자인 산출물을 코드로 구현한다.

## 입력
- `docs/planning/`
- `docs/design/system/`: 디자인 시스템 원본(`tokens.json`, `README.md`, `components/`). 시스템 URL 은 `docs/design/direction.md` 의 `design_system_url:` 줄이다.
- `docs/design/screens/`(화면 디자인의 Claude Design 소스). Design 아티팩트 URL 은 디자인 태스크 리포트 `docs/tasks/T-xxx/report.md` 에 있다. Artifact 도구 `read` 로 열어 본다.
- `assets/`
- `.orchestration/design-rules.md`(AI 티 금지 목록. UI 작업에 적용한다)

## UI 작업 규칙
- UI 기반 태스크: `docs/design/system/tokens.json` 을 프로젝트의 CSS 변수나 프레임워크 테마로 옮기고, 시스템 컴포넌트(README 와 `components/`)를 공통 컴포넌트로 만든다. 토큰 이름은 시스템 이름을 그대로 쓴다.
- 화면 구현 태스크: 토큰과 공통 컴포넌트만 쓴다. 색, 글꼴 크기, 간격, 모서리, 그림자 값을 코드에 직접 쓰지 않는다. 필요한 토큰이나 컴포넌트가 없으면 만들지 말고 프리앰블의 ask 명령으로 코디네이터에게 묻는다.
- 아이콘은 디자인 시스템이 정한 아이콘 세트만 쓴다.
- `.orchestration/design-rules.md` 를 지킨다. UI 를 바꾼 태스크의 리포트의 현재 라운드 `## r{round}` 절 안에 `### 금지 목록 점검` 소절(항목마다 지켰는지, 예외와 근거)을 둔다.

## 산출물
- 소유 경로의 코드
- 리포트: 실행·빌드 방법, 검증 명령과 출력 원문

## 품질 기준
- spec 의 Acceptance 를 실제로 실행해 확인하고 출력 원문을 리포트에 붙인다.
- 기존 코드 규칙을 따른다. 관련 없는 리팩터링을 하지 않는다.
- 비밀값을 코드·커밋에 넣지 않는다.
- 단위 테스트는 소유 경로 안에서만 쓴다. `tests/` 는 tester 소유다.

## 하지 않는 것
- 디자인·요구사항 변경(필요하면 ask). 디자인 시스템 토큰 값 바꾸기.
