> orca-team 내장 Codex 래퍼(`orca-team/scripts/codex-run.sh`)와 이미지 생성(`/orca-team:image`)의 설계 문서다. orca-team 에는 `run` 스킬이 없다. 본문의 `run` 스킬 이야기는 설계 당시 기록이다.

# 05 — 검사

두 층이다. **가짜 codex 회귀 테스트**는 래퍼의 계약을 지키고, **실물 스파이크**는 가짜가 흉내 못 내는
것을 잡는다. 하나로 다른 하나를 대신하지 않는다 — 이전 프로젝트는 44개 테스트가 초록인 채로 실환경 첫 호출이
100% 실패했다(가짜 codex 가 stdin 을 읽지 않았다).

## 1. 회귀 테스트 `orca-team/tests/test-codex-run.sh`

시드는 이전 프로젝트의 래퍼 회귀 테스트(80 단언)다. 가짜 `codex` 를 PATH 앞에 붙여 전 경로를 탄다.
프레임워크 없음, bash 3.2, `mktemp` + trap 정리. `02` 의 "이전 래퍼와 다른 점" 에 맞춰 고칠 것:

| 시드 | 고칠 것 |
|---|---|
| `--role develop\|spec\|design\|plan` 케이스 | `--profile run\|image` 두 케이스. 기본값이 `heartbeat < timeout` 을 만족하는지 |
| `--model` 누락 → exit 2 | `--model` 없음 → argv 에 `--model` 이 **없음**을 단언. 있으면 그대로 전달 |
| cwd 대조 | `--cwd` 가 argv 의 `-C <dir>` 로 전달됨. `status.json` 에 `cwd` 기록 |
| (없음) | `--image a.png --image b.png` → argv 에 `-i a.png -i b.png` |
| (없음) | `status.json` 에 `profile`·`codex_version` 필드 |

**바꾸지 않는 단언** (보안·관측 계약 — 이것이 깨지면 테스트가 깨져야 한다):

- 우회 플래그·`--skip-git-repo-check`·`--json`·`--output-last-message` 가 argv 에 실재
- stdin 이 `/dev/null`(chardev) — 가짜 codex 가 `/dev/stdin` 의 파일 종류를 **기록만** 하고 읽지 않는다. 읽으면 상속된 stdin 에서 테스트 자체가 멈춘다
- 환경이 상속된다(마커 환경변수)
- `result.txt` 에 JSONL 이 섞이지 않는다
- exit 0 인데 `turn.failed` / 턴 완료 후 프로세스가 안 죽어 exit 11 인데 `terminal_event`·`result.txt` 가 살아남는다 / SIGTERM 에서도 `terminal_event` 보존
- 타임아웃·무응답 후 프로세스 그룹 잔여 0
- exit 2 는 `status.json` 을 만들지 않는다

가짜 codex 는 **진짜가 하는 일을 한다**: stdout 에 compact JSONL(`thread.started` → `turn.started` → `item.completed` → `turn.completed`), `-o` 파일에 최종 메시지, `--version` 에 버전 문자열. 실물의 이벤트 이름이 바뀌면 가짜도 바꾼다.

완료 조건: `bash orca-team/tests/run.sh --wrapper` 가 exit 0(마지막 줄 `N개 통과 / 0개 실패`), 단언 수가 시드(80) 이상.

## 2. 실물 스파이크 — `run`

래퍼를 통해 **읽기 전용** 작업을 한 번 돌린다. `codex exec` 를 직접 치면 플러그인이 실제로 쓰는 경로를 검증하지 못한다.

```bash
RUN="$(mktemp -d)/run"; mkdir -p "$RUN"
cat > "$RUN/prompt.txt" <<'PROMPT'
점검용 실행이다. 아무것도 수정하지 마라. 현재 작업 디렉터리의 절대경로와 codex 버전을 알려주고,
네가 이 머신에서 도는 Codex 임을 한 줄로 확인하라.
PROMPT
"${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh" --profile run --cwd "$PWD" \
  --prompt-file "$RUN/prompt.txt" --run-dir "$RUN" --effort low --timeout 120 --heartbeat 60
echo "exit=$?"
```

확인 항목(전부 통과해야 한다). **2026-09-06 전부 통과** — exit 0, 20초, 이벤트 10줄, `codex-cli 0.153.4`:

- [x] exit 0, `status.json` 의 `terminal_event` 가 `"turn.completed"`
- [x] `result.txt` 에 실제 응답, JSONL 없음
- [x] `events.jsonl` 에 `"type":"turn.completed"` 가 있다 — 이벤트 이름 계약 확인. `codex --version` 과 함께 기록
- [x] `events.jsonl` 이 자랐다(`progress.log` 는 거의 비는 것이 정상)
- [x] `-c model_reasoning_effort=low` 가 오류 없이 수용된다 — `--json` 모드에서는 stderr 헤더도 이벤트도 model/effort 를 찍지 않는다(0.153.4 실측). 잘못된 값은 codex 가 시작 단계에서 거부하므로(`06` #13) 수용 자체가 근거다
- [x] `-C` 로 준 경로를 codex 가 작업 디렉터리로 보고했다
- [x] `hook:` 줄이 보이면 기록한다(사용자 훅이 안에서 돈다)

그 다음 **쓰기 스파이크** (2026-09-06 통과 — 알림 도착, `M README.md`, 요약 일치, 30초): 임시 git 저장소에서 "README.md 에 한 줄 추가" 를 `run` 프로파일 기본값으로 백그라운드 실행. 확인: 완료 알림이 실제로 온다, `git status --porcelain` 에 변경이 있다, `result.txt` 요약이 변경과 일치한다, 비대화 문장이 있어 "진행할까요?" 로 끝나지 않는다.

## 3. 실물 스파이크 — `image`

1장만. 쿼터를 쓴다. **2026-09-06 셋 다 통과** — 46초 / 투명 76초(`hasAlpha: yes`) / 편집 55초(초록 사과, 구도 유지). 레이아웃 `<thread-id>/exec-<uuid>.png`.

```
/orca-team:image --out /tmp/orca-team-image-spike 단색 배경 위의 빨간 사과 하나, 스튜디오 조명
```

확인 항목:

- [x] exit 0, `turn.completed`
- [x] `result.txt` 마지막 줄이 절대경로이고 그 파일이 실재한다
- [x] `/tmp/orca-team-image-spike/` 에 복사됐다. `sips -g pixelWidth -g pixelHeight` 가 찍힌다
- [x] 소요 시간 기록(하트비트 300 이 충분한지)
- [x] `$CODEX_HOME/generated_images/` 의 실제 레이아웃 기록(평면인가 세션 하위인가)

두 번째: `--transparent --ar 1:1` 로 1장. `hasAlpha` 확인. 안 되면 크로마키 후처리 경로 확인.
세 번째(편집, `00` D11): `--ref` 로 첫 사과를 첨부해 "사과를 초록색으로" 편집.

## 4. `orch doctor` 검사 항목

본체는 `orca-team/lib/orch/setup.py` 의 `doctor()` 다. 실행: `python3 orca-team/bin/orch doctor`(프로젝트에서는 `.orchestration/bin/orch doctor`). 표와 코드가 어긋나면 둘 다 고친다.
아래는 Codex 관련 항목이다. 그 밖에 python ≥3.11, git 저장소(경고), orca runtime 을 본다.

| 항목 | 명령 | 판정 |
|---|---|---|
| 설치·로그인 | `codex login status` | codex 가 없거나 `Logged in` 이 아니면 차단 |
| 이미지 feature | `codex features list` 의 `image_generation` 행 | 마지막 열이 `true` 가 아니면 **경고**(`/orca-team:image` 만 막힘), `codex features enable image_generation` 안내 |
| Codex 래퍼 | 내장 `scripts/codex-run.sh` 파일·실행 권한·`--bypass-hook-trust` 지원 | 아니면 차단(플러그인 설치가 깨졌다) |

설계 당시 점검(doctor) 목록에 있던 회귀 테스트·실물 스파이크·이벤트 이름·사용자 설정 개입·run-dir 항목은 `orch doctor` 에 없다. 회귀 테스트는
`bash orca-team/tests/run.sh --wrapper` 로, 스파이크·이벤트 이름·사용자 설정 개입은 §2 스파이크를 손으로 돌려 확인한다.
"샌드박스가 꺼진 상태로 돈다" 는 README 보안 주의에 있다. 시험 이미지 생성은 하지 않는다.

## 5. 트리거 시험 문장

설명문을 고칠 때마다 새 세션에서 아래 문장을 하나씩 넣고 어느 스킬이 뜨는지 본다.
orca-team 에는 `run` 스킬이 없다. 기대가 `run` 인 행은 해당 없음(아무것도 안 뜨는 것이 정상)이고, `image` 는 `/orca-team:image` 다.

| 문장 | 기대 |
|---|---|
| "codex 한테 로그인 버그 고치라고 해" | `run` |
| "이거 코덱스로 돌려 줘" | `run` |
| "로그인 버그 고쳐 줘" | 아무것도 안 뜸 |
| "이 diff 리뷰해 줘" | 아무것도 안 뜸 (사용자가 `/codex:review` 를 고른다) |
| "앱 아이콘용 여우 일러스트 만들어 줘" | `image` |
| "히어로 배경 이미지 3장 뽑아 줘" | `image`, 확인 없이 3회 호출(백그라운드) |
| "이 로고를 SVG 로 그려 줘" | 아무것도 안 뜸 |
| "OPENAI API 로 gpt-image-2 호출해서 만들어" | 아무것도 안 뜸 (gpt-image 플러그인) |

## 6. 문서·스크립트 일치

래퍼 헤더 주석, `02`, 스킬 본문의 명령 예시가 같은 플래그 집합을 말하는지 `grep` 으로 대조한다. 한 자리를 고치면 세 자리를 본다. 이전 프로젝트 결함의 대부분이 "고친 자리가 새로 만든 어긋남" 이었다.
