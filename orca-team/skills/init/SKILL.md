---
name: init
description: >
  새 프로젝트에 orca-team 오케스트레이션(메인/관제/고정 에이전트, Codex 리뷰)을 세팅한다.
  트리거: "/orca-team:init <프로젝트 설명>". 설명 없이 부르면 한 줄로 묻는다.
argument-hint: "<프로젝트 설명>"
---

# /orca-team:init

이 세션은 **메인**이다. 단계 순서를 바꾸지 않는다. 명령은 적힌 그대로 실행한다.

아래 `ORCH` 는 `python3 "${CLAUDE_PLUGIN_ROOT}/bin/orch"` 다. 3단계 이후에는 프로젝트 shim `.orchestration/bin/orch` 를 쓴다.

## 1. 설명 확인
인자: `$ARGUMENTS`

비어 있으면 "어떤 프로젝트인지 한두 문장으로 알려 달라"고 묻고 멈춘다.

## 2. 점검
`ORCH doctor` 를 실행한다.
- `warn git 저장소` 면 `git init` 후 `git commit --allow-empty -m "chore: init"` 을 실행한다.
- `FAIL` 이 있으면 출력 표와 각 detail 의 고칠 방법을 보여주고 멈춘다.
- `FAIL Codex 래퍼` 면 orca-team 플러그인 설치가 깨진 것이다. orca-team 을 다시 설치하라고 안내한다(`/plugin uninstall orca-team@setup-with-orca` 다음 `/plugin install orca-team@setup-with-orca`).
- `warn codex image_generation` 은 멈추지 않는다. 이미지 생성(`/orca-team:image`)만 막히고 `codex features enable image_generation` 으로 켠다는 것을 6단계 보고에 적는다.

## 3. 스캐폴드
설명 원문을 스크래치 파일에 쓰고 `ORCH scaffold --description-file <그 파일>` 을 실행한다. 출력의 created·skipped 를 기억한다.

## 4. 설치 목록 제안 — 판단이 들어가는 유일한 단계
프로젝트 설명과 저장소 상태를 보고 표 하나를 만든다.

| 구분 | 이름 | 이유 | 범위 | 명령·변경 |
|---|---|---|---|---|

- 구분은 다섯 가지다: 스킬, 플러그인, 프로그램, 직무 owns, 추가 직무.
- 이미 설치된 것은 넣지 않는다. `claude plugin list`, `which <도구>`, `brew list` 로 확인한다.
- 직무 owns: 프로젝트 구조에 맞춘 `.orchestration/config.toml` 의 `[roles.*].owns` 값이다. 예: Next.js 면 developer `["src/**", "app/**", "package.json"]`.
- 추가 직무: 기본 5개(planner·director·designer·developer·tester)로 부족할 때만 넣는다. `.orchestration/roles/<role>.md`(기본 직무 파일과 같은 절 구성)와 config 항목을 같이 만든다.
- 텔레그램 등 채널은 넣지 않는다.
- AskUserQuestion 으로 한 번에 승인받는다. 옵션: "전부 승인(Recommended)", "설치 없이 진행". 일부 제외는 Other 로 받는다.
- 승인된 항목을 설치·적용한다. 실패해도 멈추지 않고 표에 결과(성공/실패)를 남긴다.

## 5. 관제 기동
1. `.orchestration/bin/orch up --wait-run 180` 을 Bash `timeout: 240000` 으로 실행한다(Run 생성을 최대 180초 기다리므로 기본 2분 제한에 걸리지 않게 한다).
2. Bash `run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다.
   - 이후 watch 가 끝날 때마다(메시지 도착) CLAUDE.md 의 "메인 규칙" 대로 처리하고 다시 띄운다.

## 6. 마무리
1. `.orchestration/bin/orch setup-done` 을 실행한다.
2. 세팅 파일을 경로를 지정해 커밋한다. `<경로>` 는 `CLAUDE.md AGENTS.md .gitignore .orchestration` 과 4단계에서 바뀐 설정 파일이다: `git add -- <경로> && git commit -m "chore: orca-team 세팅" -- <경로>`. 관제·워커와 인덱스를 같이 쓰므로 경로 없는 `git commit` 은 쓰지 않는다.
3. 이 세션은 CLAUDE.md 가 만들어지기 전에 시작해 메인 규칙이 컨텍스트에 없다. 사용자의 첫 요청을 받기 전에 CLAUDE.md 의 `<!-- orca-team -->` 절을 읽는다(사용자가 `/clear` 로 새 세션을 열어도 된다). 보고에 이 사실을 한 번 적는다.
4. 보고한다: 준비 완료, 관제 탭(orca-team 관제) handle, 설치 결과 표, "다음 메시지로 첫 업무를 주면 된다".

## 하지 않는 것
- `orca orchestration` 직접 호출. 메인이 Run 에 묶이면 Orca 알림이 사용자 입력란에 들어온다.
- 기획·디자인·코드 작업 시작. 첫 업무는 사용자의 다음 메시지부터다.
