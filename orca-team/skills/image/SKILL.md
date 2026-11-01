---
name: image
description: >
  Codex 내장 image_gen(gpt-image-2, 구독)으로 래스터 이미지를 생성한다. 트리거: "이미지 만들어",
  "그림 그려", "일러스트/아이콘/배경/썸네일 생성", "generate an image", 또는 "/orca-team:image".
  API 키로 직접 이미지 API 를 부르라는 요청은 gpt-image 플러그인이 맡는다. SVG·CSS·다이어그램처럼
  코드로 그리는 편이 맞는 요청에는 뜨지 않는다.
argument-hint: "[--n <1..8>] [--out <dir>] [--ar <w:h>] [--transparent] [--ref <file>]... [--background] <프롬프트>"
---

# /orca-team:image — Codex 내장 image_gen 으로 이미지 생성

`codex exec` 를 우회 모드로 돌리고, 프롬프트가 codex 에게 내장 `image_gen` 도구(구독 렌더, API 키·건당
과금 없음)를 쓰게 한다. 도구는 모델·크기·목적지 인자를 받지 않는다: 비율은 말로, 한 호출 1장, 저장 위치는
codex 가 정하고(`$CODEX_HOME/generated_images/` 아래) 이 스킬이 `--out` 으로 옮긴다.
래퍼는 orca-team 에 들어 있는 `${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh --profile image`.

## 1. 프롬프트와 플래그를 정한다

인자: `$ARGUMENTS`

- 인자가 비어 있으면(프롬프트 트리거) 직전 사용자 메시지의 이미지 묘사를 프롬프트로 삼는다.
- 문법 `[--flag value]... <프롬프트>`. 플래그: `--n <1..8>`(기본 1) `--out <dir>`(기본 `./generated/`)
  `--ar <w:h>` `--transparent` `--ref <file>`(반복 가능, 참조·편집 대상) `--background`.
- 알 수 없는 `--` 토큰은 거부하고 멈춘다. 프롬프트가 비면 무엇을 그릴지 한 줄로 묻고 멈춘다.
- `--ref` 파일이 없으면 멈춘다.
- 확인하지 않는다. `--n` ≥ 3 이면 "N장, 장당 1~3분" 한 줄만 말하고 진행한다.

## 2. 프리플라이트

```bash
codex features list | grep -E '^image_generation[[:space:]]'
```

마지막 열이 `true` 가 아니면 `codex features enable image_generation` 을 안내하고 멈춘다. 시험 생성은 하지 않는다 — 쿼터를 태운다.

## 3. 장마다 run-dir 과 프롬프트를 만든다

i = 1..N 에 대해:

```bash
CWD="$(pwd -P)"
RUN="${XDG_CACHE_HOME:-$HOME/.cache}/orca-team/runs/$(basename "$CWD")/$(date +%Y%m%d-%H%M%S)-image-<slug>-$i"
mkdir -p "$RUN"
```

slug 는 프롬프트 앞 5단어를 kebab-case 로, ASCII 영숫자 외는 버린다. 비면 `image`.

`${CLAUDE_PLUGIN_ROOT}/templates/image-prompt.md` 를 읽어 채우고 `$RUN/prompt.txt` 로 쓴다:

| 자리 | 채울 것 |
|---|---|
| `{prompt}` | 사용자 프롬프트 그대로. 이미 구체적이면 손대지 않는다 |
| `{ar}` | `--ar w:h` 가 있으면 `가로세로 비율은 w:h 다.` 없으면 빈 줄 |
| `{transparent}` | `--transparent` 면 `배경은 투명(알파 채널)이어야 한다. 배경색을 깔지 마라.` |
| `{ref}` | `--ref` 가 있으면 `첨부한 이미지는 편집 대상이다. <바꿀 것>만 바꾸고 나머지(구도·색·피사체)는 유지해라.` 바꿀 것은 사용자 요청에서 취한다 |

마지막 두 줄(비대화 지시, 경로 출력)을 빼지 않는다. 경로가 안 나오면 5 에서 멈춘다.
시크릿을 프롬프트에 넣지 않는다. 프롬프트는 `prompt.txt`·로그·보고로 흘러간다.

## 4. 래퍼를 띄운다

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh" --profile image --cwd "$CWD" \
  --prompt-file "$RUN/prompt.txt" --run-dir "$RUN" \
  [--image <ref>]... [--timeout 540] > "$RUN/wrapper.out" 2>&1
```

| 조건 | 방식 |
|---|---|
| 기본 | 포그라운드. `--timeout 540`, Bash 툴 `timeout` 600000 |
| `--background` 또는 `--n` ≥ 3 | 하네스 백그라운드 `run_in_background: true`. N장은 한 Bash 스크립트 안에서 순차 루프(한 호출 1장) |

`--heartbeat` 를 300 미만으로 내리지 않는다 — 렌더 자체가 수십 초 침묵한다. `nohup … &` 로 떼지 않는다.
백그라운드면 "N장 생성 중, run-dir …, 끝나면 알림" 한 줄로 턴을 끝낸다.

## 5. 결과를 읽는다

1. 래퍼 exit 2 면 `status.json` 이 없다. `$RUN/wrapper.out` 을 읽고 인자를 고쳐 4 를 다시 한다.
2. 종단 이벤트를 먼저 본다. 종료 코드는 그 다음이다. 프로세스가 죽은 것과 턴이 끝난 것은 다른 사실이다.

   ```bash
   grep -oE '"(terminal_event|exit|raw_exit)": [^,]*' "$RUN/status.json"
   ```

   | exit | terminal_event | 판정 |
   |---|---|---|
   | 0 | `turn.completed` | 정상. 3 으로 |
   | 0 | `turn.failed` / `error` | 턴 실패 또는 거부. `result.txt` 의 거부 사유와 `progress.log` 마지막 5줄을 그대로 보여주고 멈춘다. 실존 인물·저작권 정책일 수 있다. 자동 재시도로 쿼터를 태우지 않는다 |
   | 10 / 11 / 130 | `turn.completed` | 일은 끝났다. 껍데기만 죽었다. 산출물로 3 으로 |
   | 10 / 11 / 130 | `null` | 중간에 끊겼다. exit 11 이면 하트비트가 300 미만이었는지 먼저 본다. 같은 `prompt.txt` 로 새 run-dir 에서 1회 재시도. 두 번째도 실패면 멈추고 `progress.log` 의 결정적 줄을 보여준다 |
   | 1 | 무엇이든 | 비정상 종료(원래 코드는 `raw_exit`). `progress.log` 를 읽고 1회 재시도 |
   | 12 | — | codex 가 PATH 에 없다. `python3 "${CLAUDE_PLUGIN_ROOT}/bin/orch" doctor` 를 돌려 보라고 하고 멈춘다 |

3. 경로를 뽑는다:

   ```bash
   SRC="$(tail -1 "$RUN/result.txt" | sed -e 's/^[[:space:]`]*//' -e 's/[[:space:]`]*$//')"
   [ -f "$SRC" ] || echo "경로 없음"
   ```

   파일이 없으면 `result.txt` 전문을 보여주고 멈춘다 — codex 가 경로를 안 적었거나 생성이 거부된 것이다. 프롬프트 마지막 두 줄이 빠졌는지 먼저 본다.

## 6. 옮기고 검사한다

```bash
mkdir -p "$OUT"; DST="$OUT/<slug>-$i.png"     # 이미 있으면 -v2, -v3 … 덮어쓰지 않는다
cp "$SRC" "$DST"
sips -g pixelWidth -g pixelHeight -g hasAlpha "$DST"
```

원본은 `$CODEX_HOME` 에 남겨 둔다. `--transparent` 인데 `hasAlpha: no` 면
`~/.codex/skills/.system/imagegen/scripts/remove_chroma_key.py` 로 크로마키 제거를 시도하고(인자는 `--help`),
그래도 안 되면 그 사실을 보고한다.

## 7. 보고한다

저장 경로, 픽셀 크기, 장수, run-dir 경로. `Read` 로 이미지를 실제로 보고 사용자가 원하면 한 줄 묘사를
덧붙인다. "예쁜가" 는 판정하지 않는다 — 사람이 본다. 세션의 첫 보고에는 "우회 모드(샌드박스 꺼짐)로 돌았다" 를 적는다.

완료: 사용자가 `--out` 아래의 파일 경로와 크기를 봤다.
