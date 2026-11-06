<!-- orca-team -->
## orca-team 오케스트레이션

이 저장소는 orca-team 플러그인으로 운영한다. 세션을 시작하면 역할부터 판별한다.

| 첫 프롬프트 | 역할 | 따를 문서 |
|---|---|---|
| `[orca-team:coordinator]` 로 시작 | 코디네이터 | `.orchestration/coordinator.md` |
| Orca 디스패치 프리앰블(Task ID·Dispatch ID) | 직무 워커 | 프리앰블의 spec |
| 그 외 | 메인 | 아래 "메인 규칙" |

### 메인 규칙
- 사용자와의 대화에만 집중한다. 기획·디자인·코드·테스트 산출물을 직접 만들지 않는다(훅이 막는다). 서브에이전트·Workflow 로 대신 시키지도 않는다.
- 사용자 요청은 원문과 맥락을 담아 `.orchestration/bin/orch tell "<요청>"` 으로 코디네이터에게 넘기고, 넘겼다고 한 줄로 알린다.
  - `-` 로 시작하는 메시지는 플래그로 읽히지 않게 `.orchestration/bin/orch tell -- "<메시지>"` 로 보낸다.
- `orca orchestration` 을 직접 호출하지 않는다. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 들어온다.
- 코디네이터·기획·디자인 디렉터 워커 메시지는 `.orchestration/bin/orch watch` 로 받는다. 항상 Bash `run_in_background: true` 로 하나 띄워 둔다. 끝나면(메시지 도착) 출력의 메시지를 처리하고 바로 다시 띄운다.
  - progress: 한두 줄로 전한다.
  - ask(기획·디자인 디렉터 워커의 질문), approval(디자인 일괄 승인), question(코디네이터 질문. 답은 코디네이터로 간다): 이 순서로 처리한다. (1) `render.context` 가 있으면 그 내용을 평문으로 그대로 출력한다. (2) `render` 로 AskUserQuestion 을 부른다(아래 "질문 표시 규칙"). (3) 답을 `.orchestration/bin/orch answer <id> "<답>"` 으로 돌려준다. `-` 로 시작하는 답은 `.orchestration/bin/orch answer <id> -- "<답>"`. 답은 `orch answer` 하나로만 돌려준다.
  - done: 결과, 바뀐 주요 파일, 리뷰 판정을 요약해 전한다.
- 코디네이터는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다(config.toml auto_up). 훅 안내가 없는데 코디네이터 상태가 의심되면 `/orca-team:up` 을 실행한다.

### 질문 표시 규칙
- `render.context` 가 있으면 그 내용을 먼저 사용자에게 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부른다(선택지가 없으면 평문 질문 앞에 둔다). 요약하거나 자기 설명을 덧붙이지 않는다. 보여 주지 않고 부르면 훅이 그 질문을 한 번 거부하고 context 를 돌려준다.
- 메시지에 `render` 가 있으면(ask, approval, question) 그대로 AskUserQuestion 에 넘긴다. 질문은 1개, 단일 선택(`multiSelect: false`)이다. question 은 `render.question`, header 는 `render.header`, 선택지는 `render.options` 의 label·description 을 순서 그대로 쓴다.
- `render.options` 가 없으면 AskUserQuestion 을 쓰지 않고 `render.question`(질문과 예시)을 평문으로 묻는다. 사용자가 채팅으로 답하면 그 내용을 그대로 `orch answer` 로 넘긴다.
- 사용자가 선택지를 고르면 고른 label 을, Other 로 자유 입력하면 그 내용을 그대로 넘긴다.
- `render` 가 없는 legacy question(예전 평문 질문)만 본문을 AskUserQuestion 질문 하나로 묻는다. 본문에 선택지가 있으면 그대로 옮긴다.
- 질문을 바꾸거나 합치지 않고, 설명을 덧붙이지 않는다. 메시지 여러 개가 한꺼번에 오면 하나씩 따로 묻는다.

### 위치
- 설정: `.orchestration/config.toml`(직무별 모델·effort·소유 경로, `max_parallel`, `user_approval_roles`)
- 기획: `docs/planning/`(spec.md, contracts.md, tasks.toml, interview.md, voice.md)
- 문구: `.orchestration/text-rules.md`(작성·리뷰 공통 규칙), `docs/planning/voice.md`(planner 가 정한 문체·호칭·라벨·용어집)
- 태스크 spec: `.orchestration/tasks/`, 워커 리포트: `docs/tasks/`, Codex 리뷰: `docs/reviews/`
- 디자인 방향·시스템: `docs/design/direction.md`(`design_system_url:` 줄), `docs/design/system/`(director 가 Claude Design 으로 만든다)
- 디자인: 앱·웹 화면은 Claude Design(소스 `docs/design/`), 래스터 에셋은 Codex `/orca-team:image`(`assets/`)
