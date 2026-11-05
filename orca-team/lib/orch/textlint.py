"""문구 자동 검사(orch review). 태스크가 추가한 줄에서 .orchestration/text-rules.md 의 기계적으로 찾을 수 있는 항목의
의심 위치를 찾는다.

결과는 경고다. 정당한 쓰임도 있으므로 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import re
from pathlib import Path

from .globs import matches_any
from .paths import OrchError
from .util import git

RULES_FILE = ".orchestration/text-rules.md"
VOICE_FILE = "docs/planning/voice.md"
TEXT_SUFFIXES = {".md", ".mdx", ".txt", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
                 ".json", ".yaml", ".yml", ".po", ".arb", ".dart", ".strings", ".xcstrings", ".xml", ".kt", ".swift"}
CODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".yaml", ".yml", ".dart", ".kt", ".swift", ".strings"}
HTML_SUFFIXES = {".html", ".htm", ".xml"}
MARKDOWN_SUFFIXES = {".md", ".mdx"}
TEXT_EXCLUDE = ["docs/planning/**", "docs/tasks/**", "docs/reviews/**", "docs/qa/**", "docs/design/*.md",
                "docs/design/system/**/*.md", "tests/**", ".orchestration/**", "**/*.test.*", "**/*.spec.*", "**/__tests__/**",
                "e2e/**"]
MAX_BYTES = 1 << 20  # 이보다 큰 파일(번들·데이터)은 대상이 아니다
MAX_FINDINGS = 50  # 리뷰 프롬프트가 커지지 않게 이만큼만 싣는다
SNIPPET = 60
MAX_MINOR_LINES = 5  # 높임법 섞임에 적는 적은 쪽 줄 번호 수

HANGUL_RE = re.compile("[가-힣]")
COMMENT_PREFIXES = ("//", "/*", "*", "#", "<!--")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
TYPOS = ("됬", "몇일", "어떻해", "금새", "왠만", "오랫만", "역활", "희안", "설겆이", "할께", "일일히", "깨끗히", "틈틈히",
         "안되요", "되요")
CHECKS: list[tuple[str, re.Pattern]] = [
    ("오탈자", re.compile("|".join(sorted(TYPOS, key=len, reverse=True)))),  # 긴 것 먼저: "안되요" 를 "되요" 로 두 번 세지 않는다
    ("이중피동", re.compile("(?:되어|보여|쓰여|불려)(?:지|진|짐|져|졌)")),
    ("번역투", re.compile(r"에 있어(?:서|\s)")),
    ("상투어", re.compile(r"혁신적|획기적|최적의|극대화|차세대|시너지|패러다임|게임 체인저"
                       r"|\b(?:seamless(?:ly)?|empower)\b", re.I)),
    ("hedging", re.compile("라고 할 수 있|다고 볼 수 있|것으로 보여지|것으로 보인다고")),
    ("챗봇 잔재", re.compile("도움이 되셨길|도움이 되었길|도움이 되셨으면|궁금한 점이 있으시면 언제든|물론입니다")),
    ("쉼표", re.compile(r"(?<![가-힣])(?:그리고|또한|그러나|따라서|하지만),"
                      r"|(?<=[가-힣])(?:며|지만|면서|아서|어서|여서|해서),|(?<=[하되있없않이었았했겠])고,")),
]
ENDING_RE = re.compile(r"([가-힣]+)[.!?…~]*(?=\s*(?:[\"'`<|]|$))")
HAEYO_EXCEPT = ("필요", "중요", "개요", "주요", "수요", "아니요", "좋아요")  # "요" 로 끝나지만 해요체 종결이 아닌 낱말
PAREN_TAIL_RE = re.compile(r"\s*\([^()]*\)\s*$")  # 용어집 쓰지 않는 표기 칸의 끝 괄호 조건: "지우기(라벨)"
GLOSSARY_HEAD = ("쓰는 표기", "쓰지 않는 표기")


def snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET else text[:SNIPPET - 1] + "…"


def read_target(root: Path, rel: str) -> str | None:
    """rel 이 텍스트 대상(확장자, 제외 경로 아님, 1MB 이하 UTF-8, 한글 있음)이면 내용, 아니면 None."""
    if Path(rel).suffix.lower() not in TEXT_SUFFIXES or matches_any(rel, TEXT_EXCLUDE):
        return None
    path = root / rel
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if HANGUL_RE.search(text) else None


def text_targets(root: Path, files: list[str]) -> list[str]:
    return [rel for rel in files if read_target(root, rel) is not None]


def load_glossary(path: Path) -> list[tuple[str, list[str]]]:
    """voice.md `## 용어집` 절의 첫 표에서 (쓰는 표기, [쓰지 않는 표기…]). 없거나 형식이 다르면 빈 목록."""
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return []
    start = next((i for i, line in enumerate(lines) if line.strip() == "## 용어집"), None)
    if start is None:
        return []
    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("|"):
            rows.append([cell.strip().strip("`").strip() for cell in stripped.strip("|").split("|")])
        elif rows:
            break
    if len(rows) < 2 or tuple(rows[0][:2]) != GLOSSARY_HEAD:
        return []
    out = []
    for cells in rows[2:]:
        if len(cells) < 2:
            continue
        banned = [term for term in (PAREN_TAIL_RE.sub("", t).strip() for t in cells[1].split(",")) if term]
        if cells[0] and banned:
            out.append((cells[0], banned))
    return out


def visible_lines(rel: str, text: str) -> list[tuple[int, str, str]]:
    """검사할 줄 (줄 번호, 원래 줄, 검사할 내용). 코드 주석 줄, Markdown 코드 블록·인라인 코드, 한글 없는 줄은 뺀다."""
    suffix = Path(rel).suffix.lower()
    out, fence = [], False
    for number, line in enumerate(text.split("\n"), 1):
        body = line
        if suffix in MARKDOWN_SUFFIXES:
            if FENCE_RE.match(line):
                fence = not fence
                continue
            if fence:
                continue
            body = INLINE_CODE_RE.sub("", line)
        elif suffix in CODE_SUFFIXES and line.strip().startswith(COMMENT_PREFIXES):
            continue
        elif suffix in HTML_SUFFIXES and line.strip().startswith("<!--"):
            continue
        if HANGUL_RE.search(body):
            out.append((number, line, body))
    return out


def ending_styles(body: str) -> set[str]:
    """줄 끝(뒤에 따옴표·`<`·`|`·줄 끝만 오는) 한글 낱말의 종결체."""
    styles = set()
    for match in ENDING_RE.finditer(body):
        word = match.group(1)
        if word.endswith(("니다", "니까", "십시오")):
            styles.add("하십시오체")
        elif word.endswith("요") and not word.endswith(HAEYO_EXCEPT):
            styles.add("해요체")
    return styles


def banned_hits(body: str, glossary) -> list[tuple[int, str]]:
    """용어집 금지 표기 위치. 어느 행이든 쓰는 표기가 그 자리를 덮으면(금지 "가입" 이 "회원가입" 안, 금지 "할일" 이 "오늘할일" 안) 건너뛴다."""
    keep = [(m.start(), m.end()) for preferred, _ in glossary for m in re.finditer(re.escape(preferred), body)]
    hits = []
    for preferred, banned in glossary:
        for term in banned:
            for m in re.finditer(re.escape(term), body):
                if not any(s <= m.start() and m.end() <= e for s, e in keep):
                    hits.append((m.start(), f'금지 표기 "{term}"→"{preferred}"'))
    return hits


def scan_text(rel: str, text: str, added: set[str] | None = None,
              glossary: list[tuple[str, list[str]]] = ()) -> list[str]:
    """한 파일의 의심 위치를 줄 순서로 "파일:줄: 분류(발췌)" 목록으로 돌려준다.
    added 가 있으면 내용이 그 집합에 있는 줄만 본다(태스크가 추가한 줄)."""
    found: list[tuple[int, int, str]] = []
    styles: dict[str, list[int]] = {"해요체": [], "하십시오체": []}
    for number, line, body in visible_lines(rel, text):
        if added is not None and line not in added:
            continue
        hits = [(m.start(), f'{name} "{m.group(0)}"') for name, regex in CHECKS for m in regex.finditer(body)]
        hits += banned_hits(body, glossary)
        for pos, what in sorted(hits):
            found.append((number, pos, f"{rel}:{number}: {what}({snippet(line)})"))
        for style in ending_styles(body):
            styles[style].append(number)
    haeyo, hasipsio = styles["해요체"], styles["하십시오체"]
    if haeyo and hasipsio:
        name, minor = ("해요체", haeyo) if len(haeyo) < len(hasipsio) else ("하십시오체", hasipsio)
        numbers = ", ".join(str(n) for n in minor[:MAX_MINOR_LINES])
        found.append((minor[0], 1 << 30, f"{rel}:{minor[0]}: 높임법 섞임(해요체 {len(haeyo)}줄, "
                                          f"하십시오체 {len(hasipsio)}줄. {name} 줄: {numbers})"))
    return [item for _, _, item in sorted(found, key=lambda f: (f[0], f[1]))]


def added_lines(root: Path, shas: list[str], rel: str) -> set[str] | None:
    """shas 커밋들이 rel 에 추가한 줄 내용. 파일 머리(--- / +++)는 hunk(@@) 밖이라 빠진다. git 오류면 None."""
    added: set[str] = set()
    for sha in shas:
        try:
            out = git(root, "show", "--format=", "--no-color", "--no-ext-diff", "--no-renames", "-U0", sha, "--", f":(literal){rel}")
        except (OrchError, UnicodeDecodeError):
            return None
        in_hunk = False
        for line in out.split("\n"):
            if line.startswith("diff --git"):
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line.startswith("+"):
                added.add(line[1:])
    return added


def text_lint(root: Path, files: list[str], shas: list[str]) -> list[str]:
    """files(저장소 기준 상대 경로) 중 텍스트 대상에서 shas 가 추가한 줄의 의심 위치 목록.
    git 오류가 난 파일은 건너뛴다. MAX_FINDINGS 를 넘으면 마지막 항목이 "… 외 N건" 이다."""
    glossary = load_glossary(root / VOICE_FILE)
    found: list[str] = []
    for rel in files:
        text = read_target(root, rel)
        if text is None:
            continue
        added = added_lines(root, shas, rel)
        if added is None:
            continue
        found += scan_text(rel, text, added, glossary)
    if len(found) > MAX_FINDINGS:
        found = [*found[:MAX_FINDINGS], f"… 외 {len(found) - MAX_FINDINGS}건"]
    return found
