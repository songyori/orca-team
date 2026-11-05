# orca-team 문구 규칙 구현 계획 (0.5.0)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude 직무 워커가 쓰고 Codex 가 리뷰하는 한국어 서비스 문구·정책 문서·사용자 문서에 공통 규칙(`text-rules.md`), 프로젝트 문구 기준(`voice.md`), 리뷰 절, 추가된 줄만 보는 자동 검사를 넣는다.

**Architecture:** 0.4.0 디자인 금지 목록과 같은 구조다. 규칙 파일을 scaffold 가 설치하고, 직무 정의가 작성 때 참조하게 하고, `orch review` 가 텍스트 대상 파일이 바뀐 태스크의 Codex 프롬프트에 `templates/review-text.md` 절과 새 모듈 `lib/orch/textlint.py` 의 의심 위치를 넣는다.

**Tech Stack:** Python 3.11 표준 라이브러리(`re`, `pathlib`, git 은 `orch.util.git` 경유), unittest, Markdown 템플릿.

**설계:** `docs/superpowers/specs/2026-11-05-orca-team-text-rules-design.md`

## Global Constraints

- 표준 라이브러리만 쓴다. Python 3.11 이상.
- 테스트 전체: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` 이 경고 없이 통과해야 한다(기존 461개 + 추가분). 한 파일만: `cd orca-team/tests && python3 -m unittest test_textlint -v`.
- 버전은 0.5.0 이고 `orca-team/lib/orch/paths.py`, `orca-team/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` 세 곳을 함께 올린다.
- 공개 저장소다. 개인 경로를 넣지 않는다. 커밋 전에 `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'` 결과가 비어야 한다(검사 명령을 적어 둔 줄은 뺀다).
- Markdown 에서 숫자 범위를 `~` 로 쓰지 않는다(`6–9`). 한국어 문법 표기("~를 통해")는 괜찮다.
- 템플릿 문체는 기존 파일을 따른다: 코드 표기 뒤 조사는 띄운다("`x` 를"), 한다체.
- 작업 브랜치는 `feat/text-rules` 다. main 에 직접 커밋하지 않는다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 를 붙인다.

## 파일 구조

| 파일 | 책임 | 태스크 |
|---|---|---|
| `orca-team/lib/orch/textlint.py` (새) | 텍스트 대상 판정, voice.md 용어집 읽기, 줄 검사, 추가된 줄 수집, 50건 상한 | 1, 2 |
| `orca-team/tests/test_textlint.py` (새) | textlint 단위·git 테스트 | 1, 2 |
| `orca-team/templates/text-rules.md` (새) | 작성·리뷰 공통 규칙 | 3 |
| `orca-team/lib/orch/setup.py` | scaffold 가 text-rules.md 설치 | 3 |
| `orca-team/templates/review-text.md` (새), `orca-team/templates/review-prompt.md`, `orca-team/lib/orch/review.py` | 리뷰 프롬프트 문구 절 | 4 |
| `orca-team/templates/roles/{planner,designer,developer}.md`, `orca-team/templates/config.toml`, `orca-team/templates/CLAUDE.section.md`, `orca-team/templates/coordinator.md` | 작성 경로, voice.md, 소유 경로 | 5 |
| `README.md`, `docs/superpowers/specs/2026-10-30-orca-team-design.md`, 버전 세 곳 | 문서·버전 | 6 |
| `docs/superpowers/2026-11-05-orca-team-status.md` (이름 바꿈) | 상태 문서 | 7 |

---

### Task 1: textlint 줄 검사 (`scan_text`, `load_glossary`, `text_targets`)

**Files:**
- Create: `orca-team/lib/orch/textlint.py`
- Create: `orca-team/tests/test_textlint.py`

**Interfaces:**
- Consumes: `orch.globs.matches_any(path: str, patterns) -> bool`
- Produces (Task 2·4 가 쓴다):
  - 상수 `RULES_FILE = ".orchestration/text-rules.md"`, `VOICE_FILE = "docs/planning/voice.md"`, `MAX_BYTES`, `MAX_FINDINGS = 50`
  - `read_target(root: Path, rel: str) -> str | None` — 텍스트 대상이면 파일 내용, 아니면 None
  - `text_targets(root: Path, files: list[str]) -> list[str]`
  - `load_glossary(path: Path) -> list[tuple[str, list[str]]]`
  - `ending_styles(body: str) -> set[str]` — `{"해요체", "하십시오체"}` 의 부분집합
  - `scan_text(rel: str, text: str, added: set[str] | None = None, glossary: list[tuple[str, list[str]]] = ()) -> list[str]` — `"파일:줄: 분류(발췌)"` 목록

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_textlint.py`

```python
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from orch.textlint import MAX_BYTES, ending_styles, load_glossary, scan_text, text_targets

VOICE = """# 문구 기준

## 문체
- 서비스 문구: 해요체

## 용어집
| 쓰는 표기 | 쓰지 않는 표기 | 메모 |
|---|---|---|
| 로그인 | 로그 인, 로긴 | |
| 회원가입 | `회원 가입` | |
| 장바구니 |  | 금지 표기 없음 |

## 다음 절
| 쓰는 표기 | 쓰지 않는 표기 | 메모 |
|---|---|---|
| 무시 | 무시됨 | |
"""

CASES = [
    ('오탈자 "됬"', "결제가 완료됬어요"),
    ('오탈자 "안되요"', "지금은 안되요"),
    ('이중피동 "되어진"', "설정이 저장되어진 상태예요"),
    ('이중피동 "되어졌"', "오류가 발생되어졌어요"),
    ('번역투 "에 있어서"', "보안에 있어서 중요해요"),
    ('상투어 "혁신적"', "혁신적인 경험을 드려요"),
    ('상투어 "Seamless"', "Seamless 결제 경험"),
    ('hedging "라고 할 수 있"', "가장 빠른 방법이라고 할 수 있어요"),
    ('챗봇 잔재 "물론입니다"', "물론입니다. 바로 도와드릴게요"),
    ('쉼표 "그리고,"', "그리고, 저장 버튼을 눌러요"),
    ('쉼표 "며,"', "주문을 확인하며, 결제를 진행해요"),
    ('쉼표 "해서,"', "자주 사용해서, 바로가기에 넣었어요"),
]


class ScanTextTest(unittest.TestCase):
    def scan(self, text, rel="src/app.tsx", glossary=()):
        return scan_text(rel, text, None, list(glossary))

    def test_each_category(self):
        for label, line in CASES:
            with self.subTest(label=label):
                found = self.scan(f'const a = "{line}";\n')
                self.assertTrue(any(f.startswith(f"src/app.tsx:1: {label}(") for f in found), found)

    def test_finding_format(self):
        self.assertEqual(self.scan('const a = "결제가 완료됬어요";\n'),
                         ['src/app.tsx:1: 오탈자 "됬"(const a = "결제가 완료됬어요";)'])

    def test_does_not_flag_normal_text(self):
        for line in ("회원님은 언제든지 탈퇴할 수 있습니다", "결제는 카드를 통해 진행해요", "unlock()", "로그인이 필요"):
            with self.subTest(line=line):
                self.assertEqual(self.scan(f'const a = "{line}";\n'), [])

    def test_skips_code_comments(self):
        self.assertEqual(self.scan("// 완료됬어요\n  * 완료됬어요\n/* 완료됬어요 */\n"), [])
        self.assertEqual(self.scan("# 완료됬어요\n", rel="src/i18n/ko.yaml"), [])
        self.assertEqual(self.scan("<!-- 완료됬어요 -->\n", rel="docs/design/screens/home.html"), [])

    def test_markdown_skips_code_but_not_bullets(self):
        md = "```\n완료됬어요\n```\n`완료됬어요` 는 예시예요\n"
        self.assertEqual(self.scan(md, rel="docs/user/guide.md"), [])
        self.assertEqual(len(self.scan("* 완료됬어요\n", rel="docs/user/guide.md")), 1)

    def test_added_filter(self):
        text = 'const a = "완료됬어요";\nconst b = "또 완료됬어요";\n'
        found = scan_text("src/app.tsx", text, {'const b = "또 완료됬어요";'})
        self.assertEqual([f.split(":")[1] for f in found], ["2"])

    def test_banned_term_inside_preferred_is_skipped(self):
        glossary = [("회원가입", ["가입"])]
        self.assertEqual(self.scan('const a = "회원가입 완료";\n', glossary=glossary), [])
        self.assertEqual(self.scan('const a = "가입 완료";\n', glossary=glossary),
                         ['src/app.tsx:1: 금지 표기 "가입"→"회원가입"(const a = "가입 완료";)'])

    def test_mixed_endings_reported_once_with_minor_lines(self):
        text = "# 안내\n\n저장했어요.\n\n삭제합니다.\n\n복원합니다.\n"
        found = self.scan(text, rel="docs/user/guide.md")
        self.assertEqual(found, ["docs/user/guide.md:3: 높임법 섞임(해요체 1줄, 하십시오체 2줄. 해요체 줄: 3)"])

    def test_mixed_endings_tie_reports_hasipsio_lines(self):
        found = self.scan("저장했어요.\n삭제합니다.\n", rel="docs/user/guide.md")
        self.assertEqual(found, ["docs/user/guide.md:2: 높임법 섞임(해요체 1줄, 하십시오체 1줄. 하십시오체 줄: 2)"])

    def test_long_line_snippet_is_cut(self):
        line = "가" * 100 + "됬"
        found = self.scan(f"{line}\n", rel="docs/user/guide.md")
        snippet = found[0][found[0].index("(") + 1:-1]
        self.assertEqual(len(snippet), 60)
        self.assertTrue(snippet.endswith("…"))


class EndingStylesTest(unittest.TestCase):
    def test_styles(self):
        self.assertEqual(ending_styles('"저장했어요",'), {"해요체"})
        self.assertEqual(ending_styles("<p>확인하십시오</p>"), {"하십시오체"})
        self.assertEqual(ending_styles("| 저장 | 저장했습니다 |"), {"하십시오체"})
        self.assertEqual(ending_styles("로그인이 필요"), set())
        self.assertEqual(ending_styles("다음 단계로 가요. 저장합니다."), {"하십시오체"})


class GlossaryAndTargetTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orca-team-textlint-")).resolve()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")

    def test_load_glossary(self):
        self.put("voice.md", VOICE)
        self.assertEqual(load_glossary(self.root / "voice.md"),
                         [("로그인", ["로그 인", "로긴"]), ("회원가입", ["회원 가입"])])

    def test_load_glossary_bad_or_missing(self):
        self.assertEqual(load_glossary(self.root / "없음.md"), [])
        self.put("a.md", "# 문구 기준\n\n## 문체\n- 해요체\n")
        self.assertEqual(load_glossary(self.root / "a.md"), [])
        self.put("b.md", "## 용어집\n| 표기 | 금지 |\n|---|---|\n| 로그인 | 로그 인 |\n")
        self.assertEqual(load_glossary(self.root / "b.md"), [])
        self.put("c.md", b"## \xff\n")
        self.assertEqual(load_glossary(self.root / "c.md"), [])

    def test_text_targets(self):
        self.put("src/a.tsx", 'const a = "저장";\n')
        self.put("src/b.tsx", "const b = 1;\n")
        self.put("docs/planning/spec.md", "# 기획\n")
        self.put("docs/design/direction.md", "# 방향\n")
        self.put("docs/design/system/README.md", "# 시스템\n")
        self.put("docs/design/screens/home.html", "<p>홈</p>\n")
        self.put("tests/a.test.ts", 'it("저장");\n')
        self.put("src/logo.png", "로고")
        self.put("src/big.md", "가" * (MAX_BYTES // 3 + 1))
        self.put("src/latin1.md", b"\xff\xfe")
        files = ["src/a.tsx", "src/b.tsx", "docs/planning/spec.md", "docs/design/direction.md",
                 "docs/design/system/README.md", "docs/design/screens/home.html", "tests/a.test.ts", "src/logo.png",
                 "src/big.md", "src/latin1.md", "src/missing.tsx"]
        self.assertEqual(text_targets(self.root, files), ["src/a.tsx", "docs/design/screens/home.html"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orch.textlint'`

- [ ] **Step 3: 구현** — `orca-team/lib/orch/textlint.py`

```python
"""문구 자동 검사(orch review). 태스크가 추가한 줄에서 .orchestration/text-rules.md 의 기계적으로 찾을 수 있는 항목의
의심 위치를 찾는다.

결과는 경고다. 정당한 쓰임도 있으므로 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import re
from pathlib import Path

from .globs import matches_any

RULES_FILE = ".orchestration/text-rules.md"
VOICE_FILE = "docs/planning/voice.md"
TEXT_SUFFIXES = {".md", ".mdx", ".txt", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
                 ".json", ".yaml", ".yml", ".po"}
CODE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".yaml", ".yml"}
HTML_SUFFIXES = {".html", ".htm"}
MARKDOWN_SUFFIXES = {".md", ".mdx"}
TEXT_EXCLUDE = ["docs/planning/**", "docs/tasks/**", "docs/reviews/**", "docs/qa/**", "docs/design/*.md",
                "docs/design/system/**/*.md", "tests/**", ".orchestration/**"]
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
                       r"|\b(?:seamless(?:ly)?|unlock|elevate|empower)\b", re.I)),
    ("hedging", re.compile("라고 할 수 있|다고 볼 수 있|것으로 보여지|것으로 보인다고")),
    ("챗봇 잔재", re.compile("도움이 되셨길|도움이 되었길|도움이 되셨으면|궁금한 점이 있으시면 언제든|물론입니다")),
    ("쉼표", re.compile(r"(?<![가-힣])(?:그리고|또한|그러나|따라서|하지만),"
                      r"|(?<=[가-힣])(?:며|고|지만|면서|아서|어서|여서|해서),")),
]
ENDING_RE = re.compile(r"([가-힣]+)[.!?…~]*(?=\s*(?:[\"'`<|]|$))")
HAEYO_EXCEPT = ("필요", "중요", "개요", "주요", "수요")  # "요" 로 끝나지만 해요체 종결이 아닌 낱말
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
        banned = [term.strip() for term in cells[1].split(",") if term.strip()]
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
    """용어집 금지 표기 위치. 같은 자리를 쓰는 표기가 덮으면(금지 "가입" 이 "회원가입" 안) 건너뛴다."""
    hits = []
    for preferred, banned in glossary:
        keep = [(m.start(), m.end()) for m in re.finditer(re.escape(preferred), body)]
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v`
Expected: PASS (ScanTextTest 10개, EndingStylesTest 1개, GlossaryAndTargetTest 3개)

테스트가 정규식 경계 때문에 실패하면 구현을 고친다. 테스트 기대값을 바꾸려면 설계 §8.3 의 의도(예: "회원가입" 은 금지 "가입" 에 걸리지 않는다)와 맞는지 먼저 확인하고, 바꾼 이유를 커밋 메시지에 적는다.

- [ ] **Step 5: 커밋**

```bash
git add orca-team/lib/orch/textlint.py orca-team/tests/test_textlint.py
git commit -m "feat(orca-team): 문구 자동 검사의 줄 검사·용어집·대상 판정을 더한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: textlint 추가된 줄 수집과 묶음 (`added_lines`, `text_lint`)

**Files:**
- Modify: `orca-team/lib/orch/textlint.py`
- Modify: `orca-team/tests/test_textlint.py`

**Interfaces:**
- Consumes: Task 1 의 `read_target`, `load_glossary`, `scan_text`, `VOICE_FILE`, `MAX_FINDINGS`; `orch.util.git(root: Path, *args: str, check: bool = True) -> str`(실패하면 `OrchError`); `orch.paths.OrchError`
- Produces (Task 4 가 쓴다):
  - `added_lines(root: Path, shas: list[str], rel: str) -> set[str] | None` — git 오류면 None
  - `text_lint(root: Path, files: list[str], shas: list[str]) -> list[str]` — 50건 넘으면 마지막이 `"… 외 N건"`

- [ ] **Step 1: 실패하는 테스트 추가** — `orca-team/tests/test_textlint.py`

import 두 줄(`import helpers …`, `from orch.textlint import …`)을 다음으로 바꾼다.

```python
import helpers  # noqa: F401  (lib 경로를 sys.path 에 넣는다)
from helpers import ProjectCase
from orch.textlint import (MAX_BYTES, MAX_FINDINGS, added_lines, ending_styles, load_glossary, scan_text, text_lint,
                           text_targets)
```

`if __name__ == "__main__":` 앞에 다음 클래스를 더한다.

```python
class TextLintGitTest(ProjectCase):
    """태스크 커밋이 추가한 줄만 본다. 같은 체크아웃의 다른 커밋과 기존 줄은 잡지 않는다."""

    def commit(self, rel, text, message):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def test_only_lines_added_by_task_commits(self):
        self.commit("src/app.tsx", 'const a = "기존 문구가 완료됬어요";\n', "chore: base")
        sha = self.commit("src/app.tsx", 'const a = "기존 문구가 완료됬어요";\nconst b = "새 문구도 완료됬어요";\n',
                          "[T-001] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]),
                         ['src/app.tsx:2: 오탈자 "됬"(const b = "새 문구도 완료됬어요";)'])

    def test_lines_from_other_commits_are_ignored(self):
        sha = self.commit("src/app.tsx", 'const a = "첫 문구";\n', "[T-001] feat")
        self.commit("src/app.tsx", 'const a = "첫 문구";\nconst b = "남의 문구가 완료됬어요";\n', "[T-002] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]), [])

    def test_content_line_starting_with_plus_is_kept(self):
        sha = self.commit("docs/user/guide.md", "++ 완료됬어요\n", "[T-001] docs")
        self.assertEqual(added_lines(self.root, [sha], "docs/user/guide.md"), {"++ 완료됬어요"})

    def test_git_error_skips_file(self):
        self.commit("src/app.tsx", 'const a = "완료됬어요";\n', "[T-001] feat")
        self.assertIsNone(added_lines(self.root, ["0" * 40], "src/app.tsx"))
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], ["0" * 40]), [])

    def test_glossary_from_voice_md(self):
        self.commit("docs/planning/voice.md", VOICE, "chore: voice")
        sha = self.commit("src/app.tsx", 'const a = "로그 인 해 주세요";\nconst b = "로그인 해 주세요";\n', "[T-001] feat")
        self.assertEqual(text_lint(self.root, ["src/app.tsx"], [sha]),
                         ['src/app.tsx:1: 금지 표기 "로그 인"→"로그인"(const a = "로그 인 해 주세요";)'])

    def test_non_targets_are_skipped(self):
        sha = self.commit("tests/app.test.ts", 'it("완료됬어요");\n', "[T-001] test")
        self.assertEqual(text_lint(self.root, ["tests/app.test.ts"], [sha]), [])

    def test_cap(self):
        lines = "".join(f'const a{i} = "완료됬어요";\n' for i in range(MAX_FINDINGS + 3))
        sha = self.commit("src/app.tsx", lines, "[T-001] feat")
        found = text_lint(self.root, ["src/app.tsx"], [sha])
        self.assertEqual(len(found), MAX_FINDINGS + 1)
        self.assertEqual(found[-1], "… 외 3건")
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v`
Expected: FAIL — `ImportError: cannot import name 'added_lines' from 'orch.textlint'`

- [ ] **Step 3: 구현** — `orca-team/lib/orch/textlint.py`

`from .globs import matches_any` 아래에 두 줄을 더한다.

```python
from .paths import OrchError
from .util import git
```

파일 끝에 더한다.

```python
def added_lines(root: Path, shas: list[str], rel: str) -> set[str] | None:
    """shas 커밋들이 rel 에 추가한 줄 내용. 파일 머리(--- / +++)는 hunk(@@) 밖이라 빠진다. git 오류면 None."""
    added: set[str] = set()
    for sha in shas:
        try:
            out = git(root, "show", "--format=", "--no-color", "--no-ext-diff", "--no-renames", "-U0", sha, "--", rel)
        except OrchError:
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v`
Expected: PASS (Task 1 테스트 14개 + TextLintGitTest 7개)

- [ ] **Step 5: 커밋**

```bash
git add orca-team/lib/orch/textlint.py orca-team/tests/test_textlint.py
git commit -m "feat(orca-team): 문구 자동 검사가 태스크가 추가한 줄만 보게 한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 규칙 파일 `text-rules.md` 와 설치

**Files:**
- Create: `orca-team/templates/text-rules.md`
- Modify: `orca-team/lib/orch/setup.py` (`SCAFFOLD_FILES`)
- Modify: `orca-team/tests/test_templates.py` (새 테스트)
- Modify: `orca-team/tests/test_setup.py` (`test_creates_files_and_setup_mode` 경로 목록)

**Interfaces:**
- Produces: 프로젝트에 `.orchestration/text-rules.md` 가 생긴다(Task 4 의 문구 절 조건). 절 제목 `## §0 범위` … `## §5 리뷰 심각도`, `## 출처`. 리포트 소절 이름 `` `### 문구 점검` ``(Task 4·5 가 같은 이름을 쓴다).

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_templates.py` 의 `test_design_rules` 다음에 더한다.

```python
    def test_text_rules(self):
        text = (T / "text-rules.md").read_text(encoding="utf-8")
        for heading in ("## §0 범위", "## §1 보존", "## §2 맞춤법·표기", "## §3 문체·용어 일관성", "## §4 AI 티·번역투",
                        "## §5 리뷰 심각도", "## 출처"):
            self.assertIn(f"\n{heading}", text)
        for key in ("docs/planning/voice.md", "`### 문구 점검`", "{name}", "%s", "하여야 한다", "할 수 있다",
                    "A가 아니라 B", "되어지다", "에 있어서", "P1(REQUEST_CHANGES)", "P2:", "P3:", "orch review",
                    ".orchestration/design-rules.md", "https://github.com/epoko77-ai/im-not-ai",
                    "https://github.com/amondnet/yoonmoon", "MIT"):
            self.assertIn(key, text)
        self.assertLess(text.index("## §1 보존"), text.index("## §4 AI 티·번역투"))
        self.assertNotRegex(text, r"\d~\d")
```

`orca-team/tests/test_setup.py` `test_creates_files_and_setup_mode` 의 경로 튜플에서 `".orchestration/design-rules.md",` 바로 뒤에 `".orchestration/text-rules.md",` 를 더한다.

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates.TemplateTest.test_text_rules test_setup.ScaffoldTest.test_creates_files_and_setup_mode -v`
Expected: FAIL — `FileNotFoundError`(text-rules.md 없음)와 `AssertionError: False is not true : .orchestration/text-rules.md`

- [ ] **Step 3: 규칙 파일 작성** — `orca-team/templates/text-rules.md` (아래 내용 그대로)

```markdown
# 문구 규칙 (한국어 텍스트)

서비스 문구, 정책·법률 문서, 사용자용 문서를 쓰는 직무(designer, developer)와 Codex 리뷰가 같은 기준으로 쓴다. 문체·호칭·라벨·용어는 `docs/planning/voice.md`(planner 가 정한 문구 기준)를 따른다. 문구나 문서를 쓰거나 바꾼 태스크는 리포트 현재 라운드 `## r{round}` 절 안의 `### 문구 점검` 소절에 §1–§4 를 지켰는지, 예외와 근거, 용어집에 없는 새 핵심 용어를 적는다. `orch review` 는 태스크가 추가한 줄에서 기계적으로 찾을 수 있는 의심 위치를 리뷰 프롬프트에 넣는다. 판정은 Codex 가 한다.

## §0 범위
- 대상: 서비스 내 문구(UI 라벨·버튼·오류·알림·빈 화면 문구), 정책·법률 문서(이용약관, 개인정보처리방침, 운영 정책), 사용자용 문서(README, 도움말, 가이드, 릴리스 노트)의 한국어 텍스트.
- 제외: 기획 문서(`docs/planning/`), 워커 리포트(`docs/tasks/`), 리뷰 파일(`docs/reviews/`), 코드 주석, 테스트.

## §1 보존 — 다른 모든 규칙보다 우선
1. 고유명사, 수치·단위·날짜·금액, 코드·키·URL·식별자를 바꾸지 않는다.
2. i18n 변수(`{name}`, `{{count}}`, `%s`, `%(name)s`)와 HTML·JSX 태그는 그대로 둔다. 고치기 전과 후의 변수 개수와 이름이 같아야 한다.
3. 정책 문서의 의무·권한·가능성·금지 표현("하여야 한다", "할 수 있다", "할 수 있을 것으로", "하지 않는다")은 뜻을 바꾸지 않는다. §4 의 hedging 규칙보다 우선한다.
4. 주장의 방향·강도·인과를 바꾸지 않는다. 덜어내다가 문장 성분·필수 조사·어미가 빠져 뜻이 흐려지면 되돌린다.

## §2 맞춤법·표기
1. 한글 맞춤법, 띄어쓰기(의존명사 "수·것·때·데", 보조용언, 단위 명사), 표준어, 외래어 표기법을 따른다.
2. 확정 오류는 고친다. 예: "됬다"→"됐다", "몇일"→"며칠", "금새"→"금세", "할께요"→"할게요", "되요"→"돼요", "일일히"→"일일이".
3. 규범 판단이 갈리는 것(보조용언 붙여쓰기 허용 등)은 고치지 않는다.

## §3 문체·용어 일관성
1. voice.md 의 문체(종결체), 호칭, 라벨 형태, 용어집을 따른다. voice.md 가 없으면 한 화면·한 문서 안에서 종결체와 용어 표기를 섞지 않는 것만 지킨다.
2. 높임을 맞춘다. 높이는 호칭과 서술어를 함께 쓴다("회원님이 확인했어요"→"회원님이 확인하셨어요").
3. 버튼·메뉴 라벨은 voice.md 가 정한 한 형태로 쓰고 끝에 마침표를 찍지 않는다("저장"과 "저장하기"를 섞지 않는다).
4. 오류 메시지는 무엇이 잘못됐는지와 사용자가 할 일을 함께 쓴다("비밀번호가 맞지 않아요. 다시 입력하거나 비밀번호를 재설정해 주세요"). 사용자를 탓하지 않고("잘못 입력하셨습니다"), 예외 이름이나 오류 코드만 보여 주지 않는다.
5. 같은 대상은 문서 전체에서 한 이름으로 부른다. 용어집에 없는 새 핵심 용어는 리포트 `### 문구 점검` 에 적는다.

## §4 AI 티·번역투
항상 고친다:
1. 상투어·과장어: 혁신적, 획기적, 최적의, 극대화, 차세대, 시너지, 패러다임, "Seamless", "Unlock", "Elevate"류. 구체적인 사실로 바꾸거나 뺀다.
2. 챗봇 잔재: "물론입니다", "도움이 되셨길 바랍니다", "궁금한 점이 있으시면 언제든".
3. 메타 hedging: "~라고 할 수 있다", "~다고 볼 수 있다", "~것으로 보여진다". 단정하거나 근거를 적는다. 정책 문서의 권한·가능성 표현은 §1 이 우선한다.
4. 이중피동("되어지다", "보여지다", "쓰여지다")과 "~에 있어서".
5. 쉼표: 문두 접속어("그리고", "또한", "그러나", "따라서", "하지만") 뒤 쉼표를 뺀다. 연결어미("-며", "-고", "-지만", "-면서", "-아서/-어서") 바로 뒤 쉼표는 긴 문장을 끊어 읽어야 할 때만 남긴다.
6. "A가 아니라 B" 대구가 한 문서에 2번 이상이면 가장 나은 1번만 남기고 나머지는 평서문으로 쓴다.
7. 영어 병기는 처음 나올 때 한 번만, 용어집이 병기를 정한 용어만 쓴다.
8. 이모지 장식은 `.orchestration/design-rules.md` 1번을 따른다.

반복될 때만 고친다(한 문서·한 화면에 3번 이상): "~를 통해", "~에 대해", 형식명사 "것"("~하는 것이 중요하다"), 무생물 복수 "-들", 문두 "또한·따라서", "~중 하나".

고치지 않는다(과교정 방지):
- "~를 통해", "것이다"가 1–2번 쓰인 경우. 사람 글이 AI 글보다 더 많이 쓰는 표현이다.
- 가이드·문서의 열거 표지(첫째·둘째, 번호 목록)와 짧은 문장. UI 문구와 기술 문서는 짧게 쓰는 것이 맞다. 짧은 문장을 일부러 길게 잇지 않는다.
- 덜어내면 뜻이 모자라게 되는 경우(성분 생략, 개조식 종결, 뜻이 다른 비유어로 바꾸기).

## §5 리뷰 심각도
- P1(REQUEST_CHANGES): 정책 문서의 의무·권한·책임 표현 오류나 변경, i18n 변수·태그 손상, spec·contracts 와 다른 사실·수치, 사용자에게 보이는 §2 확정 오류.
- P2: voice.md 문체·용어 위반, 한 화면 안의 높임법 섞임, 할 일을 알려 주지 않는 오류 메시지, §4 "항상 고친다" 항목.
- P3: §4 "반복될 때만" 항목, 규범 판단이 갈리는 표기, 더 나은 표현 제안, 이 태스크가 바꾸지 않은 기존 텍스트의 문제.

## 출처
§1 의 서법 보존, §4 의 실측 신호("A가 아니라 B" 대구, 연결어미 뒤 쉼표)와 과교정 방지는 im-not-ai(https://github.com/epoko77-ai/im-not-ai, MIT)에서, §2·§3 의 교정·문체 체계와 §4 의 AI 티 분류, 삭제 과교정 가드는 yoonmoon(https://github.com/amondnet/yoonmoon, MIT)에서 가져와 개발 텍스트에 맞게 새로 압축했다. UI 라벨·오류 메시지·i18n 변수 규칙은 orca-team 이 더했다.
```

- [ ] **Step 4: scaffold 에 등록** — `orca-team/lib/orch/setup.py` 의 `SCAFFOLD_FILES` 에서 디자인 규칙 줄 바로 뒤에 한 줄을 더한다.

```python
    ("design-rules.md", ".orchestration/design-rules.md"),
    ("text-rules.md", ".orchestration/text-rules.md"),
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates test_setup -v`
Expected: PASS. `test_commit_instructions_use_pathspec` 도 통과해야 한다(text-rules.md 에 `git commit` 표기가 없다).

- [ ] **Step 6: 커밋**

```bash
git add orca-team/templates/text-rules.md orca-team/lib/orch/setup.py orca-team/tests/test_templates.py orca-team/tests/test_setup.py
git commit -m "feat(orca-team): 작성·리뷰 공통 문구 규칙을 설치한다

im-not-ai 의 서법 보존·실측 신호·과교정 방지와 yoonmoon 의 교정·문체 체계·AI 티 분류를
개발 텍스트에 맞게 압축하고 UI 라벨·오류 메시지·i18n 변수 규칙을 더한다.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Codex 리뷰 프롬프트의 문구 절

**Files:**
- Create: `orca-team/templates/review-text.md`
- Modify: `orca-team/templates/review-prompt.md` (`{design_section}## REVIEW_FILE 형식` 줄)
- Modify: `orca-team/lib/orch/review.py` (import, `design_section` 뒤 `text_section` 추가, `compose_prompt`)
- Modify: `orca-team/tests/test_review.py` (새 클래스), `orca-team/tests/test_templates.py` (`test_placeholders`)

**Interfaces:**
- Consumes: Task 1·2 의 `RULES_FILE`, `text_targets(root, files)`, `text_lint(root, files, shas)`; Task 3 이 설치한 `.orchestration/text-rules.md`
- Produces: `review.text_section(p: Project, files: list[str], shas: list[str]) -> str` — 붙지 않으면 `""`, 붙으면 끝의 빈 줄까지 포함

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_templates.py` `test_placeholders` 끝에 더한다.

```python
        self.assertIn("{design_section}{text_section}## REVIEW_FILE 형식", prompt)
        text = (T / "review-text.md").read_text(encoding="utf-8")
        for key in ("## 문구 기준", "{text_lint}", ".orchestration/text-rules.md", "docs/planning/voice.md",
                    "### 문구 점검", "text-rules §5", "P3", "orch 문구 자동 검사"):
            self.assertIn(key, text)
```

`orca-team/tests/test_review.py` 의 `if __name__ == "__main__":` 앞에 더한다.

```python
class ReviewTextSectionTest(ReviewCase):
    """서비스 문구·문서(한글이 든 텍스트 대상 파일)를 바꾼 태스크 리뷰에만 문구 기준과 orch 문구 자동 검사가 들어간다."""

    def prompt(self):
        return sorted((self.tmp / "cache/orca-team/runs").rglob("prompt.txt"))[-1].read_text(encoding="utf-8")

    def commit_text(self, path, text, task_id="T-001"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"[{task_id}] feat: {path}")

    def test_korean_ui_text_adds_text_section_with_lint(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인이 완료됬어요";\n')
        self.review()
        prompt = self.prompt()
        self.assertIn("## 문구 기준", prompt)
        self.assertIn(".orchestration/text-rules.md", prompt)
        self.assertIn('- src/login.tsx:1: 오탈자 "됬"(export const done = "로그인이 완료됬어요";)\n\n'
                      "## REVIEW_FILE 형식\n", prompt)
        for raw in ("{text_section}", "{text_lint}", "{design_section}"):
            self.assertNotIn(raw, prompt)
        self.assertNotIn("\n\n\n", prompt)
        self.assertLess(prompt.index("## 디자인·UI 기준"), prompt.index("## 문구 기준"))

    def test_clean_korean_text_reports_none(self):
        self.commit_text("src/login.tsx", 'export const done = "로그인했어요";\n')
        self.review()
        section = self.prompt().split("## 문구 기준", 1)[1]
        self.assertIn("orch 문구 자동 검사", section)
        self.assertIn("\n- 없음\n", section)

    def test_no_korean_text_has_no_text_section(self):
        self.commit_text("src/login.ts", "export const done = 1;\n")
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 문구 기준", prompt)
        self.assertNotIn("{text_section}", prompt)
        self.assertNotIn("\n\n\n", prompt)

    def test_excluded_paths_have_no_text_section(self):
        self.write_spec("T-002", role="tester", title="로그인 테스트")
        self.git("add", "--", ".orchestration/tasks/T-002.md")
        self.git("commit", "-q", "-m", "[T-002] spec: 로그인 테스트")
        self.orch("start", "T-002")
        self.commit_text("tests/login.test.ts", 'it("로그인이 완료됬어요", () => {});\n', task_id="T-002")
        self.orch("review", "T-002")
        self.assertNotIn("## 문구 기준", self.prompt())

    def test_project_without_text_rules_has_no_text_section(self):
        """0.4.x 프로젝트(.orchestration/text-rules.md 없음)는 문구 기준 없이 지금 흐름 그대로 리뷰한다."""
        self.git("rm", "-q", "--", ".orchestration/text-rules.md")
        self.git("commit", "-q", "-m", "chore: 0.4 프로젝트")
        self.commit_text("src/login.tsx", 'export const done = "로그인이 완료됬어요";\n')
        self.review()
        prompt = self.prompt()
        self.assertNotIn("## 문구 기준", prompt)
        self.assertNotIn("text-rules.md", prompt)
        self.assertNotIn("\n\n\n", prompt)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review.ReviewTextSectionTest test_templates.TemplateTest.test_placeholders -v`
Expected: FAIL — 프롬프트에 `## 문구 기준` 이 없고, `review-text.md` 가 없다.

- [ ] **Step 3: 리뷰 절 템플릿** — `orca-team/templates/review-text.md` (끝에 줄바꿈 하나)

```markdown
## 문구 기준
- 이 태스크가 바꾼 서비스 문구·정책 문서·사용자 문서를 `.orchestration/text-rules.md` 와 `docs/planning/voice.md`(있으면)로 본다. 심각도는 text-rules §5 를 따른다. P1 은 REQUEST_CHANGES 사유다.
- 이 태스크가 바꾼 텍스트만 지적한다. 원래 있던 문제는 P3 로만 적는다.
- 워커 리포트 마지막 라운드의 `### 문구 점검` 이 실제와 맞는지 본다.

orch 문구 자동 검사(이 태스크가 추가한 줄의 의심 위치. 정당한 쓰임도 있으니 직접 열어 보고 판단한다):
{text_lint}
```

- [ ] **Step 4: 프롬프트 자리** — `orca-team/templates/review-prompt.md` 에서 다음 줄을

```
{design_section}## REVIEW_FILE 형식
```

다음으로 바꾼다.

```
{design_section}{text_section}## REVIEW_FILE 형식
```

- [ ] **Step 5: review.py** — `from .lint import DESIGN_ROLES, design_lint` 아래에 한 줄을 더한다.

```python
from .textlint import RULES_FILE, text_lint, text_targets
```

`design_section` 함수 바로 뒤에 더한다.

```python
def text_section(p: Project, files: list[str], shas: list[str]) -> str:
    """서비스 문구·문서를 바꾼 태스크 리뷰에 넣는 문구 기준과 orch 문구 자동 검사 결과(templates/review-text.md).
    직무와 무관하다. 다음 절과 사이의 빈 줄까지 붙여 돌려준다. text-rules.md 가 없는 이전 버전(0.4.x) 프로젝트와
    한글이 든 텍스트 대상 파일을 바꾸지 않은 태스크는 빈 문자열."""
    if not (p.root / RULES_FILE).is_file() or not text_targets(p.root, files):
        return ""
    found = text_lint(p.root, files, shas)
    listing = "\n".join(f"- {item}" for item in found) if found else "- 없음"
    return render((TEMPLATES / "review-text.md").read_text(encoding="utf-8"), {"text_lint": listing}) + "\n"
```

`compose_prompt` 의 `render(...)` 값 사전 마지막 항목

```python
        "design_section": design_section(p, cfg, doc.role, files)})
```

을 다음으로 바꾼다.

```python
        "design_section": design_section(p, cfg, doc.role, files),
        "text_section": text_section(p, files, [sha for sha, _ in commits])})
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd orca-team/tests && python3 -m unittest test_review test_templates -v`
Expected: PASS. 기존 `ReviewDesignSectionTest` 4개도 그대로 통과한다(그 테스트 파일들은 한글이 없거나 `tests/**` 라 문구 절이 붙지 않는다).

- [ ] **Step 7: 커밋**

```bash
git add orca-team/templates/review-text.md orca-team/templates/review-prompt.md orca-team/lib/orch/review.py orca-team/tests/test_review.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 문구를 바꾼 태스크의 Codex 리뷰에 문구 기준과 자동 검사를 넣는다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 작성 경로 — planner 문구 기준, designer·developer 규칙, 소유 경로

**Files:**
- Modify: `orca-team/templates/roles/planner.md` (점검표, 점검표 뒤 문단, `## 확정` 첫 문단, `## 산출물`, 태스크 그래프 규칙)
- Modify: `orca-team/templates/roles/designer.md` (`## 입력`, 새 `## 문구 규칙` 절)
- Modify: `orca-team/templates/roles/developer.md` (`## 입력`, 새 `## 문구 규칙` 절)
- Modify: `orca-team/templates/config.toml` (`[roles.developer]` owns)
- Modify: `orca-team/templates/CLAUDE.section.md` ("위치")
- Modify: `orca-team/templates/coordinator.md` (워커 질문 답하기 표 행, 직무 목록의 planner 줄)
- Modify: `orca-team/tests/test_templates.py`, `orca-team/tests/test_tasks.py`, `orca-team/tests/test_review.py`

**Interfaces:**
- Consumes: Task 3 의 `.orchestration/text-rules.md`, `` `### 문구 점검` ``; Task 1 의 voice.md 형식(`## 용어집` 절, 표 머리 `| 쓰는 표기 | 쓰지 않는 표기 | 메모 |`)
- Produces: 기본 developer owns `["src/**", "README.md", "docs/user/**", "docs/policy/**"]`

- [ ] **Step 1: 실패하는 테스트 작성** — `orca-team/tests/test_templates.py`

`test_planner_interview_rules` 의 첫 key 튜플에 `"docs/planning/voice.md", "산출물 다섯 개"` 를 더하고, 점검표 항목 튜플의 `"범위 밖"` 바로 앞에 다음 항목을 더한다.

```python
"문구 기준(서비스 문구·정책 문서·사용자 문서의 문체, 사용자 호칭, 버튼·메뉴 라벨 형태, 핵심 용어 표기)",
```

`test_spec_section_13_design_director` 바로 앞에 다음 테스트들을 더한다.

```python
    def test_planner_writes_voice_doc(self):
        text = (T / "roles" / "planner.md").read_text(encoding="utf-8")
        for key in ("# 문구 기준", "## 용어집", "| 쓰는 표기 | 쓰지 않는 표기 | 메모 |", "서비스 문구: 해요체",
                    "정책 문서: 하십시오체", "사용자 문서: 해요체", "용어집 표", "`docs/policy/**`", "`docs/user/**`",
                    "`README.md`"):
            self.assertIn(key, text)

    def test_writers_follow_text_rules(self):
        for name in ("designer", "developer"):
            with self.subTest(role=name):
                text = (T / "roles" / f"{name}.md").read_text(encoding="utf-8")
                for key in ("## 문구 규칙", ".orchestration/text-rules.md", "docs/planning/voice.md",
                            "`### 문구 점검`", "voice.md 는 고치지 않는다"):
                    self.assertIn(key, text)

    def test_developer_owns_user_and_policy_docs(self):
        cfg = tomllib.loads((T / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["roles"]["developer"]["owns"], ["src/**", "README.md", "docs/user/**", "docs/policy/**"])

    def test_locations_name_text_rules(self):
        claude = (T / "CLAUDE.section.md").read_text(encoding="utf-8")
        for key in ("`.orchestration/text-rules.md`", "voice.md"):
            self.assertIn(key, claude)
        coordinator = (T / "coordinator.md").read_text(encoding="utf-8")
        self.assertIn("`docs/planning/voice.md`", coordinator)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_templates -v 2>&1 | tail -20`
Expected: FAIL — 새 테스트 4개와 `test_planner_interview_rules`

- [ ] **Step 3: planner.md**

점검표에서 `- 범위 밖` 바로 앞에 한 줄을 더한다.

```markdown
- 문구 기준(서비스 문구·정책 문서·사용자 문서의 문체, 사용자 호칭, 버튼·메뉴 라벨 형태, 핵심 용어 표기)
```

"디자인 톤과 레퍼런스 조사는 묻지 않는다. …" 문단 바로 뒤에 빈 줄과 다음 문단을 더한다.

```markdown
문구 기준의 문체는 질문 1개로 묻는다. 추천은 서비스 문구 해요체, 정책 문서 하십시오체, 사용자 문서 해요체, 호칭 생략, 라벨 명사형이다. 서비스 성격상 답이 자명하면 묻지 않고 자동 결정한다. 용어집은 따로 묻지 않는다. spec 의 기능·화면·데이터 이름에서 뽑아 확정 질문 요약에 넣는다.
```

`## 확정` 첫 문단에서
- `점검표가 끝나면 아래 산출물 네 개를 먼저 쓴다.` 를 `점검표가 끝나면 아래 산출물 다섯 개를 먼저 쓴다.` 로,
- `기획 핵심, 자동 결정 목록, 태스크 그래프 표(key, 직무, 선행 key)` 를 `기획 핵심, 자동 결정 목록, 용어집 표(쓰는 표기, 쓰지 않는 표기), 태스크 그래프 표(key, 직무, 선행 key)` 로 바꾼다.

`## 산출물` 의 `` - `docs/planning/interview.md`: … `` 줄 바로 뒤에 다음을 더한다(바깥 네 백틱은 이 계획 문서의 표기다. 파일에는 안쪽 내용만 넣는다).

````markdown
- `docs/planning/voice.md`: 문구 기준. 작성 워커와 Codex 리뷰, orch 문구 자동 검사가 읽는다. 절 제목과 용어집 표 머리를 아래 그대로 쓴다. 증분 기획이면 기존 파일을 읽고 바뀌는 부분만 고친다.

```markdown
# 문구 기준

## 문체
- 서비스 문구: 해요체
- 정책 문서: 하십시오체
- 사용자 문서: 해요체
- 호칭: 생략. 필요하면 "회원님"

## 라벨
- 버튼·메뉴: 명사형("저장", "삭제")

## 용어집
| 쓰는 표기 | 쓰지 않는 표기 | 메모 |
|---|---|---|
| 로그인 | 로그 인, 로긴 | |
| 회원가입 | 회원 가입 | |
```
````

태스크 그래프 규칙 목록에서 `- UI 기반 developer 태스크(…)는 director 태스크에 의존한다.` 줄 바로 뒤에 더한다.

```markdown
- 정책·법률 문서(`docs/policy/**`)와 사용자용 문서(`README.md`, `docs/user/**`)는 developer 태스크로 만들고 owns 에 그 경로를 적는다. `constraints` 에 `docs/planning/voice.md` 와 `.orchestration/text-rules.md` 를 따른다고 적는다.
```

- [ ] **Step 4: designer.md, developer.md**

두 파일 모두 `## 입력` 목록 끝에 한 줄을 더한다.

```markdown
- `.orchestration/text-rules.md`(문구 규칙)와 `docs/planning/voice.md`(문구 기준: 문체·호칭·라벨·용어집. 있으면)
```

designer.md 는 `## 산출물` 바로 앞에 다음 절과 빈 줄을 더한다.

```markdown
## 문구 규칙
- 화면 시안에 넣는 문구(라벨·버튼·오류·알림·빈 화면 문구)는 `.orchestration/text-rules.md` 와 `docs/planning/voice.md` 를 지킨다. 실제 서비스에 그대로 들어갈 문구로 쓴다.
- 문구가 있는 화면을 만들거나 바꾼 태스크는 리포트 현재 라운드 `## r{round}` 절 안에 `### 문구 점검` 소절을 둔다: text-rules §1–§4 를 지켰는지, 예외와 근거, 용어집에 없는 새 핵심 용어.
- voice.md 는 고치지 않는다. 용어집에 없는 용어가 꼭 필요하면 `### 문구 점검` 에 적는다.
```

developer.md 는 `## 산출물` 바로 앞에 다음 절과 빈 줄을 더한다.

```markdown
## 문구 규칙
- 서비스 문구(UI 라벨·버튼·오류·알림, i18n 파일), 정책 문서(`docs/policy/`), 사용자 문서(`README.md`, `docs/user/`)를 쓰거나 바꾸면 `.orchestration/text-rules.md` 와 `docs/planning/voice.md` 를 지킨다. 디자인 시안의 문구가 있으면 그대로 옮긴다.
- 그런 태스크의 리포트 현재 라운드 `## r{round}` 절 안에 `### 문구 점검` 소절을 둔다: text-rules §1–§4 를 지켰는지, 예외와 근거, 용어집에 없는 새 핵심 용어.
- voice.md 는 고치지 않는다. 용어집에 없는 용어가 꼭 필요하면 `### 문구 점검` 에 적는다.
```

- [ ] **Step 5: config.toml, CLAUDE.section.md, coordinator.md**

`orca-team/templates/config.toml` `[roles.developer]` 의 owns 를 바꾼다.

```toml
owns = ["src/**", "README.md", "docs/user/**", "docs/policy/**"]
```

`orca-team/templates/CLAUDE.section.md` "위치" 에서 `` - 기획: `docs/planning/`(spec.md, contracts.md, tasks.toml, interview.md) `` 를 `` - 기획: `docs/planning/`(spec.md, contracts.md, tasks.toml, interview.md, voice.md) `` 로 바꾸고, 그 줄 바로 뒤에 더한다.

```markdown
- 문구: `.orchestration/text-rules.md`(작성·리뷰 공통 규칙), `docs/planning/voice.md`(planner 가 정한 문체·호칭·라벨·용어집)
```

`orca-team/templates/coordinator.md` 에서 두 곳을 바꾼다.
- 워커 질문(question) 표 행의 `` `docs/planning/interview.md` 와 spec·직무 정의·project.md 로 답한다 `` → `` `docs/planning/interview.md`, `docs/planning/voice.md` 와 spec·직무 정의·project.md 로 답한다 ``
- 직무 목록 planner 줄의 `` 태스크 그래프(`docs/planning/tasks.toml`) `` → `` 태스크 그래프(`docs/planning/tasks.toml`), 문구 기준(`docs/planning/voice.md`) ``

- [ ] **Step 6: 기본 owns 변경으로 깨지는 테스트 고치기**

`orca-team/tests/test_tasks.py` `StartTest.test_fixed_placement_and_role_models` 의 마지막 단언을 바꾼다.

```python
        self.assertEqual((st["status"], st["round"], st["dispatch_id"], st["owns"]),
                         ("working", 1, "ctx_1", ["src/**", "README.md", "docs/user/**", "docs/policy/**"]))
```

`orca-team/tests/test_review.py` 에서 `README.md` 를 "owns 밖 파일"로 쓰던 네 테스트(`test_ownership_violation_is_auto_request_changes`, `test_lookalike_subject_is_not_skipped`, `test_spec_subject_with_product_file_is_checked`, `test_uncommitted_change_outside_owns_does_not_block`)의 `"README.md"` 를 모두 `"NOTES.md"` 로 바꾼다.

Run: `grep -n '"README.md"' orca-team/tests/test_review.py`
Expected(바꾼 뒤): 출력 없음.

- [ ] **Step 7: 전체 테스트**

Run: `bash orca-team/tests/run.sh 2>&1 | tail -5`
Expected: `OK`. 다른 테스트가 기본 developer owns `["src/**"]` 를 가정해 실패하면, "owns 밖" 을 보려던 테스트는 경로를 `NOTES.md` 로, 기본값 자체를 확인하던 테스트는 기대값을 새 목록으로 바꾼다. 고친 테스트를 커밋 메시지 본문에 적는다.

- [ ] **Step 8: 커밋**

```bash
git add orca-team/templates/roles/planner.md orca-team/templates/roles/designer.md orca-team/templates/roles/developer.md orca-team/templates/config.toml orca-team/templates/CLAUDE.section.md orca-team/templates/coordinator.md orca-team/tests/test_templates.py orca-team/tests/test_tasks.py orca-team/tests/test_review.py
git commit -m "feat(orca-team): planner 문구 기준과 designer·developer 문구 규칙을 더한다

developer 기본 owns 에 README.md, docs/user/**, docs/policy/** 를 더해
owns 밖 커밋을 보던 리뷰 테스트는 NOTES.md 를 쓴다.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 0.5.0 버전과 문서

**Files:**
- Modify: `orca-team/lib/orch/paths.py` (`VERSION`), `orca-team/.claude-plugin/plugin.json` (`version`), `.claude-plugin/marketplace.json` (`version`)
- Modify: `orca-team/tests/test_cli.py` (`test_version`)
- Modify: `README.md` (`## 보안 주의` 앞)
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md` (파일 끝 = §13 끝)
- Modify: `orca-team/tests/test_templates.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`orca-team/tests/test_cli.py` `test_version` 의 단언을 바꾼다.

```python
        self.assertIn("orca-team 0.5.0", self.orch("--version").stdout)
```

`orca-team/tests/test_templates.py` `TemplateTest` 끝에 더한다.

```python
    def test_readme_documents_text_rules(self):
        text = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("## 문구 규칙", ".orchestration/text-rules.md", "docs/planning/voice.md", "### 문구 점검",
                    "https://github.com/epoko77-ai/im-not-ai", "https://github.com/amondnet/yoonmoon",
                    "0.4.x 로 만든 프로젝트는", "orca-team/templates/text-rules.md",
                    "docs/superpowers/specs/2026-11-05-orca-team-text-rules-design.md",
                    "docs/superpowers/plans/2026-11-05-orca-team-text-rules.md"):
            self.assertIn(key, text)
        self.assertLess(text.index("## 디자인 디렉터"), text.index("## 문구 규칙"))
        self.assertLess(text.index("## 문구 규칙"), text.index("## 보안 주의"))

    def test_spec_section_13_text_rules(self):
        text = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = text[text.index("## 13."):]
        for key in ("### 문구 규칙 (2026-11-05, 0.5.0)", "text_lint", "{text_section}", "NOTES.md", "파일 복사"):
            self.assertIn(key, section)
```

- [ ] **Step 2: 실패 확인**

Run: `cd orca-team/tests && python3 -m unittest test_cli test_templates 2>&1 | tail -15`
Expected: FAIL — `test_version`, `test_readme_documents_text_rules`, `test_spec_section_13_text_rules`

- [ ] **Step 3: 버전 세 곳** — 값만 바꾼다.

- `orca-team/lib/orch/paths.py`: `VERSION = "0.5.0"`
- `orca-team/.claude-plugin/plugin.json`: `"version": "0.5.0",`
- `.claude-plugin/marketplace.json`: `"version": "0.5.0",`

- [ ] **Step 4: README** — `README.md` 의 `## 보안 주의` 바로 앞에 다음 절과 빈 줄을 더한다.

```markdown
## 문구 규칙

- 서비스 내 문구(UI 라벨·버튼·오류·알림), 정책·법률 문서(`docs/policy/`), 사용자용 문서(`README.md`, `docs/user/`)의 한국어 텍스트를 규칙 하나(`.orchestration/text-rules.md`)로 쓰고 리뷰한다. 규칙은 공개 한국어 윤문 스킬 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)(서법 보존, 실측 AI 신호, 과교정 방지)와 [yoonmoon](https://github.com/amondnet/yoonmoon)(교정·문체 체계, AI 티 분류)에서 개발 텍스트에 필요한 것만 가져와 압축하고(둘 다 MIT), UI 라벨·오류 메시지·i18n 변수 규칙을 더했다.
- planner 가 인터뷰 점검표 "문구 기준"으로 서비스 문체(기본 해요체), 정책 문서 문체(기본 하십시오체), 호칭, 라벨 형태, 용어집을 정해 `docs/planning/voice.md` 에 남긴다. 용어집은 확정 질문 요약에서 함께 승인받는다.
- designer·developer 는 문구를 쓰거나 바꾸면 이 규칙과 voice.md 를 지키고 리포트에 `### 문구 점검` 을 적는다. 정책·사용자 문서는 developer 가 맡는다(기본 owns 에 `README.md`, `docs/user/**`, `docs/policy/**`).
- `orch review` 는 태스크가 한글이 든 서비스 문구·문서 파일을 바꿨으면 리뷰 프롬프트에 "문구 기준" 절을 붙인다. Codex 는 text-rules §5 심각도로 판정하고(정책 문서의 의무·권한 표현 변경, i18n 변수 손상, 사용자에게 보이는 확정 맞춤법 오류는 P1), 이 태스크가 바꾼 텍스트만 지적한다. orch 는 그 전에 태스크가 추가한 줄에서 확정 오탈자, 이중피동, "에 있어서", 상투어, 메타 hedging, 챗봇 잔재, 접속어·연결어미 뒤 쉼표, voice.md 금지 표기, 높임법 섞임의 의심 위치를 찾아 넣는다(자동 불합격은 아니다).
- 0.4.x 로 만든 프로젝트는 `.orchestration/text-rules.md` 가 없어 지금 흐름 그대로 돈다. 새 흐름을 쓰려면 다음을 한다.
  1. `orca-team/templates/text-rules.md` 를 `.orchestration/text-rules.md` 로 복사한다. 이것만으로 Codex 리뷰에 문구 기준 절이 붙는다.
  2. 워커가 작성에도 쓰게 하려면 `.orchestration/roles/planner.md`, `designer.md`, `developer.md` 와 `.orchestration/coordinator.md` 를 `orca-team/templates/` 의 같은 파일로 바꾸고, `CLAUDE.md` 의 `<!-- orca-team -->` 절을 `orca-team/templates/CLAUDE.section.md` 로 바꾼다. 정책·사용자 문서를 developer 가 맡게 하려면 `.orchestration/config.toml` 의 `[roles.developer]` owns 에 `"README.md", "docs/user/**", "docs/policy/**"` 를 더한다. 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
  3. voice.md 는 다음 기획(증분 기획 포함)에서 planner 가 만든다. 없는 동안은 용어집 검사를 건너뛴다.
- 설계: `docs/superpowers/specs/2026-11-05-orca-team-text-rules-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-05-orca-team-text-rules.md`.
```

- [ ] **Step 5: 기본 설계 §13** — `docs/superpowers/specs/2026-10-30-orca-team-design.md` 는 §13 이 마지막 절이다. 파일 끝에 빈 줄과 다음을 더한다.

```markdown
### 문구 규칙 (2026-11-05, 0.5.0)

설계: `2026-11-05-orca-team-text-rules-design.md`. 구현에서 정한 것:
- `textlint.text_lint` 는 태스크 커밋마다 `git show -U0` 의 hunk 안 `+` 줄을 모으고, 내용이 같은 현재 줄만 본다. 파일 머리(`+++`)는 hunk 밖이라 빠지고 `++` 로 시작하는 내용 줄은 남는다. 오탈자 정규식은 긴 표기부터 맞춰 "안되요" 를 "되요" 로 두 번 세지 않는다.
- 설계 §8.3 에 더해 이중피동은 "되어졌"(과거형), 쉼표는 "해서," 도 잡는다.
- 높임법 섞임은 줄 끝 한글 낱말(뒤에 따옴표·`<`·`|`·줄 끝만 오는)로 판정한다. "필요·중요·개요·주요·수요" 로 끝나는 낱말은 해요체로 보지 않는다. 두 종결체 줄 수가 같으면 하십시오체 쪽 줄을 적는다.
- 리뷰 프롬프트 자리는 `{design_section}{text_section}` 순서다. 문구 절은 직무와 무관하게 `.orchestration/text-rules.md` 가 있고 한글이 든 텍스트 대상 파일이 바뀌었을 때만 붙는다. 디자인 절의 "orch 자동 검사" 와 구분하려고 "orch 문구 자동 검사" 라고 부른다.
- 기본 config 의 developer owns 에 `README.md`, `docs/user/**`, `docs/policy/**` 를 더해, owns 밖 커밋을 확인하던 리뷰 테스트는 `NOTES.md` 를 쓴다.
- 업그레이드는 README 가 파일 복사로 안내한다. 설계 §9 의 init 재실행과 결과가 같다(scaffold 는 없는 파일만 만든다).
```

- [ ] **Step 6: 전체 테스트와 개인 경로 검사**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3; git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'; echo "grep exit=$?"`
Expected: 경고 없는 `OK` 와 `grep exit=1`(검사 명령 줄 밖에는 일치 없음)

- [ ] **Step 7: 커밋**

```bash
git add orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json orca-team/tests/test_cli.py orca-team/tests/test_templates.py README.md docs/superpowers/specs/2026-10-30-orca-team-design.md
git commit -m "orca-team 0.5.0: 문구 규칙

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 마무리 검증과 상태 문서

**Files:**
- Rename: `docs/superpowers/2026-11-01-orca-team-status.md` → `docs/superpowers/2026-11-05-orca-team-status.md`

- [ ] **Step 1: 전체 테스트(래퍼 포함)**

Run: `bash orca-team/tests/run.sh --wrapper 2>&1 | tail -5`
Expected: Python 테스트 `OK`, 래퍼 회귀 테스트 통과. `Ran N tests` 의 N 을 기록한다.

- [ ] **Step 2: 실제 샘플로 자동 검사 확인** — 저장소 루트에서 실행한다. 임시 저장소를 만들고 지운다.

```bash
repo=$(pwd); tmp=$(mktemp -d); cd "$tmp" && git init -q && git -c user.name=t -c user.email=t@e commit -q --allow-empty -m init \
&& mkdir -p src docs/policy \
&& printf 'export const msg = {\n  saved: "저장이 완료됬어요",\n  error: "오류가 발생되어졌습니다",\n  hint: "그리고, 다시 시도해 주세요",\n};\n' > src/ko.ts \
&& printf '# 이용약관\n\n회사는 서비스를 변경할 수 있습니다.\n본 약관에 있어서 용어의 정의는 다음과 같습니다.\n' > docs/policy/terms.md \
&& git add -A && git -c user.name=t -c user.email=t@e commit -q -m "[T-001] feat" \
&& PYTHONPATH="$repo/orca-team/lib" python3 -c "
import subprocess
from pathlib import Path
from orch.textlint import text_lint
sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
print('\n'.join(text_lint(Path('.'), ['src/ko.ts', 'docs/policy/terms.md'], [sha])))
"; cd "$repo"; rm -rf "$tmp"
```

Expected(정확히 이 5줄, 이 순서):

```
src/ko.ts:2: 오탈자 "됬"(saved: "저장이 완료됬어요",)
src/ko.ts:3: 이중피동 "되어졌"(error: "오류가 발생되어졌습니다",)
src/ko.ts:3: 높임법 섞임(해요체 2줄, 하십시오체 1줄. 하십시오체 줄: 3)
src/ko.ts:4: 쉼표 "그리고,"(hint: "그리고, 다시 시도해 주세요",)
docs/policy/terms.md:4: 번역투 "에 있어서"(본 약관에 있어서 용어의 정의는 다음과 같습니다.)
```

정책 문서의 "할 수 있습니다"(권한 표현)는 잡히지 않아야 한다. 출력이 다르면 원인을 찾아 Task 1·2 에 테스트를 더하고 고친다.

- [ ] **Step 3: 상태 문서 갱신**

```bash
git mv docs/superpowers/2026-11-01-orca-team-status.md docs/superpowers/2026-11-05-orca-team-status.md
```

`docs/superpowers/2026-11-05-orca-team-status.md` 를 고친다.
- 제목: `# orca-team 현재 상태 (2026-11-05, 0.5.0)`
- 버전 표 끝에 행을 더한다: `| 0.5.0 | 문구 규칙: 작성·리뷰 공통 text-rules.md, planner 문구 기준 voice.md, Codex 리뷰 문구 절, 추가된 줄만 보는 자동 검사(textlint) | `specs/2026-11-05-orca-team-text-rules-design.md`, `plans/2026-11-05-orca-team-text-rules.md` |`
- "검증" 의 테스트 수를 Step 1 의 N 으로, 날짜를 2026-11-05 로 바꾼다.
- "실환경에서 확인하지 않은 흐름" 에 더한다: `- 0.5.0 문구 규칙: planner 의 문구 기준 질문과 voice.md, 워커의 문구 점검, Codex 리뷰의 문구 절을 실제 Orca·Codex 로 돌려 보지 않았다(단위·통합 테스트와 자동 검사 샘플 실행만 했다).`

Run: `grep -rn '2026-11-01-orca-team-status' --include=*.md . | grep -v '^./docs/superpowers/plans/'`
Expected: 출력 없음. 남은 참조가 있으면 새 파일 이름으로 바꾼다.

- [ ] **Step 4: 커밋**

```bash
git add -A docs/superpowers/
git commit -m "docs: orca-team 0.5.0 상태를 정리한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: 메인 세션에 넘길 것** — 메인 세션이 하는 일이다(워커는 하지 않는다).
  - 저장소 밖 메모리의 상태 문서 경로를 새 이름으로 바꾼다.
  - superpowers:requesting-code-review 로 `main..feat/text-rules` 브랜치 전체를 리뷰받는다.
  - 실환경 확인 여부를 사용자에게 묻는다(시험 프로젝트·Orca 재시작은 사용자 승인이 필요하다).
