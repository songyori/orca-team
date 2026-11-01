> orca-team 내장 Codex 래퍼(`orca-team/scripts/codex-run.sh`)와 이미지 생성(`/orca-team:image`)의 설계 문서다. orca-team 에는 `run` 스킬이 없다. 본문의 `run` 스킬 이야기는 설계 당시 기록이다.

# 03 — 이미지 생성 (codex 내장 image_gen, gpt-image-2)

호출 경로는 `02` 와 같다. 래퍼 `--profile image` 로 `codex exec` 를 부르고, **프롬프트가 codex 에게
내장 `image_gen` 도구를 쓰게 한다.** 이 문서는 그 위에 얹히는 것만 적는다.

## 사실 (실측, 2026-09-06)

| 항목 | 값 | 근거 |
|---|---|---|
| feature flag | `image_generation stable true` | `codex features list` |
| 도구 이름 | 내장 `image_gen` (생성·편집·투명 배경) | `~/.codex/skills/.system/imagegen/SKILL.md` |
| 모델 | 도구는 모델 인자를 받지 않는다. 구독 렌더, **API 키 불필요, 건당 과금 없음**(구독 쿼터만) | 같은 파일. 이전 프로젝트에서 `codex exec` 로 실생성 확인(PNG 1254×1254, exit 0) |
| gpt-image-2 와의 관계 | 내장 도구 = 구독의 최신 이미지 모델. 공식 스킬이 "내장 `image_gen` 또는 CLI `gpt-image-2` 에서 `gpt-image-1.5` 로 조용히 바꾸지 마라" 고 적는다 — 둘을 같은 급으로 둔다 | 같은 파일 |
| 저장 위치 | `$CODEX_HOME/generated_images/` 아래. **목적지 인자 없음.** 레이아웃은 버전마다 다르다: 옛 버전이 남긴 파일은 평면(`<슬러그>.png`), 0.153.4 `codex exec` 실측(2026-09-06)은 `<thread-id>/exec-<uuid>.png` 세션 하위 — 레이아웃을 가정하지 말고 **codex 가 절대경로를 보고하게 한다** | 스파이크 3회 `result.txt` |
| `codex exec -i` | 입력 전용. 참조·편집 대상 첨부. 생성은 안 한다 | `codex exec --help`, 이전 프로젝트 기록 |
| 크기·비율 | 도구 인자 없음. 비율은 프롬프트에 말로. 정확한 픽셀은 후처리(`sips`) | 이전 프로젝트 실측 |
| 투명 배경 | 자동 아님. 프롬프트에 명시하고 알파를 확인. 0.153.4 실측: 명시하면 `hasAlpha: yes` 로 바로 나온다(후처리 불필요). 안 나오면 `~/.codex/skills/.system/imagegen/scripts/remove_chroma_key.py --input <src> --out <dst> --auto-key corners` | 공식 스킬 + 스파이크 2 |
| 장수 | 한 호출당 1장. N장 = N번 호출 | 이전 프로젝트 실측 |
| seed | 없음. 재현 좌표는 프롬프트뿐 | 이전 프로젝트 실측 |
| 검증 상태 | 2026-09-06 래퍼 경유 스파이크 3회 전부 exit 0·`turn.completed`: 생성 46초, 투명 1:1 76초, `--ref` 편집 55초. 전부 1254×1254. 하트비트 300 충분 | `05` §3 |
| 편집 | `--image`→`-i` 첨부 + "편집 대상이다, X만 바꿔라" 프롬프트로 색 변경이 됐다(사과 빨강→초록, 구도 유지) | 스파이크 3 |
| 스킬 예산 경고 | 사용자 `~/.codex/skills` 가 많으면(실측 138~177개) `events.jsonl` 에 `item.type:error` "Exceeded skills context budget…" 이 찍힌다. **턴 수준 `error` 이벤트가 아니라** `item.completed` 안의 아이템이라 종단 판정에 영향 없고, 내장 도구는 정상 동작한다. 기록만 한다 | 스파이크 1~3 |

API 키 경로(`OPENAI_API_KEY` + `scripts/image_gen.py` 또는 `gpt-image` CLI)는 **범위 밖**이다. 그쪽은 `size`·`quality`·`background` 인자를 받지만 건당 과금이다. 사용자가 명시적으로 요구할 때만 `gpt-image@wuyoscar-skills` 를 안내한다.

## 인터페이스

```
/orca-team:image [--n <1..8>] [--out <dir>] [--ar <w:h>] [--transparent] [--ref <file>]... [--background] <프롬프트>
```

| 인자 | 기본 | 뜻 |
|---|---|---|
| `--n` | 1 | 장수. 같은 프롬프트로 N번 호출 |
| `--out` | `./generated/` | 결과를 옮길 폴더(`00` D4). 없으면 만든다 |
| `--ar` | 없음 | 비율. 프롬프트 문장으로 변환("가로세로 비율 16:9의 …") |
| `--transparent` | 꺼짐 | 투명 배경 요구 문장 추가 + 알파 검사 |
| `--ref` | 없음 | 참조·편집 대상 이미지. 래퍼 `--image` → `codex exec -i`. v1 포함(`00` D11) |
| `--background` | 꺼짐 | 하네스 백그라운드로. `--n` ≥ 3 이면 자동 |

파일명: `<out>/<slug>-<n>.png`. slug 는 프롬프트 앞 5단어를 kebab-case 로. 이미 있으면 덮어쓰지 않고 `-v2` 를 붙인다(공식 스킬의 비파괴 저장 규칙).

## 프롬프트 뼈대 (`templates/image-prompt.md`)

```
이미지를 하나 생성해라. 내장 이미지 생성 도구(image_gen)를 써라.

{사용자 프롬프트 — 그대로}

{--ar 가 있으면: "가로세로 비율은 {w}:{h} 다."}
{--transparent 가 있으면: "배경은 투명(알파 채널)이어야 한다. 배경색을 깔지 마라."}
{--ref 가 있으면: "첨부한 이미지는 {참조|편집 대상}이다. {바꿀 것}만 바꾸고 나머지는 유지해라."}

이것은 비대화 자동 실행이다. 질문하지 말고 바로 생성해라.
생성이 끝나면 만들어진 PNG 파일의 절대경로를 마지막 줄에 그것만 출력해라.
다른 파일을 만들거나 수정하지 마라.
```

프롬프트 다듬기(구도·조명·재질·글자 따옴표 처리)는 공식 스킬의 `references/prompting.md` 와 `references/sample-prompts.md` 를 참고한다. 사용자 프롬프트가 이미 구체적이면 손대지 않는다.

## 절차 (스킬 본문)

1. 프리플라이트: `codex features list | grep image_generation` 이 `stable true` 인가. 아니면 `codex features enable image_generation` 을 안내하고 멈춘다. **시험 생성은 하지 않는다** — 쿼터를 태운다.
2. 확인하지 않는다(`00` D10). `--n` ≥ 3 이면 장수와 예상 시간(장당 1~3분)을 한 줄 말하고 바로 진행한다.
3. run-dir 을 `…/runs/<repo>/<ts>-image-<slug>-<i>/` 로 만들고 `prompt.txt` 를 쓴다.
4. 래퍼를 부른다. 포그라운드 기본(`--timeout 540`), `--background` 면 하네스 백그라운드.
5. `02` 의 결과 읽기 절차. `result.txt` **마지막 줄**을 경로로 취한다. 백틱·공백을 벗긴다.
6. 파일이 실재하는지 `[ -f "$SRC" ]`. 없으면 `result.txt` 전문을 보여주고 멈춘다 — codex 가 경로를 안 적었거나 생성이 거부된 것이다.
7. `cp "$SRC" "<out>/<slug>-<i>.png"`. 원본은 `$CODEX_HOME` 에 남겨 둔다.
8. 검사: `sips -g pixelWidth -g pixelHeight -g hasAlpha <file>`. `--transparent` 인데 `hasAlpha: no` 면 `remove_chroma_key.py` 를 시도하고, 그래도 안 되면 그 사실을 보고한다.
9. 보고: 저장 경로, 크기, 장수. Claude 의 `Read` 는 이미지를 실제로 본다 — 사용자가 원하면 한 줄 묘사를 덧붙인다. **"예쁜가" 는 판정하지 않는다.** 사람이 본다.

## 실패 처리

- `turn.failed`/거부: 프롬프트 정책 위반(실존 인물·저작권)일 수 있다. `result.txt` 의 거부 사유를 그대로 보여준다. 자동 재시도로 쿼터를 태우지 않는다.
- exit 11(무응답): 이미지 렌더 자체가 수십 초 침묵한다. `--heartbeat 300` 미만으로 내리지 마라.
- 경로 없음(6단계): 프롬프트의 마지막 두 줄이 빠졌는지 먼저 본다.

## 편집 (v1 포함, `00` D11)

`--ref <file>` 로 로컬 이미지를 `-i` 첨부하면 codex 는 그 이미지를 보고 `image_gen` 편집 모드를 쓸 수 있다. 공식 스킬은 "편집 대상은 대화 컨텍스트에 보이는 이미지여야 한다" 고 적는데, `-i` 첨부가 정확히 그것이다. 마스크·정밀 인페인팅은 API 경로 전용이라 범위 밖이다. 편집 프롬프트에는 **불변 조건**(바꾸지 말 것)을 명시한다.
