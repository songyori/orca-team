---
name: up
description: >
  orca-team 프로젝트의 관제(orch drive)와 메시지 watcher 를 되살리는 수동 복구. 관제는 보통 메인 세션 훅이 자동으로 살린다(config.toml [drive] auto_up). 자동 부활이 꺼져 있거나 훅이 안내했을 때 쓴다.
  트리거: "/orca-team:up", SessionStart 안내.
---

# /orca-team:up

이 세션은 메인이다.

1. `.orchestration/bin/orch status` 를 실행한다.
2. `drive.alive` 가 true 가 아니면 `.orchestration/bin/orch up --wait-run 180` 을 Bash `timeout: 240000` 으로 실행한다(Run 생성을 최대 180초 기다리므로 기본 2분 제한에 걸리지 않게 한다).
3. `watch_alive` 가 false 면 Bash `run_in_background: true` 로 `.orchestration/bin/orch watch` 를 띄운다. `unread_outbox` 가 0 보다 크면 watch 가 바로 끝나며 밀린 메시지를 준다.
4. `open_questions`(답하지 않은 질문과 decide)가 비어 있지 않으면 `.orchestration/bin/orch reask` 를 실행한다. 출력은 watch 와 같은 형식의 메시지 배열이다. CLAUDE.md 의 "메인 규칙" 대로 하나씩 처리한다.
5. 보고한다: 관제 상태, 고정 에이전트 표(이름, 맡은 태스크, 대기열, 리셋 예정), 진행 중 태스크(id, 직무, 상태, 라운드) 표.
