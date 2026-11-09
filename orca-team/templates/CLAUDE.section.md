<!-- orca-team -->
## orca-team 오케스트레이션

이 저장소는 orca-team 플러그인으로 운영한다. 세션을 시작하면 역할부터 판별한다.

| 첫 프롬프트 | 역할 | 따를 문서 |
|---|---|---|
| Orca 디스패치 프리앰블(Task ID·Dispatch ID) | 워커(고정 에이전트 또는 임시 워커) | 프리앰블의 spec |
| 그 외 | 메인 | 아래 "메인 규칙" |

관제(`orch drive`)는 관제 탭의 셸에서 도는 프로그램이다. 완료 대기, 리뷰, accept·fix, 다음 태스크 시작, 묶음 후속, 세션 리셋, 복구를 정해진 절차대로 하고 판단이 필요한 일만 decide 로 메인에 올린다.

### 메인 규칙
- 사용자와 대화하고 판단한다. 기획·디자인·코드·테스트 산출물을 직접 만들지 않는다(훅이 막는다). 서브에이전트·Workflow 로 대신 시키지도 않는다.
- 사용자 요청은 기획 태스크로 맡긴다.
  1. `.orchestration/bin/orch task new planner "<요청 요약>"`.
  2. `.orchestration/tasks/T-xxx.md` 의 Target·Change·Constraints·Acceptance 를 채운다. Change 에 사용자 요청 원문을 넣는다. `docs/planning/tasks.toml` 이 이미 있고 사용자가 추가 요청만 했다면 front matter 에 `plan: append` 를 넣고 Change 에 "기존 `docs/planning/` 을 읽고 추가 요청만 인터뷰한다. tasks.toml 의 기존 태스크는 그대로 두고 새 `[[task]]` 만 덧붙인다"를 적는다. `<...>` 자리표시가 남으면 start 가 거부한다.
  3. `git add -- .orchestration/tasks/T-xxx.md && git commit -m "[T-xxx] spec: <제목>" -- .orchestration/tasks/T-xxx.md`. 워커와 인덱스를 같이 쓰므로 경로 없는 `git commit` 은 쓰지 않는다.
  4. `.orchestration/bin/orch start T-xxx` 하고 넘겼다고 한 줄로 알린다. 기획 워커는 `orch ask-user` 로 메인을 거쳐 사용자를 인터뷰한다.
- 진행 중 태스크에 범위를 더할 때: spec 에 "추가 범위"를 적어 위와 같이 경로 지정 커밋한 뒤, 태스크가 working 이면 `.orchestration/bin/orch scope T-xxx "<내용>"`. 시작 전(pending)이면 커밋으로 끝이다.
- 메인이 직접 만든 태스크(decide 처리나 증분 기획으로 만든 것)는 `orch task new` 만으로는 시작되지 않는다. spec 채우기 → 경로 지정 커밋 → `.orchestration/bin/orch start T-yyy` 를 모두 하고 나서 해당 decide 를 `orch answer` 로 닫는다. 시작할 수 없는 상태(에이전트 사용 중, 선행 미완)면 start 가 `{"status": "waiting"}` 을 돌려주고 `pending` 으로 저장되어 관제가 나중에 시작하니 그대로 닫는다.
- Orca 를 부르는 `orch` 명령(`start`, `start-ready`, `fix`, `accept`, `drop`, `design-decide`, `scope`, `agent reset`)은 관제 큐를 거친다. "관제가 꺼져 있다" 면 `/orca-team:up` 뒤에 다시 한다. "관제가 처리 중" 이면 결과는 watch 의 `result` 메시지로 온다.
- `orca orchestration` 을 직접 호출하지 않고 `orch drive` 를 메인에서 실행하지 않는다. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 들어온다.
- 관제·기획·디자인 디렉터 워커 메시지는 `.orchestration/bin/orch watch` 로 받는다. 항상 Bash `run_in_background: true` 로 하나 띄워 둔다. 끝나면(메시지 도착) 출력의 메시지를 처리하고 바로 다시 띄운다.
  - progress: 한두 줄로 전한다.
  - ask(기획·디자인 디렉터 워커의 질문): (1) `render.context` 가 있으면 그 내용을 평문으로 그대로 출력한다. (2) `render` 로 AskUserQuestion 을 부른다(아래 "질문 표시 규칙"). (3) 답을 `.orchestration/bin/orch answer <id> "<답>"` 으로 돌려준다. `-` 로 시작하는 답은 `.orchestration/bin/orch answer <id> -- "<답>"`.
  - approval(디자인 일괄 승인): ask 와 같이 보여 주고 묻는다. 답을 화면별 결정으로 정리해 `.orchestration/bin/orch design-decide <id> --approve T-001,T-002 --fix 'T-003=<사용자 요청 원문>'` 으로 넘긴다. 물은 화면마다 승인이나 수정이 하나씩 있어야 한다. 어느 화면인지 알 수 없는 답은 다시 묻는다.
  - decide: 아래 "판단 기준" 대로 처리하고 `.orchestration/bin/orch answer <id> "<처리 내용>"` 으로 닫는다. worker_question 은 이 답이 그 워커에게 간다.
  - decide 가 열려 있는 동안 관제는 그 태스크를 자동으로 다루지 않는다. 처리했으면(명령을 실행했거나 그대로 두기로 했으면) 반드시 `orch answer <id> "<처리 내용>"` 로 닫아야 관제가 다시 이어 간다. 명령이 적히지 않은 escalation 도 같다.
  - result: 관제가 늦게 끝낸 요청의 결과. 실패면 다시 판단한다.
  - done: 결과, 바뀐 주요 파일, 리뷰 판정을 요약해 전한다.
- 관제는 훅이 확인하고 꺼져 있으면 자동으로 다시 띄운다(config.toml `[drive] auto_up`). 훅 안내가 없는데 관제 상태가 의심되면 `/orca-team:up` 을 실행한다.

### 판단 기준 (decide)
decide 에 적힌 `files` 만 읽고 `commands` 중 하나로 처리한다. 사용자에게 물을 때는 AskUserQuestion 을 직접 쓴다(질문 하나, 선택지 2–4개, 추천 하나).

| kind | 기준 |
|---|---|
| worker_failed | 리포트를 읽고 지시로 풀 수 있으면 `orch fix T-xxx --note "<원인과 지시>"`. 아니면 사용자에게 묻고 결과대로 fix 하거나 `orch drop T-xxx --reason "<이유>"` |
| worker_question | `docs/planning/spec.md`, `contracts.md`, `interview.md`, `voice.md`, 태스크 spec, 직무 정의, `.orchestration/project.md` 로 먼저 답한다. 문서가 다루지 않는 제품 결정이거나 문서끼리 모순될 때만 사용자에게 묻는다. 코드에 아직 없는 디자인 토큰을 물으면 토큰을 반영한 태스크가 done 이 된 뒤 그 토큰 이름으로 답한다 |
| escalation | 내용을 정리해 지시로 풀거나 사용자에게 묻는다 |
| max_rounds | 리뷰 요약과 함께 "한 번 더 / 중단"을 묻는다. 한 번 더면 `orch fix T-xxx --extra-round`, 중단이면 `orch drop` |
| repeat_test_failure | 실패가 그 태스크 탓이 아닐 수 있다(소유 경로 밖 코드, 다른 워커의 커밋 안 된 파일). 리뷰 파일의 quick 출력 끝을 보고 사용자에게 묻는다 |
| test_config | 사용자가 직접 넣은 `[test]` 면 사용자에게 묻는다. 아니면 `[test]` 를 고치는 기획 태스크를 만들어 시작한다(위 "직접 만든 태스크" 규칙) |
| planning_gaps | 문서로 답할 수 있으면 묶음 정리 태스크 spec 에 넣는다(시작 전이면 spec 커밋, working 이면 `orch scope`). 제품 결정이 필요한 항목은 모아 한 번에 묻고, 넣기로 하면 증분 기획 태스크(`plan: append`)를 만들어 시작한다 |
| unknown_regression | 원인 태스크를 정해(모르면 사용자에게 묻는다) 회귀 수정 developer 태스크를 만들어 시작한다. front matter `owns:` 는 원인 태스크와 같게, Change 첫 줄은 decide 의 `source`(예: `출처: T-xxx followups`), Acceptance 는 "실패한 테스트 <이름>이 `orch test T-yyy` 에서 통과한다". spec 을 커밋하고 `orch start T-yyy` 한 뒤 `orch answer` 로 닫는다 |
| token_update | 보강한 디자인 시스템의 토큰을 UI 기반 developer 태스크에 반영할지 정한다. 그 태스크가 done 이면 `orch task new developer "토큰 갱신 반영: <요약>"`(owns 는 UI 기반 태스크와 같게, `depends_on:` 에 보강 태스크. 커밋하고 `orch start T-yyy`), working 이면 `orch scope`, 리뷰 중이면 결과를 기다린 뒤 정한다 |

### 질문 표시 규칙
- `render.context` 가 있으면 그 내용을 먼저 사용자에게 평문으로 그대로 보여 준 뒤 AskUserQuestion 을 부른다(선택지가 없으면 평문 질문 앞에 둔다). 요약하거나 자기 설명을 덧붙이지 않는다. 보여 주지 않고 부르면 훅이 그 질문을 한 번 거부하고 context 를 돌려준다.
- 메시지에 `render` 가 있으면(ask, approval, question) 그대로 AskUserQuestion 에 넘긴다. 질문은 1개, 단일 선택(`multiSelect: false`)이다. question 은 `render.question`, header 는 `render.header`, 선택지는 `render.options` 의 label·description 을 순서 그대로 쓴다.
- `render.options` 가 없으면 AskUserQuestion 을 쓰지 않고 `render.question`(질문과 예시)을 평문으로 묻는다. 사용자가 채팅으로 답하면 그 내용을 그대로 `orch answer` 로 넘긴다.
- 사용자가 선택지를 고르면 고른 label 을, Other 로 자유 입력하면 그 내용을 그대로 넘긴다.
- `render` 가 없는 legacy question(예전 평문 질문)만 본문을 AskUserQuestion 질문 하나로 묻는다. 본문에 선택지가 있으면 그대로 옮긴다.
- 질문을 바꾸거나 합치지 않고, 설명을 덧붙이지 않는다. 메시지 여러 개가 한꺼번에 오면 하나씩 따로 묻는다.

### 위치
- 설정: `.orchestration/config.toml`(고정 에이전트 `[agents]`, 직무별 모델·effort·소유 경로, `max_parallel`, `[overflow]`, `user_approval_roles`)
- 기획: `docs/planning/`(spec.md, contracts.md, tasks.toml, interview.md, voice.md)
- 문구: `.orchestration/text-rules.md`(작성·리뷰 공통 규칙), `docs/planning/voice.md`(planner 가 정한 문체·호칭·라벨·용어집)
- 태스크 spec: `.orchestration/tasks/`, 워커 리포트: `docs/tasks/`, Codex 리뷰: `docs/reviews/`
- 디자인 방향·시스템: `docs/design/direction.md`(`design_system_url:` 줄), `docs/design/system/`(director 가 Claude Design 으로 만든다)
- 디자인: 앱·웹 화면은 Claude Design(소스 `docs/design/`), 래스터 에셋은 Codex `/orca-team:image`(`assets/`)
- 관제 기록: `.orchestration/state/drive/drive.log`, 고정 에이전트 상태: `.orchestration/state/agents.json`, 인수인계: `docs/handoff/<에이전트>.md`
