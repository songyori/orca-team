"""디자인 자동 검사(orch review). 태스크가 바꾼 파일에서 AI 티 패턴(.orchestration/design-rules.md §1 의 1–3번)의 의심 위치를 찾는다.

결과는 경고다. 블록 인용처럼 정당한 쓰임이 있으므로 불합격 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import bisect
import colorsys
import re
from pathlib import Path

from .globs import matches_any

DESIGN_ROLES = ("director", "designer", "developer")  # 리뷰 프롬프트에 디자인 기준과 이 검사 결과를 넣는 직무
LINT_SUFFIXES = {".html", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".md", ".json"}
LINT_EXCLUDE = ["docs/design/references.md", "docs/planning/**", "docs/tasks/**", "docs/reviews/**"]
MAX_BYTES = 1 << 20  # 이보다 큰 파일(번들·데이터)은 건너뛴다
MAX_FINDINGS = 50  # 리뷰 프롬프트가 커지지 않게 이만큼만 싣는다
SNIPPET = 60

# BMP 에서 기본으로 이모지로 보이는(Emoji_Presentation) 문자. ✓ ★ ❯ ➜ ⬆ ▶ 같은 텍스트 기호는 넣지 않는다.
EMOJI_BMP = ("\u231a-\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd-\u25fe\u2614-\u2615\u2648-\u2653\u267f\u2693\u26a1"
             "\u26aa-\u26ab\u26bd-\u26be\u26c4-\u26c5\u26ce\u26d4\u26ea\u26f2-\u26f3\u26f5\u26fa\u26fd\u2705"
             "\u270a-\u270b\u2728\u274c\u274e\u2753-\u2755\u2757\u2795-\u2797\u27b0\u27bf\u2b1b-\u2b1c\u2b50\u2b55")
# 앞 문자를 이모지로 보이게 하는 U+FE0F(이모지 표시 선택자)가 붙은 공백 아닌 문자(▶️ ☀️), U+1F000–U+1FAFF, EMOJI_BMP.
# 공백 뒤에 홀로 남은 FE0F 는 보이는 것이 없어 잡지 않는다.
EMOJI_RE = re.compile(f"[^\\s]\ufe0f|[\U0001F000-\U0001FAFF{EMOJI_BMP}]")
BORDER_CSS_RE = re.compile(r"\bborder-(?:left|inline-start)\s*:\s*([^;{}\n]+)", re.I)
BORDER_JS_RE = re.compile(r"\bborder(?:Left|InlineStart)\s*:\s*[\"'`]([^\"'`\n]+)[\"'`]")
BORDER_WIDTH_CSS_RE = re.compile(r"\bborder-(?:left|inline-start)-width\s*:\s*([^;{}\n]+)", re.I)
BORDER_WIDTH_JS_RE = re.compile(r"\bborder(?:Left|InlineStart)Width\s*:\s*(?:(\d*\.?\d+)(?![\w.])|[\"'`]([^\"'`\n]+)[\"'`])")
WIDTH_RE = re.compile(r"(?<![\w.#-])(\d*\.?\d+)(px|rem|em)\b", re.I)
# 너비 낱말. 앞뒤가 낱말 문자·하이픈이면(var(--thick) 같은 사용자 정의 속성 이름) 낱말로 보지 않는다.
WIDTH_KEYWORD_RE = re.compile(r"(?<![\w-])(thin|medium|thick)(?![\w-])", re.I)
WIDTH_KEYWORD_PX = {"thin": 1.0, "medium": 3.0, "thick": 5.0}
VISIBLE_RE = re.compile(r"\b(?:solid|dashed|dotted|double|groove|ridge|inset|outset)\b|#[0-9a-f]{3,8}\b"
                        r"|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color-mix|var)\(", re.I)
# 선이 보이지 않게 하는 낱말. invisible() 이 괄호 밖에서 홀로 선 낱말만 본다(사용자 정의 속성 이름·함수 인자는 뺀다).
INVISIBLE_RE = re.compile(r"(?<![\w-])(?:transparent|none|hidden)(?![\w-])", re.I)
# Tailwind 왼쪽(border-l)·논리 시작(border-s) 너비: N 2~8 또는 임의 값 [3px]·[0.25rem](2px 이상은 left_border 가 본다).
TW_LEFT_WIDTH_RE = re.compile(r"(?<![\w-])border-[ls]-(?:[2-8]|\[([^\]\s]+)\])(?![\w-])")
TW_BORDER_COLOR_RE = re.compile(r"(?<![\w-])border-(?:[ls]-)?(?:[a-z]+-\d{2,3}|black|white|\[#[0-9a-fA-F]{3,8}\])"
                                r"(?![\w-])")
TW_BLUE_RE = re.compile(r"(?<![\w-])(?:from|via|to)-(?:blue|indigo|sky)-\d{2,3}(?![\w-])")
TW_PURPLE_RE = re.compile(r"(?<![\w-])(?:from|via|to)-(?:purple|violet|fuchsia)-\d{2,3}(?![\w-])")
GRADIENT_RE = re.compile(r"\b(?:repeating-)?(?:linear|radial|conic)-gradient\(", re.I)
COLOR_TOKEN_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch)\([^()]*\)|\b[a-zA-Z]+\b")
# oklch(L C H [/ alpha]). L 은 % 또는 0~1, C 는 숫자(또는 %: 100% = 0.4), H 는 도(deg 생략 가능).
OKLCH_RE = re.compile(r"oklch\(\s*(\d*\.?\d+)(%?)\s+(\d*\.?\d+)(%?)\s+(\d*\.?\d+)(?:deg)?\s*(?:/\s*\d*\.?\d+%?\s*)?\)")
BLUE_NAMES = {"blue", "royalblue", "dodgerblue", "deepskyblue", "cornflowerblue", "steelblue", "navy", "mediumblue",
              "darkblue", "midnightblue"}
PURPLE_NAMES = {"purple", "violet", "indigo", "blueviolet", "darkviolet", "mediumpurple", "rebeccapurple", "darkorchid",
                "mediumorchid", "orchid", "plum", "magenta", "fuchsia", "slateblue", "mediumslateblue", "darkslateblue"}


def snippet(text: str) -> str:
    return " ".join(text.split())[:SNIPPET]


def max_px(value: str) -> float:
    """값에 적힌 길이(px·rem·em)와 너비 낱말(thin 1px, medium 3px, thick 5px) 중 가장 큰 것을 px 로.
    rem·em 은 16px 로 본다. 길이가 없으면 0."""
    lengths = [float(number) * (16 if unit.lower() in ("rem", "em") else 1) for number, unit in WIDTH_RE.findall(value)]
    lengths += [WIDTH_KEYWORD_PX[word.lower()] for word in WIDTH_KEYWORD_RE.findall(value)]
    return max(lengths, default=0.0)


def invisible(value: str) -> bool:
    """선 값의 괄호 밖에 transparent·none·hidden 이 홀로 선 낱말로 있다. 함수 인자(var(--none),
    var(--accent, transparent))는 보지 않는다. 괄호 깊이를 한 번 훑어 괄호 밖 글자만 남긴다(선형)."""
    depth, outside = 0, []
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif depth == 0:
            outside.append(ch)
    return bool(INVISIBLE_RE.search("".join(outside)))


def left_border(line: str) -> bool:
    """다음 중 하나가 있다.
    - 2px 이상(medium·thick 포함)이고 보이는 선(선 모양·색. 홀로 선 transparent·none·hidden 이 없다)이 있는
      border-left·border-inline-start 선언(CSS, JSX 인라인 스타일)
    - 너비만 따로 준 2px 이상(medium·thick 포함)의 border-left-width·border-inline-start-width
      (JSX borderLeftWidth 등은 숫자 2 이상도)
    - 한 줄에 Tailwind border-l·border-s 너비(2~8, 2px 이상 임의 값)와 테두리 색"""
    for match in (*BORDER_CSS_RE.finditer(line), *BORDER_JS_RE.finditer(line)):
        value = match.group(1)
        if max_px(value) >= 2 and VISIBLE_RE.search(value) and not invisible(value):
            return True
    if any(max_px(match.group(1)) >= 2 for match in BORDER_WIDTH_CSS_RE.finditer(line)):
        return True
    for match in BORDER_WIDTH_JS_RE.finditer(line):
        number, text = match.groups()
        if (float(number) if number is not None else max_px(text)) >= 2:
            return True
    wide = any(value is None or max_px(value) >= 2 for value in (m.group(1) for m in TW_LEFT_WIDTH_RE.finditer(line)))
    return wide and bool(TW_BORDER_COLOR_RE.search(line))


def parse_color(token: str) -> tuple[float, float, float] | None:
    """(색상각 0~360, 채도 0~1, 밝기 0~1). hex(3·4·6·8자리), rgb(), hsl() 만 읽는다."""
    if token.startswith("#"):
        digits = token[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits[:3])
        elif len(digits) in (6, 8):
            digits = digits[:6]
        else:
            return None
        r, g, b = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    else:
        numbers = re.findall(r"\d*\.?\d+", token)
        if not token.startswith(("rgb", "hsl")) or len(numbers) < 3:
            return None
        if token.startswith("hsl"):
            return float(numbers[0]) % 360, min(float(numbers[1]), 100) / 100, min(float(numbers[2]), 100) / 100
        r, g, b = (min(float(n), 255) / 255 for n in numbers[:3])
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, sat, light


def oklch_family(token: str) -> str | None:
    """oklch() 색 하나의 계열. 채도 C 가 0.05 보다 작으면(무채색에 가깝다) None. 색상각 200~280° 파랑, 280~330° 보라
    (Tailwind v4 의 sky·blue·indigo 는 파랑, violet·purple·fuchsia 는 보라)."""
    match = OKLCH_RE.fullmatch(token)
    if not match:
        return None
    light, light_pct, chroma, chroma_pct, hue = match.groups()
    if float(light) > (100 if light_pct else 1) or float(chroma) * (0.004 if chroma_pct else 1) < 0.05:
        return None
    hue_deg = float(hue) % 360
    if 200 <= hue_deg < 280:
        return "blue"
    if 280 <= hue_deg < 330:
        return "purple"
    return None


def hue_family(token: str) -> str | None:
    """색 하나가 파랑 계열이면 "blue", 보라 계열이면 "purple". 무채색에 가깝거나 읽지 못하면 None."""
    low = token.lower()
    if low in BLUE_NAMES:
        return "blue"
    if low in PURPLE_NAMES:
        return "purple"
    if low.startswith("oklch("):
        return oklch_family(low)
    color = parse_color(low)
    if color is None:
        return None
    hue, sat, light = color
    if sat < 0.25 or not 0.12 < light < 0.92:
        return None
    if 190 <= hue < 245:
        return "blue"
    if 245 <= hue < 300:
        return "purple"
    return None


def gradient_body(text: str, start: int) -> str:
    """start(여는 괄호 바로 뒤)부터 짝이 맞는 닫는 괄호 앞까지. 닫히지 않으면 500자에서 자른다."""
    depth, end = 1, min(len(text), start + 500)
    for i in range(start, end):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:end]


def scan(rel: str, text: str) -> list[str]:
    """한 파일의 의심 위치를 줄 순서로 "파일:줄: 패턴(발췌)" 목록으로 돌려준다."""
    found: list[tuple[int, str]] = []
    line_starts = [0]  # 줄마다 시작 위치. 그라디언트 매치의 줄 번호를 bisect 로 찾는다(매치마다 처음부터 세지 않는다)
    for number, line in enumerate(text.split("\n"), 1):
        line_starts.append(line_starts[-1] + len(line) + 1)
        emoji = EMOJI_RE.search(line)
        if emoji:
            found.append((number, f"이모지({emoji.group(0)})"))
        if left_border(line):
            found.append((number, f"왼쪽 줄 강조({snippet(line)})"))
        if TW_BLUE_RE.search(line) and TW_PURPLE_RE.search(line):
            found.append((number, f"파랑·보라 그라디언트({snippet(line)})"))
    for match in GRADIENT_RE.finditer(text):
        body = gradient_body(text, match.end())
        if {"blue", "purple"} <= {hue_family(token) for token in COLOR_TOKEN_RE.findall(body)}:
            number = bisect.bisect_right(line_starts, match.start())
            shown = snippet(match.group(0) + body + ")")
            found.append((number, f"파랑·보라 그라디언트({shown})"))
    return [f"{rel}:{number}: {what}" for number, what in sorted(found, key=lambda item: item[0])]


def cap(found: list[str]) -> list[str]:
    """MAX_FINDINGS 를 넘으면 앞 MAX_FINDINGS 개와 "… 외 N건" 한 줄로 줄인다."""
    if len(found) <= MAX_FINDINGS:
        return found
    return [*found[:MAX_FINDINGS], f"… 외 {len(found) - MAX_FINDINGS}건"]


def read_source(root: Path, rel: str) -> str | None:
    """root 아래 rel 파일의 UTF-8 텍스트. 파일이 아니거나 1MB 를 넘거나 읽을 수 없으면 None."""
    path = root / rel
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def design_findings(root: Path, files: list[str]) -> list[str]:
    """files(저장소 기준 상대 경로) 중 검사 대상의 의심 위치 목록(상한 없음). 대상 확장자가 아니거나, 제외 경로거나,
    없거나, 1MB 를 넘거나, UTF-8 이 아니면 건너뛴다."""
    found: list[str] = []
    for rel in files:
        if Path(rel).suffix.lower() not in LINT_SUFFIXES or matches_any(rel, LINT_EXCLUDE):
            continue
        text = read_source(root, rel)
        if text is not None:
            found += scan(rel, text)
    return found


def design_lint(root: Path, files: list[str]) -> list[str]:
    """design_findings 를 MAX_FINDINGS 로 자른 것. 넘으면 마지막 항목이 "… 외 N건" 이다."""
    return cap(design_findings(root, files))
