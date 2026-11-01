#!/usr/bin/env bash
# test-codex-run.sh — codex-run.sh 회귀 테스트. 프레임워크 없음.
# 대상은 orca-team/scripts/codex-run.sh 다.
#
# 가짜 codex 를 PATH 앞에 붙여 실제 codex 없이 전 경로를 탄다.
# 가짜는 진짜가 하는 일을 한다 — stdout 에 compact JSONL, -o 파일에 최종 메시지,
# --version 에 버전 문자열. 진짜의 이벤트 이름이 바뀌면 가짜도 바꾼다(docs/codex/05 §1).
# 실행: bash orca-team/tests/test-codex-run.sh   (전부 통과 시 exit 0)
#       또는 bash orca-team/tests/run.sh --wrapper (Python 테스트 뒤에 이어서 돈다)

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$(cd "$SCRIPT_DIR/../scripts" && pwd)/codex-run.sh"

PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
nope() { FAIL=$((FAIL+1)); printf '  FAIL %s\n       기대: %s\n       실제: %s\n' "$1" "$2" "$3"; }

assert_eq() {  # $1=이름 $2=기대 $3=실제
  if [ "$2" = "$3" ]; then ok "$1"; else nope "$1" "$2" "$3"; fi
}

assert_file_contains() {  # $1=이름 $2=파일 $3=문자열
  if [ -f "$2" ] && grep -qF -- "$3" "$2"; then
    ok "$1"
  else
    nope "$1" "'$3' 포함" "미포함 또는 파일 없음: $2"
  fi
}

assert_file_lacks() {  # $1=이름 $2=파일 $3=문자열
  if [ -f "$2" ] && grep -qF -- "$3" "$2"; then
    nope "$1" "'$3' 미포함" "포함: $2"
  else
    ok "$1"
  fi
}

# 플래그 부재는 줄 전체 일치로 본다 — 부분 문자열이면 --skip-git-repo-check 의 "-c" 에 오탐한다.
assert_argv_lacks() {  # $1=이름 $2=argv 파일 $3=플래그
  if [ -f "$2" ] && grep -q -x -F -- "$3" "$2"; then
    nope "$1" "'$3' 미포함" "포함: $2"
  else
    ok "$1"
  fi
}

json_field() {  # $1=status.json $2=키 — 값만 뽑는다(따옴표·쉼표 제거)
  grep "\"$2\":" "$1" 2>/dev/null | head -1 | sed -e 's/^[^:]*: *//' -e 's/[",]//g' -e 's/[[:space:]]*$//'
}

# argv 파일은 한 줄에 인자 하나다. 플래그 다음 줄이 그 값이다.
argv_after() {  # $1=argv 파일 $2=플래그 → 첫 번째 값
  grep -A1 -x -F -- "$2" "$1" 2>/dev/null | sed -n '2p'
}

if [ ! -x "$RUNNER" ]; then
  echo "codex-run.sh 가 없거나 실행 권한이 없다: $RUNNER" >&2
  exit 1
fi

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"' EXIT

FAKE_BIN="$TMPROOT/bin"
mkdir -p "$FAKE_BIN" "$TMPROOT/empty"

cat > "$FAKE_BIN/codex" <<'FAKE'
#!/usr/bin/env bash
# 진짜 codex 처럼 군다: stdout 은 --json 의 JSONL, 최종 메시지는 --output-last-message 파일,
# --version 은 버전 문자열 한 줄.
if [ "${1:-}" = "--version" ]; then
  echo "codex-cli 9.9.9-fake"
  exit 0
fi
LAST=""; OUTFILE=""; SCHEMA=""; prev=""
for a in "$@"; do
  case "$prev" in
    --output-last-message) OUTFILE="$a" ;;
    --output-schema)       SCHEMA="$a" ;;
  esac
  prev="$a"; LAST="$a"
done
printf '%s\n' "$@" > "$FAKE_ARGV"
printf '%s' "$LAST" > "$FAKE_PROMPT"
printf '%s' "${CODEX_RUN_TEST_MARKER:-<unset>}" > "$FAKE_ENV"
printf '%s' "$SCHEMA" > "$FAKE_SCHEMA"
pwd > "$FAKE_PWD"
ps -o pgid= -p $$ | tr -d ' ' > "$FAKE_PGID"
# stdin 이 무엇으로 열려 있는지 기록한다. /dev/null 이면 chardev.
# 읽어서 판정하지 않는다 — 상속된 stdin 이면 읽기가 블록되고 테스트가 멈춘다.
SD="unknown"
[ -c /dev/stdin ] && SD="chardev"
[ -p /dev/stdin ] && SD="pipe"
[ -f /dev/stdin ] && SD="file"
[ -t 0 ] && SD="tty"
printf '%s' "$SD" > "$FAKE_STDIN"
emit_done() {
  printf '%s\n' '{"type":"thread.started","thread_id":"t-fake"}'
  printf '%s\n' '{"type":"turn.started"}'
  printf '%s\n' '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"FAKE_RESULT_OK"}}'
  printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
  [ -n "$OUTFILE" ] && printf 'FAKE_RESULT_OK\n' > "$OUTFILE"
  return 0
}
case "${FAKE_MODE:-ok}" in
  ok)       emit_done; exit 0 ;;
  # 프로세스는 정상 종료했지만 턴 자체는 실패했다. 종료 코드로는 구분되지 않는다.
  turnfail) printf '%s\n' '{"type":"thread.started","thread_id":"t-fake"}'
            printf '%s\n' '{"type":"turn.started"}'
            printf '%s\n' '{"type":"turn.failed","error":{"message":"fake turn failure"}}'
            exit 0 ;;
  fail)     echo "boom" >&2; exit 3 ;;
  # 턴은 끝났는데 프로세스가 안 죽는다 — 실측된 "완성품을 버릴 뻔한" 경로.
  donehang) emit_done; while :; do sleep 1; done ;;
  chatty)   sleep 300 & sleep 300 & while :; do printf '%s\n' '{"type":"item.started","item":{"id":"i","type":"command_execution"}}'; sleep 1; done ;;
  silent)   sleep 300 & while :; do sleep 1; done ;;
esac
FAKE
chmod +x "$FAKE_BIN/codex"

export PATH="$FAKE_BIN:$PATH"
export FAKE_ARGV="$TMPROOT/argv.txt"
export FAKE_PROMPT="$TMPROOT/prompt-received.txt"
export FAKE_ENV="$TMPROOT/env.txt"
export FAKE_PGID="$TMPROOT/pgid.txt"
export FAKE_STDIN="$TMPROOT/stdin-kind.txt"
export FAKE_SCHEMA="$TMPROOT/schema-received.txt"
export FAKE_PWD="$TMPROOT/pwd-received.txt"
export CODEX_RUN_TEST_MARKER="marker-8f3a"

# 인용부호가 깨지기 쉬운 프롬프트. 파일 방식을 도입한 이유가 이것이다.
# 후행 개행을 일부러 둔다 — 명령 치환이 이를 제거하는 것이 정상 동작임을 검증한다.
PROMPT_FILE="$TMPROOT/prompt.txt"
printf '%s\n\n' 'quote " backtick ` dollar $HOME 그리고 한글
둘째 줄 $(whoami) 세미콜론; 파이프| 끝' > "$PROMPT_FILE"

LAST_BYTE="$(tail -c1 "$PROMPT_FILE" | od -An -tx1 | tr -d ' \n')"
assert_eq "픽스처: 프롬프트 파일이 후행 개행으로 끝난다" "0a" "$LAST_BYTE"

# 테스트가 도는 cwd. 래퍼의 --cwd 기본값이다.
HERE="$(pwd -P)"

echo "== 1. 정상 종료 =="
RD="$TMPROOT/run-ok"
FAKE_MODE=ok "$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
RC=$?
assert_eq "정상: exit 0" "0" "$RC"
assert_eq "정상: state=ok" "ok" "$(json_field "$RD/status.json" state)"
assert_eq "정상: exit 필드 0" "0" "$(json_field "$RD/status.json" exit)"
assert_eq "정상: raw_exit 0" "0" "$(json_field "$RD/status.json" raw_exit)"
assert_eq "정상: profile 기록" "run" "$(json_field "$RD/status.json" profile)"
assert_eq "정상: cwd 기록(기본값 = 호출 cwd)" "$HERE" "$(json_field "$RD/status.json" cwd)"
assert_eq "정상: codex_version 기록" "codex-cli 9.9.9-fake" "$(json_field "$RD/status.json" codex_version)"
assert_file_contains "정상: result.txt 에 최종 메시지" "$RD/result.txt" "FAKE_RESULT_OK"
[ -f "$RD/progress.log" ] && ok "정상: progress.log 생성" || nope "정상: progress.log 생성" "존재" "없음"
assert_file_contains "정상: events.jsonl 에 이벤트 스트림" "$RD/events.jsonl" '"type":"turn.completed"'
assert_eq "정상: terminal_event 기록" "turn.completed" "$(json_field "$RD/status.json" terminal_event)"
# result.txt 는 --output-last-message 가 만든다. JSONL 이 섞이면 소비자가 전부 깨진다.
if grep -q '"type":' "$RD/result.txt" 2>/dev/null; then
  nope "정상: result.txt 에 JSONL 이 섞이지 않는다" "최종 메시지만" "이벤트 혼입"
else
  ok "정상: result.txt 에 JSONL 이 섞이지 않는다"
fi

echo "== 2. 보안·관측 계약 (회귀 가드) =="
assert_file_contains "우회 플래그가 실제 argv 에 있다" "$FAKE_ARGV" "--dangerously-bypass-approvals-and-sandbox"
assert_file_contains "--skip-git-repo-check 가 argv 에 있다" "$FAKE_ARGV" "--skip-git-repo-check"
# stdin 을 상속시키면 codex 가 "Reading additional input from stdin..." 에서 EOF 를
# 기다리며 작업을 시작하지 못하고, 하트비트가 정상 작업을 오살한다. 실측으로 확인된 결함이다.
assert_eq "stdin 이 /dev/null 로 열려 있다" "chardev" "$(cat "$FAKE_STDIN" 2>/dev/null)"
assert_file_contains "exec 서브커맨드" "$FAKE_ARGV" "exec"
# 관측 계약: 이 둘은 한 몸이다. --json 만 떼면 events.jsonl 이 비어 종단 판정이 죽고,
# --output-last-message 만 떼면 result.txt 가 JSONL 로 오염된다.
assert_file_contains "--json 이 argv 에 있다" "$FAKE_ARGV" "--json"
assert_file_contains "--output-last-message 가 argv 에 있다" "$FAKE_ARGV" "--output-last-message"
assert_eq "--model 전달" "m-alpha" "$(argv_after "$FAKE_ARGV" --model)"
assert_file_contains "effort 전달" "$FAKE_ARGV" "model_reasoning_effort=high"
# -C 는 codex 의 작업 루트다. 빠뜨리면 codex 가 엉뚱한 저장소를 보면서 정상 종료한다.
assert_eq "-C 로 cwd 전달(기본값)" "$HERE" "$(argv_after "$FAKE_ARGV" -C)"
assert_eq "환경이 상속된다" "marker-8f3a" "$(cat "$FAKE_ENV")"
EXPECTED_PROMPT="$TMPROOT/prompt-expected.txt"
printf '%s' "$(cat "$PROMPT_FILE")" > "$EXPECTED_PROMPT"
if diff -q "$EXPECTED_PROMPT" "$FAKE_PROMPT" >/dev/null 2>&1; then
  ok "프롬프트가 후행 개행만 제거되고 그대로 전달된다"
else
  nope "프롬프트가 후행 개행만 제거되고 그대로 전달된다" "후행 개행 제거 후 원본과 동일" "$(diff "$EXPECTED_PROMPT" "$FAKE_PROMPT" | head -3)"
fi

echo "== 2-1. 선택 인자는 없으면 붙지 않는다 (docs/codex/00 D5) =="
RD="$TMPROOT/run-nomodel"
FAKE_MODE=ok "$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--model·--effort 없음: exit 0" "0" "$?"
assert_argv_lacks "--model 없음: argv 에 --model 이 없다" "$FAKE_ARGV" "--model"
assert_file_lacks "--effort 없음: argv 에 model_reasoning_effort 가 없다" "$FAKE_ARGV" "model_reasoning_effort"
assert_argv_lacks "--effort 없음: argv 에 -c 가 없다" "$FAKE_ARGV" "-c"
assert_argv_lacks "--bypass-hook-trust 없음: argv 에 훅 신뢰 우회가 없다" "$FAKE_ARGV" "--dangerously-bypass-hook-trust"

echo "== 2-1b. --bypass-hook-trust 는 줄 때만 붙는다 =="
RD="$TMPROOT/run-hooktrust"
FAKE_MODE=ok "$RUNNER" --profile run --bypass-hook-trust --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--bypass-hook-trust: exit 0" "0" "$?"
assert_file_contains "--bypass-hook-trust: argv 에 --dangerously-bypass-hook-trust" "$FAKE_ARGV" "--dangerously-bypass-hook-trust"
assert_file_contains "--bypass-hook-trust: 우회 플래그 유지" "$FAKE_ARGV" "--dangerously-bypass-approvals-and-sandbox"

echo "== 2-2. --cwd 와 -C =="
WORK="$TMPROOT/work-dir"; mkdir -p "$WORK"
WORK_REAL="$(cd "$WORK" && pwd -P)"
RD="$TMPROOT/run-cwd"
FAKE_MODE=ok "$RUNNER" --profile run --cwd "$WORK" \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--cwd 지정: exit 0" "0" "$?"
assert_eq "--cwd 지정: argv 의 -C 값" "$WORK_REAL" "$(argv_after "$FAKE_ARGV" -C)"
assert_eq "--cwd 지정: status.json 에 cwd 기록" "$WORK_REAL" "$(json_field "$RD/status.json" cwd)"
"$RUNNER" --profile run --cwd "$TMPROOT/no-such-dir" \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--cwd 가 없는 디렉터리: exit 2" "2" "$?"

echo "== 2-3. --image 와 -i (docs/codex/03 편집 경로) =="
IMG_A="$TMPROOT/a.png"; IMG_B="$TMPROOT/b.png"
printf 'PNG' > "$IMG_A"; printf 'PNG' > "$IMG_B"
RD="$TMPROOT/run-image"
FAKE_MODE=ok "$RUNNER" --profile image --image "$IMG_A" --image "$IMG_B" \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--image 둘: exit 0" "0" "$?"
IMG_ARGS="$(grep -A1 -x -F -- '-i' "$FAKE_ARGV" | grep -v -x -F -- '-i' | grep -v -x -F -- '--' | tr '\n' ' ' | sed 's/ $//')"
assert_eq "--image 둘: argv 에 -i a -i b 순서대로" "$IMG_A $IMG_B" "$IMG_ARGS"
RD="$TMPROOT/run-noimage"
FAKE_MODE=ok "$RUNNER" --profile image --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_argv_lacks "--image 없음: argv 에 -i 가 없다" "$FAKE_ARGV" "-i"
"$RUNNER" --profile image --image "$TMPROOT/no-such.png" \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "없는 --image 파일: exit 2" "2" "$?"

echo "== 3. 비정상 종료 =="
RD="$TMPROOT/run-fail"
FAKE_MODE=fail "$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
RC=$?
assert_eq "비정상: exit 1" "1" "$RC"
assert_eq "비정상: state=failed" "failed" "$(json_field "$RD/status.json" state)"
assert_eq "비정상: raw_exit 보존" "3" "$(json_field "$RD/status.json" raw_exit)"
assert_file_contains "비정상: stderr 가 progress.log 로" "$RD/progress.log" "boom"
assert_eq "비정상: 종단 이벤트 없음" "null" "$(json_field "$RD/status.json" terminal_event)"

echo "== 4. 전체 타임아웃 =="
RD="$TMPROOT/run-timeout"
FAKE_MODE=chatty "$RUNNER" --profile run --model m-alpha --effort xhigh \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 4 --heartbeat 3 >/dev/null 2>&1
RC=$?
assert_eq "타임아웃: exit 10" "10" "$RC"
assert_eq "타임아웃: state=timeout" "timeout" "$(json_field "$RD/status.json" state)"
TPID="$(json_field "$RD/status.json" pid)"
RECORDED_PGID="$(cat "$FAKE_PGID" 2>/dev/null | tr -d ' \n')"
assert_eq "타임아웃: 자식 pgid 가 status.json pid 와 같다(set -m 동작 확인)" "$TPID" "$RECORDED_PGID"
if kill -0 "$TPID" 2>/dev/null; then
  nope "타임아웃: 프로세스 정리됨" "죽어 있음" "pid $TPID 살아 있음"
else
  ok "타임아웃: 프로세스 정리됨"
fi
if command -v pgrep >/dev/null 2>&1; then
  LEFT="$(pgrep -g "$TPID" 2>/dev/null | tr -d ' \n')"
  if [ -z "$LEFT" ]; then ok "타임아웃: 프로세스 그룹 잔여 없음"
  else nope "타임아웃: 프로세스 그룹 잔여 없음" "없음" "$LEFT"; fi
fi

echo "== 5. 하트비트 무응답 =="
RD="$TMPROOT/run-stalled"
FAKE_MODE=silent "$RUNNER" --profile run --model m-alpha --effort xhigh \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 60 --heartbeat 3 >/dev/null 2>&1
RC=$?
assert_eq "무응답: exit 11" "11" "$RC"
assert_eq "무응답: state=stalled" "stalled" "$(json_field "$RD/status.json" state)"
SPID="$(json_field "$RD/status.json" pid)"
if kill -0 "$SPID" 2>/dev/null; then
  nope "무응답: 프로세스 정리됨" "죽어 있음" "pid $SPID 살아 있음"
else
  ok "무응답: 프로세스 정리됨"
fi

echo "== 5-1. 턴 종료와 프로세스 종료는 다른 사실이다 =="
# 종료 코드만 보면 셋 다 구분되지 않는다. 종단 이벤트가 구분한다.

# (a) 프로세스는 exit 0 인데 턴은 실패했다.
RD="$TMPROOT/run-turnfail"
FAKE_MODE=turnfail "$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "턴 실패: 래퍼는 exit 0 (엔진은 돌았다)" "0" "$?"
assert_eq "턴 실패: terminal_event=turn.failed" "turn.failed" "$(json_field "$RD/status.json" terminal_event)"

# (b) 턴은 끝났는데 프로세스가 안 죽는다 — 하트비트가 죽이지만 산출물은 살아 있다.
#     실측된 사고의 기계 판정본이다: exit 코드는 실패인데 일은 끝나 있었다.
RD="$TMPROOT/run-donehang"
FAKE_MODE=donehang "$RUNNER" --profile run --model m-alpha --effort xhigh \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 60 --heartbeat 3 >/dev/null 2>&1
assert_eq "완료 후 행: exit 11" "11" "$?"
assert_eq "완료 후 행: terminal_event 가 살아남는다" "turn.completed" "$(json_field "$RD/status.json" terminal_event)"
assert_file_contains "완료 후 행: result.txt 가 남아 있다" "$RD/result.txt" "FAKE_RESULT_OK"

# (c) 사람/하네스가 중단(SIGTERM)해도 종단 이벤트는 보존된다. exit 130 의 실측 경로다.
RD="$TMPROOT/run-sigterm"
FAKE_MODE=donehang "$RUNNER" --profile run --model m-alpha --effort xhigh \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 120 --heartbeat 60 >/dev/null 2>&1 &
W_PID=$!
i=0
while [ "$i" -lt 15 ]; do
  [ -f "$RD/result.txt" ] && [ -s "$RD/result.txt" ] && break
  sleep 1; i=$((i+1))
done
kill -TERM "$W_PID" 2>/dev/null
wait "$W_PID" 2>/dev/null
assert_eq "SIGTERM: exit 130" "130" "$?"
assert_eq "SIGTERM: terminal_event 가 살아남는다" "turn.completed" "$(json_field "$RD/status.json" terminal_event)"

echo "== 5-2. 스키마 인자 =="
RD="$TMPROOT/run-schema"
SCHEMA_OK="$TMPROOT/schema-ok.json"
printf '%s\n' '{"type":"object","properties":{"verdict":{"type":"string"}},"required":["verdict"],"additionalProperties":false}' > "$SCHEMA_OK"
FAKE_MODE=ok "$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --schema "$SCHEMA_OK" >/dev/null 2>&1
assert_eq "스키마 지정: exit 0" "0" "$?"
assert_file_contains "스키마 지정: --output-schema 가 argv 에" "$FAKE_ARGV" "--output-schema"
assert_eq "스키마 지정: 경로가 그대로 전달된다" "$SCHEMA_OK" "$(cat "$FAKE_SCHEMA" 2>/dev/null)"

RD="$TMPROOT/run-noschema"
FAKE_MODE=ok "$RUNNER" --profile run --model m-alpha --effort xhigh \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_file_lacks "스키마 미지정: --output-schema 가 붙지 않는다" "$FAKE_ARGV" "--output-schema"

"$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --schema "$TMPROOT/no-such-schema.json" >/dev/null 2>&1
assert_eq "없는 스키마 파일: exit 2" "2" "$?"
EMPTY_SCHEMA="$TMPROOT/empty-schema.json"; : > "$EMPTY_SCHEMA"
"$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" --schema "$EMPTY_SCHEMA" >/dev/null 2>&1
assert_eq "빈 스키마 파일: exit 2" "2" "$?"

echo "== 6. codex 부재 =="
RD="$TMPROOT/run-missing"
PATH="$TMPROOT/empty:/usr/bin:/bin" "$RUNNER" --profile run --model m-alpha --effort high \
  --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
RC=$?
assert_eq "부재: exit 12" "12" "$RC"
assert_eq "부재: state=missing" "missing" "$(json_field "$RD/status.json" state)"
assert_eq "부재: codex_version 은 null" "null" "$(json_field "$RD/status.json" codex_version)"

echo "== 7. 인자 검증 =="
RD="$TMPROOT/run-args"
"$RUNNER" --model m-alpha --effort high --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "--profile 누락: exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$TMPROOT/does-not-exist.txt" --run-dir "$RD" >/dev/null 2>&1
assert_eq "프롬프트 파일 없음: exit 2" "2" "$?"

DIR_AS_PROMPT="$TMPROOT/dir-as-prompt"
mkdir -p "$DIR_AS_PROMPT"
"$RUNNER" --profile run --prompt-file "$DIR_AS_PROMPT" --run-dir "$RD" >/dev/null 2>&1
assert_eq "프롬프트 파일이 디렉터리: exit 2" "2" "$?"

EMPTY_PROMPT="$TMPROOT/empty-prompt.txt"
: > "$EMPTY_PROMPT"
"$RUNNER" --profile run --prompt-file "$EMPTY_PROMPT" --run-dir "$RD" >/dev/null 2>&1
assert_eq "빈 프롬프트 파일: exit 2" "2" "$?"

# FIFO 는 쓰는 쪽이 없으면 여는 순간 블로킹한다. 회귀 시 이 단언이 영원히 매달리지 않도록
# 워치독을 건다 — 5초 내에 안 끝나면 강제 종료하고 실패 처리한다.
FIFO_PROMPT="$TMPROOT/fifo-prompt"
mkfifo "$FIFO_PROMPT"
"$RUNNER" --profile run --prompt-file "$FIFO_PROMPT" --run-dir "$RD" >/dev/null 2>&1 &
FIFO_PID=$!
i=0
while [ "$i" -lt 5 ] && kill -0 "$FIFO_PID" 2>/dev/null; do
  sleep 1
  i=$((i+1))
done
if kill -0 "$FIFO_PID" 2>/dev/null; then
  kill -9 "$FIFO_PID" 2>/dev/null
  wait "$FIFO_PID" 2>/dev/null
  nope "FIFO 프롬프트 파일: exit 2" "2(5초 내 종료)" "5초 후에도 생존 — 무한 대기 회귀"
else
  wait "$FIFO_PID"
  assert_eq "FIFO 프롬프트 파일: exit 2" "2" "$?"
fi

"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --bogus x >/dev/null 2>&1
assert_eq "알 수 없는 인자: exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout >/dev/null 2>&1
assert_eq "값 없는 마지막 옵션: exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout xyz >/dev/null 2>&1
assert_eq "비숫자 timeout: exit 2" "2" "$?"
"$RUNNER" --profile bogus --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "잘못된 profile: exit 2" "2" "$?"
"$RUNNER" --profile 'a"b' --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "따옴표 든 profile: exit 2" "2" "$?"
# effort 는 low|medium|high|xhigh 만 노출한다. ultra 는 자동 위임이 붙어 비대화 실행이
# 예측 불가능하고, minimal·none 은 codex 가 거부한다(docs/codex/02 플래그 표).
"$RUNNER" --profile run --effort ultra --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "허용 밖 effort(ultra): exit 2" "2" "$?"
"$RUNNER" --profile run --effort minimal --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "허용 밖 effort(minimal): exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 9223372036854775808 >/dev/null 2>&1
assert_eq "INT64 초과 timeout: exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --heartbeat 9223372036854775808 >/dev/null 2>&1
assert_eq "INT64 초과 heartbeat: exit 2" "2" "$?"
# 하트비트가 타임아웃 이상이면 무응답 감지가 영원히 발동하지 못한다 — 감시를 설정으로
# 끄는 것과 같다. 인자로도 못 하게 막는다.
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 100 --heartbeat 100 >/dev/null 2>&1
assert_eq "heartbeat == timeout: exit 2" "2" "$?"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" --timeout 100 --heartbeat 200 >/dev/null 2>&1
assert_eq "heartbeat > timeout: exit 2" "2" "$?"
FAKE_MODE=ok "$RUNNER" --profile run --prompt-file "$PROMPT_FILE" \
  --run-dir "$TMPROOT/run-hb-ok" --timeout 100 --heartbeat 99 >/dev/null 2>&1
assert_eq "heartbeat < timeout: 정상 실행" "0" "$?"
# profile 기본값도 이 조건을 만족해야 한다. 하나라도 어기면 그 profile 은 아예 못 돈다.
for P in run image; do
  FAKE_MODE=ok "$RUNNER" --profile "$P" --prompt-file "$PROMPT_FILE" \
    --run-dir "$TMPROOT/run-hbdef-$P" >/dev/null 2>&1
  assert_eq "profile 기본값이 heartbeat < timeout 을 만족: $P" "0" "$?"
done

RD_MAXDIGIT="$TMPROOT/run-maxdigit"
FAKE_MODE=ok "$RUNNER" --profile run --prompt-file "$PROMPT_FILE" \
  --run-dir "$RD_MAXDIGIT" --timeout 999999999 >/dev/null 2>&1
assert_eq "9자리 timeout(상한 이내): exit 0" "0" "$?"

# 거절(exit 2)은 status.json 을 만들지 않는다 — 돌지 않은 실행을 기록하면 안 된다.
RD_REJ="$TMPROOT/run-rejected"
"$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD_REJ" --timeout 100 --heartbeat 100 >/dev/null 2>&1
if [ -f "$RD_REJ/status.json" ]; then
  nope "exit 2 는 status.json 을 만들지 않는다" "없음" "있음"
else
  ok "exit 2 는 status.json 을 만들지 않는다"
fi

echo "== 8. profile 기본값 =="
RD="$TMPROOT/run-profile-run"
FAKE_MODE=ok "$RUNNER" --profile run --prompt-file "$PROMPT_FILE" --run-dir "$RD" >/dev/null 2>&1
assert_eq "run 기본값으로 정상 실행" "0" "$?"
assert_eq "run: profile 기록" "run" "$(json_field "$RD/status.json" profile)"

RDI="$TMPROOT/run-profile-image"
FAKE_MODE=ok "$RUNNER" --profile image --prompt-file "$PROMPT_FILE" --run-dir "$RDI" >/dev/null 2>&1
assert_eq "image 기본값으로 정상 실행" "0" "$?"
assert_eq "image: profile 기록" "image" "$(json_field "$RDI/status.json" profile)"

RDX="$TMPROOT/run-badprofile"
FAKE_MODE=ok "$RUNNER" --profile develop --prompt-file "$PROMPT_FILE" --run-dir "$RDX" >/dev/null 2>&1
assert_eq "직무 이름(develop)은 profile 로 받지 않는다" "2" "$?"

echo "== 9. 작업 디렉터리 대조 =="
# codex 는 -C(--cwd) 에서 뜨고 --run-dir 은 산출물 자리일 뿐이다. 둘이 다른 저장소면 조용히
# 엉뚱한 저장소를 보게 되므로 래퍼가 인자 오류로 거절해야 한다. 실측된 사고다.
REPO_A="$TMPROOT/repo-a"; REPO_B="$TMPROOT/repo-b"
mkdir -p "$REPO_A" "$REPO_B"
git -C "$REPO_A" init -q 2>/dev/null
git -C "$REPO_B" init -q 2>/dev/null

if [ -d "$REPO_A/.git" ] && [ -d "$REPO_B/.git" ]; then
  RD_SAME="$REPO_A/run-same"
  ( cd "$REPO_A" && FAKE_MODE=ok "$RUNNER" --profile run \
      --prompt-file "$PROMPT_FILE" --run-dir "$RD_SAME" >/dev/null 2>&1 )
  assert_eq "cwd 와 run-dir 이 같은 저장소: 정상 실행" "0" "$?"

  RD_CROSS="$REPO_B/run-cross"
  ( cd "$REPO_A" && FAKE_MODE=ok "$RUNNER" --profile run \
      --prompt-file "$PROMPT_FILE" --run-dir "$RD_CROSS" >/dev/null 2>&1 )
  assert_eq "cwd 와 run-dir 이 다른 저장소: 인자 오류" "2" "$?"

  # 거절했으면 status.json 을 남기지 않는다 — 돌지 않은 실행을 기록하면 안 된다.
  if [ -f "$RD_CROSS/status.json" ]; then
    nope "저장소 불일치 거절 시 status.json 미생성" "없음" "있음"
  else
    ok "저장소 불일치 거절 시 status.json 미생성"
  fi

  # --cwd 로 준 디렉터리가 대조 기준이다. 호출 cwd 가 아니다.
  RD_CROSS2="$REPO_B/run-cross2"
  FAKE_MODE=ok "$RUNNER" --profile run --cwd "$REPO_A" \
      --prompt-file "$PROMPT_FILE" --run-dir "$RD_CROSS2" >/dev/null 2>&1
  assert_eq "--cwd 와 run-dir 이 다른 저장소: 인자 오류" "2" "$?"
  RD_SAME2="$REPO_B/run-same2"
  FAKE_MODE=ok "$RUNNER" --profile run --cwd "$REPO_B" \
      --prompt-file "$PROMPT_FILE" --run-dir "$RD_SAME2" >/dev/null 2>&1
  assert_eq "--cwd 와 run-dir 이 같은 저장소: 정상 실행" "0" "$?"

  # 저장소 밖(run-dir 이 git 밖)은 판정 근거가 없으므로 막지 않는다. docs/codex/00 D3 의 기본
  # run-dir(캐시 폴더)이 정확히 이 경우다.
  RD_OUT="$TMPROOT/run-outside"
  ( cd "$REPO_A" && FAKE_MODE=ok "$RUNNER" --profile run \
      --prompt-file "$PROMPT_FILE" --run-dir "$RD_OUT" >/dev/null 2>&1 )
  assert_eq "run-dir 이 저장소 밖이면 막지 않는다" "0" "$?"
else
  ok "작업 디렉터리 대조 — git 없음, 건너뜀"
fi

printf '\n%d개 통과 / %d개 실패\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
exit 0
