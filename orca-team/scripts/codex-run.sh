#!/usr/bin/env bash
#
# codex-run.sh — codex exec 를 감시하며 실행한다. 계약의 정본은 docs/codex/02-codex-invocation.md 다.
#
# 계약: 이 스크립트는 반드시 끝난다. 무한 대기가 구조적으로 불가능하다.
#   - 전체 타임아웃 초과       → 프로세스 그룹째 종료, exit 10
#   - 진행 스트림 무응답       → 프로세스 그룹째 종료, exit 11
#   - codex 가 PATH 에 없음    → exit 12
#
# 종료 코드와 별개로 **종단 이벤트**를 status.json 의 terminal_event 에 남긴다.
# 프로세스가 죽은 것과 턴이 끝난 것은 다른 사실이다 — exit 10/130 인데
# terminal_event 가 "turn.completed" 면 일은 이미 끝나 있다. 산출물을 버리지 마라.
#
# 정책을 갖지 않는다. 한 번 시도하고, 왜 끝났는지 보고한다.
# 재시도와 폴백은 호출자(image 스킬·orch review)가 결정한다.
#
# exit 0 은 "엔진이 돌았다" 는 뜻이지 "작업이 성공했다" 는 뜻이 아니다.
# 내용 판정은 result.txt 를 읽는 스킬이 한다.
#
# 관측 계약 — 변경 금지:
#   - --json 을 제거하지 않는다. 제거하면 stdout 의 JSONL 이 사라져 events.jsonl 이
#     비고, **하트비트 근거의 대부분이 함께 사라진다**(실측: --json 이 붙으면 stderr 는
#     한 줄만 남고 진행 전사가 전부 stdout 으로 간다. --json 을 떼면 그 반대가 된다).
#     하트비트는 events.jsonl 과 progress.log 의 **합**을 보므로 어느 쪽이 비어도
#     돌긴 하지만, 종단 이벤트 판정은 events.jsonl 이 없으면 성립하지 않는다.
#   - --output-last-message 를 제거하지 않는다. --json 을 쓰는 순간 stdout 은
#     최종 메시지가 아니라 이벤트 스트림이다. 이 플래그가 result.txt 를 만든다.
#   - -C <cwd> 를 제거하지 않는다. 빠뜨리면 codex 가 호출 셸의 cwd 에서 뜨고, 그 cwd 는
#     하네스가 호출 사이에 초기화한다 — 엉뚱한 저장소를 보면서 정상 종료한 실측이 있다.
#
# 보안 계약 — 변경 금지:
#   - --dangerously-bypass-approvals-and-sandbox 를 제거하거나 샌드박스로 되돌리지 않는다.
#     비대화 자동 실행은 승인 프롬프트에서 멈춘다. 샌드박스는 credential 을 포함한
#     환경 제약을 만든다. 사용자 config.toml 이 같은 값이어도 명시한다.
#   - --bypass-hook-trust 는 opt-in 이다. 기본값으로 켜지 않는다. 켜면 codex 가 신뢰 기록 없이
#     활성 훅을 실행한다. 훅 출처는 검증하지 않으므로 저장소의 Codex 훅을 신뢰할 수 있을 때만 쓴다.
#   - --skip-git-repo-check 를 제거하지 않는다. 비-git 폴더에서 "Not inside a trusted
#     directory" 로 거부된다. 저장소 안에서도 무해하다.
#   - 환경을 갈아끼우지 않는다. env -i / env -u / 화이트리스트 전달 전부 금지.
#     credential 이 환경에서 온다.
#   - 프롬프트에 시크릿을 넣지 않는다(호출자 책임).
#   - run-dir 산출물에는 codex 출력이 그대로 담긴다. 저장소 밖(docs/codex/00 D3)에 둔다.
#
# 모델·effort 는 선택이다. 없으면 플래그를 붙이지 않아 사용자 ~/.codex/config.toml 을
# 상속한다(docs/codex/00 D5). 모델 ID 를 이 파일에 박지 않는다.
#
# bash 3.2(macOS 기본) 호환. jq·GNU timeout 을 요구하지 않는다.

set -u

usage() {
  cat >&2 <<'USAGE'
사용법:
  codex-run.sh --profile <run|image> --prompt-file <path> --run-dir <path>
               [--cwd <dir>] [--model <id>] [--effort <low|medium|high|xhigh>]
               [--image <file>]... [--schema <path>] [--timeout <초>] [--heartbeat <초>]
               [--bypass-hook-trust]

  --profile  타임아웃·하트비트 기본값 묶음. run 2400/900초, image 900/300초.
  --cwd      codex 의 작업 루트(-C). 기본 $PWD. 항상 주는 것이 안전하다.
  --model    없으면 사용자 config.toml 의 모델을 상속한다.
  --effort   없으면 상속. 값은 -c model_reasoning_effort= 로 전달된다(--effort 플래그가 없는 버전 호환).
  --image    프롬프트에 첨부할 입력 이미지(-i). 반복 가능. 생성이 아니라 참조·편집 대상이다.
  --schema   최종 응답을 강제할 JSON Schema 파일. v1 스킬은 쓰지 않는다.
  --bypass-hook-trust  codex exec 에 --dangerously-bypass-hook-trust 를 붙인다(훅 신뢰 자동 적용). 기본은 붙이지 않는다.

종료 코드:
  0   정상 종료 (내용 성공 여부는 판단하지 않음)
  10  전체 타임아웃 초과
  11  진행 스트림 무응답
  12  codex 가 PATH 에 없음
  1   그 외 비정상 종료 (원래 코드는 status.json 의 raw_exit)
  2   인자 오류 (status.json 을 만들지 않는다)
  130 사람이 중단(SIGINT/SIGTERM/SIGHUP)
USAGE
}

PROFILE=""; CWD=""; MODEL=""; EFFORT=""; PROMPT_FILE=""; RUN_DIR=""; SCHEMA=""; TIMEOUT=""; HEARTBEAT=""
IMAGES=()
BYPASS_HOOK_TRUST=0

need_val() {  # $1=플래그명 $2=남은 인자 수
  # bash 3.2 에서 값 없이 끝나는 옵션의 shift 2 는 rc=1 을 내고 시프트하지 않는다.
  # 그대로 두면 while 루프가 종료 조건 없이 영원히 돈다 — 여기서 미리 막는다.
  if [ "$2" -lt 2 ]; then
    echo "인자 값 누락: $1" >&2
    usage
    exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)     need_val "$1" "$#"; PROFILE="$2";     shift 2 ;;
    --cwd)         need_val "$1" "$#"; CWD="$2";         shift 2 ;;
    --model)       need_val "$1" "$#"; MODEL="$2";       shift 2 ;;
    --effort)      need_val "$1" "$#"; EFFORT="$2";      shift 2 ;;
    --image)       need_val "$1" "$#"; IMAGES[${#IMAGES[@]}]="$2"; shift 2 ;;
    --prompt-file) need_val "$1" "$#"; PROMPT_FILE="$2"; shift 2 ;;
    --run-dir)     need_val "$1" "$#"; RUN_DIR="$2";     shift 2 ;;
    --schema)      need_val "$1" "$#"; SCHEMA="$2";      shift 2 ;;
    --timeout)     need_val "$1" "$#"; TIMEOUT="$2";     shift 2 ;;
    --heartbeat)   need_val "$1" "$#"; HEARTBEAT="$2";   shift 2 ;;
    --bypass-hook-trust) BYPASS_HOOK_TRUST=1; shift ;;
    -h|--help)     usage; exit 2 ;;
    *) echo "알 수 없는 인자: $1" >&2; usage; exit 2 ;;
  esac
done

for pair in "profile:$PROFILE" "prompt-file:$PROMPT_FILE" "run-dir:$RUN_DIR"; do
  if [ -z "${pair#*:}" ]; then
    echo "필수 인자 누락: --${pair%%:*}" >&2
    usage
    exit 2
  fi
done

if [ ! -f "$PROMPT_FILE" ] || [ ! -r "$PROMPT_FILE" ]; then
  echo "프롬프트 파일이 없거나 일반 파일이 아니거나 읽을 수 없다: $PROMPT_FILE" >&2
  exit 2
fi
if [ ! -s "$PROMPT_FILE" ]; then
  echo "프롬프트 파일이 비어 있다: $PROMPT_FILE" >&2
  exit 2
fi

# 스키마는 선택 인자다. 준 이상 반드시 유효해야 한다 — 깨진 스키마를 codex 에 넘기면
# 조용히 자유 텍스트로 되돌아가고, 그걸 알아챌 방법이 없다.
if [ -n "$SCHEMA" ]; then
  if [ ! -f "$SCHEMA" ] || [ ! -r "$SCHEMA" ] || [ ! -s "$SCHEMA" ]; then
    echo "스키마 파일이 없거나 일반 파일이 아니거나 비어 있다: $SCHEMA" >&2
    exit 2
  fi
fi

# 첨부 이미지도 같다. 없는 파일을 -i 로 넘기면 codex 가 시작 단계에서 거부하고, 그 실패는
# progress.log 에만 남아 원인 추적이 한 단계 늦어진다.
for img in ${IMAGES[@]+"${IMAGES[@]}"}; do
  if [ ! -f "$img" ] || [ ! -r "$img" ] || [ ! -s "$img" ]; then
    echo "첨부 이미지가 없거나 일반 파일이 아니거나 비어 있다: $img" >&2
    exit 2
  fi
done

# effort 는 low|medium|high|xhigh 만 노출한다. ultra 는 자동 위임이 붙어 비대화 실행이
# 예측 불가능하고, minimal·none 은 codex 가 거부한다(docs/codex/02 플래그 표).
if [ -n "$EFFORT" ]; then
  case "$EFFORT" in
    low|medium|high|xhigh) : ;;
    *) echo "--effort 는 low, medium, high, xhigh 중 하나여야 한다: $EFFORT" >&2; usage; exit 2 ;;
  esac
fi

# --cwd 는 codex 의 작업 루트다. 기본은 호출 cwd. 실재하는 디렉터리의 절대경로로 정규화한다.
: "${CWD:=$PWD}"
if [ ! -d "$CWD" ]; then
  echo "--cwd 가 디렉터리가 아니다: $CWD" >&2
  exit 2
fi
CWD="$(cd "$CWD" 2>/dev/null && pwd -P)" || { echo "--cwd 로 이동할 수 없다: $CWD" >&2; exit 2; }

# profile 별 기본값. CLI 인자가 있으면 그것이 이긴다.
#
# 하트비트는 **추론 시간을 견딜 만큼** 길어야 한다. 하트비트는 출력 스트림이 자라는지만
# 보는데, codex 의 추론 구간에는 출력이 없다. 짧게 잡으면 살아서 생각하는 중인 엔진을
# 죽인다 — 도그푸딩에서 두 번 실측했다(120초·180초에 오살, 각각 641KB·118KB 를
# 이미 출력한 뒤였다). 전체 타임아웃이 폭주를 막는 진짜 상한이고, 하트비트는
# "죽은 프로세스" 만 잡는다.
#
# xhigh 4파일 대조 실측: **도구를 다 쓴 뒤 최종 답을 짓는 구간에서 318초 동안 두 스트림
# 모두 한 바이트도 자라지 않았다.** 도구가 도는 동안 최대 갭은 29초. 침묵은 렌더링 문제가
# 아니라 실제 무출력이다. 그래서 run 은 900초다. image 는 1장 렌더가 1~3분이라 300초.
case "$PROFILE" in
  run)   : "${TIMEOUT:=2400}"; : "${HEARTBEAT:=900}" ;;
  image) : "${TIMEOUT:=900}";  : "${HEARTBEAT:=300}" ;;
  *) echo "--profile 은 run, image 중 하나여야 한다: $PROFILE" >&2; usage; exit 2 ;;
esac

# TIMEOUT/HEARTBEAT 가 숫자가 아니면 이후 [ -lt ] 비교가 전부 rc=2(오류)를 내며 조용히
# "거짓" 취급되어 타임아웃/하트비트가 영원히 발동하지 않는다. 여기서 미리 막는다.
# 자릿수 상한(9자리, 최대 999999999초 ≈ 31년)도 함께 건다 — INT64 범위를 넘는 값은
# 같은 이유로 [ -lt ]/[ -ge ] 산술을 rc=2 로 깨뜨려 타임아웃을 무력화한다.
is_uint() {
  case "$1" in
    ''|*[!0-9]*)  return 1 ;;   # 빈 값·비숫자
    ??????????*)  return 1 ;;   # 10자리 이상 — INT64 산술이 깨진다
    *)            return 0 ;;
  esac
}

for pair in "timeout:$TIMEOUT" "heartbeat:$HEARTBEAT"; do
  if ! is_uint "${pair#*:}"; then
    echo "--${pair%%:*} 는 0 이상 9자리 이하의 정수여야 한다(최대 999999999): ${pair#*:}" >&2
    exit 2
  fi
done

# 하트비트가 전체 타임아웃보다 크거나 같으면 전체 타임아웃이 항상 먼저 발동해
# **하트비트가 영원히 발동하지 않는다** — 무응답 감지가 조용히 죽은 letter 가 된다.
# 기본값은 전부 이 조건을 만족하지만 인자로는 깰 수 있으므로 여기서 막는다.
if [ "$HEARTBEAT" -ge "$TIMEOUT" ]; then
  echo "--heartbeat(${HEARTBEAT}초) 는 --timeout(${TIMEOUT}초) 보다 작아야 한다." >&2
  echo "크거나 같으면 전체 타임아웃이 먼저 발동해 무응답 감지가 동작하지 못한다." >&2
  exit 2
fi

mkdir -p "$RUN_DIR" || { echo "run-dir 생성 실패: $RUN_DIR" >&2; exit 2; }

# 작업 디렉터리 대조 — codex 는 --cwd(-C) 에서 뜨고 --run-dir 은 산출물을 적는 자리다.
# 둘이 다른 저장소를 가리키면 호출부가 인자를 잘못 짠 것이다. 조용히 틀리느니 여기서 멈춘다.
# 둘 다 git 저장소일 때만 대조한다. 기본 run-dir(docs/codex/00 D3, 캐시 폴더)은 저장소 밖이라 통과한다.
if command -v git >/dev/null 2>&1; then
  CWD_ROOT="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || true)"
  RUN_ROOT="$(git -C "$RUN_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$CWD_ROOT" ] && [ -n "$RUN_ROOT" ] && [ "$CWD_ROOT" != "$RUN_ROOT" ]; then
    echo "--cwd 와 --run-dir 이 다른 저장소를 가리킨다." >&2
    echo "  cwd     : $CWD_ROOT" >&2
    echo "  run-dir : $RUN_ROOT" >&2
    echo "run-dir 은 저장소 밖(docs/codex/00 D3)에 두거나 --cwd 와 같은 저장소 안에 둔다." >&2
    exit 2
  fi
fi

# 짧은 값(테스트)에서는 폴링 간격을 좁힌다.
POLL=5
if [ "$HEARTBEAT" -lt 10 ] || [ "$TIMEOUT" -lt 10 ]; then POLL=1; fi

RESULT="$RUN_DIR/result.txt"
EVENTS="$RUN_DIR/events.jsonl"
PROGRESS="$RUN_DIR/progress.log"
STATUS="$RUN_DIR/status.json"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

STARTED_AT="$(now_iso)"
LAST_EVENT_AT="$STARTED_AT"
CODEX_PID=""
TERMINAL_EVENT=null
SILENCE=0
CODEX_VERSION=null   # JSON 리터럴 그대로 들어간다: null 또는 "따옴표 친 문자열"

# JSON 문자열 값. 역슬래시와 큰따옴표만 이스케이프한다 — 경로·버전 문자열에는 그것으로 충분하다.
json_str() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }
CWD_JSON="$(json_str "$CWD")"

# 턴이 끝났는지는 **종료 코드가 아니라 이벤트**가 말한다. 둘은 다른 사실이다 —
# 하네스의 도구 타임아웃이나 전체 타임아웃이 껍데기만 죽이는 경우, 종료 코드는
# 130/10 인데 턴은 이미 완결돼 있다. 완성품을 버리는 것이 가장 비싼 실수다
# (실측: exit 130 인데 인수 조건 17/17 통과).
#
# jq 도 python 도 쓰지 않는다. codex 의 JSONL 은 공백 없는 compact 형식이라
# 줄 첫머리가 그대로 판정 근거다. 마지막 종단 이벤트를 취한다.
# 이벤트 이름은 버전 계약이다(docs/codex/02 통제 밖 변수). 래퍼 회귀 테스트(tests/run.sh --wrapper)가
# 가짜 codex 의 같은 이름으로 이 판정을 확인한다. 실물 codex 의 이름은 docs/codex/05 §2 스파이크로 본다.
scan_terminal() {
  TERMINAL_EVENT=null
  [ -s "$EVENTS" ] || return 0
  T="$(grep -oE '^\{"type":"(turn\.completed|turn\.failed|error)"' "$EVENTS" 2>/dev/null \
       | tail -1 | sed 's/^{"type"://')"
  [ -n "$T" ] && TERMINAL_EVENT="$T"
  return 0
}

# status.json 에는 스칼라만 담는다. 에러 내용은 progress.log·result.txt 에 있다.
# tmp + mv 로 원자적으로 쓴다 — 스킬이 실행 중에 열어봐도 반쪽 파일을 보지 않는다.
write_status() {  # $1=state $2=raw_exit(숫자|null) $3=exit(숫자|null)
  printf '{\n  "profile": "%s",\n  "state": "%s",\n  "pid": %s,\n  "cwd": %s,\n  "codex_version": %s,\n  "started_at": "%s",\n  "last_event_at": "%s",\n  "silence_secs": %s,\n  "terminal_event": %s,\n  "raw_exit": %s,\n  "exit": %s\n}\n' \
    "$PROFILE" "$1" "${CODEX_PID:-null}" "$CWD_JSON" "$CODEX_VERSION" "$STARTED_AT" "$LAST_EVENT_AT" "$SILENCE" "$TERMINAL_EVENT" "$2" "$3" \
    > "$STATUS.tmp.$$" && mv -f "$STATUS.tmp.$$" "$STATUS"
}

if ! command -v codex >/dev/null 2>&1; then
  : > "$RESULT"
  : > "$EVENTS"
  : > "$PROGRESS"
  write_status missing null 12
  echo "codex 가 PATH 에 없다. orch doctor 를 돌려라." >&2
  exit 12
fi

# 버전을 남긴다 — JSONL 이벤트 이름 계약의 근거다. 실패하면 null 로 둔다.
V="$(codex --version 2>/dev/null | head -1 | tr -d '\r')"
[ -n "$V" ] && CODEX_VERSION="$(json_str "$V")"

# codex 는 자식 프로세스를 띄울 수 있다. 프로세스 그룹째 정리한다.
kill_group() {
  [ -n "$CODEX_PID" ] || return 0
  kill -TERM "-$CODEX_PID" 2>/dev/null || kill -TERM "$CODEX_PID" 2>/dev/null
  i=0
  while [ "$i" -lt 5 ] && kill -0 "$CODEX_PID" 2>/dev/null; do
    sleep 1
    i=$((i+1))
  done
  kill -KILL "-$CODEX_PID" 2>/dev/null || kill -KILL "$CODEX_PID" 2>/dev/null
  return 0
}

# 사람이 중단해도 전체 권한으로 도는 codex 를 남기지 않는다.
trap 'kill_group; scan_terminal; write_status failed null 130; exit 130' INT TERM HUP

# 명령 치환은 후행 개행을 전부 제거한다 — 프롬프트 파일이 개행으로 끝나도 codex 에는
# 개행 없는 값이 전달된다. 프롬프트 의미에는 영향이 없으므로 의도된 동작이다.
PROMPT="$(cat "$PROMPT_FILE")"

: > "$RESULT"
: > "$EVENTS"
: > "$PROGRESS"

# 선택 인자는 있을 때만 붙인다. bash 3.2 에서 빈 배열 확장이 set -u 와 충돌하므로
# 위치 인자에 쌓는다(인자 파싱은 이미 끝나 "$@" 가 비어 있다).
set --
[ -n "$MODEL" ]  && set -- "$@" --model "$MODEL"
[ -n "$EFFORT" ] && set -- "$@" -c "model_reasoning_effort=$EFFORT"
for img in ${IMAGES[@]+"${IMAGES[@]}"}; do set -- "$@" -i "$img"; done
[ -n "$SCHEMA" ] && set -- "$@" --output-schema "$SCHEMA"
[ "$BYPASS_HOOK_TRUST" = 1 ] && set -- "$@" --dangerously-bypass-hook-trust

# set -m 으로 codex 에 자기 프로세스 그룹을 준다(pgid == $!).
# 환경은 그대로 물려준다 — env 를 건드리지 않는 것이 곧 credential 전달이다.
#
# stdin 은 반드시 /dev/null 이다 — 상속시키지 않는다. codex exec 는 프롬프트를 인자로
# 받아도 stdin 이 열려 있으면 "Reading additional input from stdin..." 을 찍고 EOF 를
# 기다린다. 하네스가 백그라운드로 띄우면 stdin 이 무엇일지 보장이 없으므로, 상속시키면
# codex 가 작업을 시작조차 못 한 채 하트비트에 걸려 죽는다(정상 작업의 오살).
#
# stdout 은 --json 때문에 **이벤트 스트림**이다. 최종 메시지는 --output-last-message 가
# result.txt 에 쓴다. 이 둘은 한 몸이다 — 한쪽만 떼면 result.txt 가 JSONL 로 오염되거나
# 사라진다.
set -m
(
  exec codex exec \
    --skip-git-repo-check \
    --dangerously-bypass-approvals-and-sandbox \
    -C "$CWD" \
    "$@" \
    --json \
    --output-last-message "$RESULT" \
    "$PROMPT"
) >"$EVENTS" 2>"$PROGRESS" </dev/null &
CODEX_PID=$!
set +m
write_status running null null

START_TS="$(date +%s)"
LAST_EVENT_TS="$START_TS"
LAST_SIZE=0

while kill -0 "$CODEX_PID" 2>/dev/null; do
  sleep "$POLL"

  # sleep 중에 끝났으면 타임아웃으로 오판하지 않는다.
  kill -0 "$CODEX_PID" 2>/dev/null || break

  NOW_TS="$(date +%s)"

  # 활동량은 두 스트림의 **합**이다. --json 이 붙으면 진행 전사는 stdout(events.jsonl)
  # 으로 가고 stderr 는 거의 마른다 — 한쪽만 보면 살아 있는 엔진을 오살한다.
  SIZE_E="$(wc -c < "$EVENTS" 2>/dev/null | tr -d ' ')"
  [ -n "$SIZE_E" ] || SIZE_E=0
  SIZE_P="$(wc -c < "$PROGRESS" 2>/dev/null | tr -d ' ')"
  [ -n "$SIZE_P" ] || SIZE_P=0
  SIZE=$((SIZE_E + SIZE_P))
  if [ "$SIZE" -ne "$LAST_SIZE" ]; then
    LAST_SIZE="$SIZE"
    LAST_EVENT_TS="$NOW_TS"
    LAST_EVENT_AT="$(now_iso)"
  fi
  SILENCE=$((NOW_TS - LAST_EVENT_TS))
  write_status running null null

  if [ $((NOW_TS - START_TS)) -ge "$TIMEOUT" ]; then
    kill_group
    scan_terminal
    write_status timeout null 10
    echo "전체 타임아웃 ${TIMEOUT}초 초과 — 강제 종료. events.jsonl·progress.log 를 보라." >&2
    echo "마지막 활동으로부터 ${SILENCE}초, 종단 이벤트=${TERMINAL_EVENT}." >&2
    [ "$TERMINAL_EVENT" = "null" ] || echo "턴은 이미 끝나 있었다 — 산출물을 폐기하지 마라." >&2
    exit 10
  fi

  if [ $((NOW_TS - LAST_EVENT_TS)) -ge "$HEARTBEAT" ]; then
    kill_group
    scan_terminal
    write_status stalled null 11
    echo "진행 스트림이 ${HEARTBEAT}초 동안 자라지 않음 — 강제 종료. events.jsonl·progress.log 를 보라." >&2
    [ "$TERMINAL_EVENT" = "null" ] || echo "종단 이벤트=${TERMINAL_EVENT} — 턴은 이미 끝나 있었다. 산출물을 폐기하지 마라." >&2
    exit 11
  fi
done

wait "$CODEX_PID"
RAW=$?
scan_terminal
SILENCE=$(( $(date +%s) - LAST_EVENT_TS ))

if [ "$RAW" -eq 0 ]; then
  write_status ok 0 0
  exit 0
fi

write_status failed "$RAW" 1
echo "codex 비정상 종료 (raw_exit=$RAW, 종단 이벤트=${TERMINAL_EVENT}). events.jsonl·progress.log 를 보라." >&2
exit 1
