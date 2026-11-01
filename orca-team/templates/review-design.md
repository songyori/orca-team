## 디자인·UI 기준
5. UI 를 바꾼 태스크면 `.orchestration/design-rules.md`(AI 티 금지 목록)를 읽고 바뀐 화면·코드가 지키는지 본다. 위반은 REQUEST_CHANGES 사유다. 워커 리포트 마지막 라운드의 `### 금지 목록 점검` 이 실제와 맞는지도 본다.
6. 디자인 시스템(`docs/design/system/tokens.json`, `docs/design/system/README.md`)이 있으면 그것을 기준으로 본다. designer 는 시스템에 없는 색·크기·간격·컴포넌트를 새로 만들지 않았는가(필요하면 리포트 마지막 라운드의 `### 시스템 추가 제안` 에 적어야 한다). developer 는 화면 코드가 토큰과 공통 컴포넌트만 쓰고 색·크기·간격·모서리·그림자를 하드코딩하지 않았는가.

orch 자동 검사(의심 위치. 블록 인용처럼 정당한 쓰임도 있으니 직접 열어 보고 판단한다):
{design_lint}
