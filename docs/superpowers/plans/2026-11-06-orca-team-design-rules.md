# orca-team 디자인 규칙 (0.5.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** orca-team 의 디자인 규칙을 "AI 티 금지 목록"에서 접근성·색·글자·레이아웃·표면·동작·상태 규칙까지 넓히고, Codex 디자인 리뷰를 변경분 중심으로 바꾸고, 지운 줄 접근성 신호 자동 검사를 더한다.

**Architecture:** 규칙은 `templates/design-rules.md` 하나(§0–§7)에 둔다. 리뷰 방식은 `templates/review-design.md` 에 둔다. diff 기반 자동 검사는 새 모듈 `lib/orch/designdiff.py` 이고, diff 읽기는 `textlint.diff_lines` 로 문구 검사와 함께 쓴다. `review.design_section` 이 내용 검사(`lint.design_findings`)와 diff 검사를 이어 붙여 50건으로 자른다.

**Tech Stack:** Python 3.11 표준 라이브러리, unittest, git CLI.

## Global Constraints

- 설계: `docs/superpowers/specs/2026-11-06-orca-team-design-rules-design.md`. 이 계획과 다르면 설계가 우선이고, 고친 점은 리포트에 적는다.
- 표준 라이브러리만 쓴다. 새 의존성을 넣지 않는다.
- 테스트는 실제 Orca 에 닿지 않는다(`tests/helpers.py` 가 가짜 orca 를 기본으로 둔다).
- 전체 테스트: 저장소 루트에서 `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3`. 경고 없이 `OK` 여야 한다.
- 한 파일 테스트: `cd orca-team/tests && python3 -m unittest test_designdiff -v` 처럼 모듈 이름으로 돌린다.
- 마크다운에 숫자 범위를 물결표(`6~9`)로 쓰지 않는다. en dash(`6–9`)를 쓴다. GitHub 에서 취소선이 된다.
- 공개 저장소다. 개인 경로·조직 이름을 넣지 않는다. 커밋 전마다 `git grep -n -I -P '/Users/|/private/tmp|~/Workspace' | grep -v 'git grep'` 결과에 새 줄이 없어야 하고, 태스크 브리프가 주는 추가 점검 명령도 돌린다.
- `.superpowers/` 는 커밋하지 않는다. `git add` 는 태스크가 바꾼 경로만 적는다(`git add -A` 금지).
- `~/.claude/plugins` 와 Orca(터미널·탭·앱)를 건드리지 않는다.
- 커밋 메시지는 한국어 한 줄 제목(`feat(orca-team): …`, `docs: …`) 뒤에 빈 줄과 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 를 둔다.
- 템플릿 문서는 한국어 한다체로 쓴다. 이 계획에 적은 문장을 그대로 옮긴다.

---

## 파일 구조

| 파일 | 할 일 |
|---|---|
| `orca-team/lib/orch/textlint.py` | `diff_lines` 추가, `added_lines` 를 그 위에 다시 쓴다 (Task 1) |
| `orca-team/lib/orch/lint.py` | `cap`, `design_findings` 추가, `design_lint` 를 그 둘로 다시 쓴다 (Task 2), 모듈 설명 (Task 4) |
| `orca-team/lib/orch/designdiff.py` | 새 파일: diff 기반 디자인 검사 `design_diff_lint` (Task 2) |
| `orca-team/lib/orch/review.py` | `guarded`, `bullets`, `design_section(…, shas)`, `compose_prompt` (Task 3) |
| `orca-team/templates/review-design.md` | Codex 리뷰 방식 5–13번 (Task 3) |
| `orca-team/templates/design-rules.md` | 규칙 §0–§7 (Task 4) |
| `orca-team/templates/roles/director.md`, `designer.md`, `developer.md` | `### 디자인 점검`, §3·§4·§7 반영 (Task 4) |
| `orca-team/templates/text-rules.md` | §4 의 8번 참조 (Task 4) |
| 버전 파일 3개, `README.md`, 기본 설계 §13, 상태 문서 | 0.5.2 (Task 5) |
| 테스트 | `tests/test_textlint.py`, `tests/test_lint.py`, `tests/test_designdiff.py`(새), `tests/test_review.py`, `tests/test_templates.py`, `tests/test_cli.py` |

---

### Task 1: diff 읽기 공유 (`textlint.diff_lines`)

**Files:**
- Modify: `orca-team/lib/orch/textlint.py` (`added_lines`, 지금 176–192행)
- Test: `orca-team/tests/test_textlint.py` (`TextLintGitTest`)

**Interfaces:**
- Produces: `textlint.diff_lines(root: Path, shas: list[str], rel: str) -> tuple[list[str], list[str]] | None` — (추가한 줄 목록, 지운 줄 목록). 같은 줄이 여러 번이면 그 수만큼 들어 있다. git 오류(`OrchError`, `UnicodeDecodeError`)면 None.
- `textlint.added_lines(root, shas, rel) -> set[str] | None` 는 이름·반환형·동작이 그대로다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`orca-team/tests/test_textlint.py` 의 `from orch.textlint import (…)` 목록에 `diff_lines` 를 더한다(`added_lines,` 바로 뒤). 줄이 120자를 넘으면 괄호 안에서 줄을 바꾼다.

`TextLintGitTest` 클래스의 `test_git_error_skips_file` 바로 뒤에 다음 테스트 세 개를 넣는다. `self.commit(rel, text, message)` 는 이 클래스에 이미 있다(파일을 쓰고 add·commit 한 뒤 SHA 를 돌려준다).

```python
    def test_diff_lines_counts_added_and_removed(self):
        self.commit("src/app.css", "a\nb\nb\n", "chore: base")
        sha = self.commit("src/app.css", "a\nc\nc\n", "[T-001] feat: css")
        self.assertEqual(diff_lines(self.root, [sha], "src/app.css"), (["c", "c"], ["b", "b"]))

    def test_diff_lines_sums_commits_and_skips_file_headers(self):
        sha1 = self.commit("src/app.css", "--x\n", "[T-001] feat: one")
        sha2 = self.commit("src/app.css", "++y\n", "[T-001] feat: two")
        self.assertEqual(diff_lines(self.root, [sha1, sha2], "src/app.css"), (["--x", "++y"], ["--x"]))

    def test_diff_lines_git_error_is_none(self):
        self.assertIsNone(diff_lines(self.root, ["0" * 40], "src/app.css"))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'diff_lines'` 로 실패.

- [ ] **Step 3: 구현한다**

`orca-team/lib/orch/textlint.py` 의 `added_lines` 함수 전체를 다음 두 함수로 바꾼다.

```python
def diff_lines(root: Path, shas: list[str], rel: str) -> tuple[list[str], list[str]] | None:
    """shas 커밋들이 rel 에 추가한 줄과 지운 줄 내용(같은 줄이 여러 번이면 그 수만큼). 파일 머리(--- / +++)는
    hunk(@@) 밖이라 빠진다. git 오류면 None."""
    added: list[str] = []
    removed: list[str] = []
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
                added.append(line[1:])
            elif in_hunk and line.startswith("-"):
                removed.append(line[1:])
    return added, removed


def added_lines(root: Path, shas: list[str], rel: str) -> set[str] | None:
    """shas 커밋들이 rel 에 추가한 줄 내용. git 오류면 None."""
    lines = diff_lines(root, shas, rel)
    return None if lines is None else set(lines[0])
```

- [ ] **Step 4: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_textlint -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: 커밋한다**

공개 저장소 grep(Global Constraints)을 돌린 뒤:

```bash
git add orca-team/lib/orch/textlint.py orca-team/tests/test_textlint.py
git commit -m "feat(orca-team): 태스크 diff 의 추가한 줄과 지운 줄을 함께 읽는 diff_lines 를 둔다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: diff 기반 디자인 검사 (`designdiff.py`)

**Files:**
- Modify: `orca-team/lib/orch/lint.py` (`design_lint`, 지금 198–215행)
- Create: `orca-team/lib/orch/designdiff.py`
- Create: `orca-team/tests/test_designdiff.py`
- Modify: `orca-team/tests/test_lint.py`

**Interfaces:**
- Consumes: `textlint.diff_lines` (Task 1). `lint.LINT_SUFFIXES`, `lint.LINT_EXCLUDE`, `lint.MAX_BYTES`, `lint.snippet` (이미 있다).
- Produces:
  - `lint.cap(found: list[str]) -> list[str]` — `MAX_FINDINGS`(50)를 넘으면 앞 50개와 `"… 외 N건"` 한 줄.
  - `lint.design_findings(root: Path, files: list[str]) -> list[str]` — 지금 `design_lint` 의 내용 검사, 상한 없음.
  - `lint.design_lint(root, files) -> list[str]` — `cap(design_findings(root, files))`. 기존 동작 그대로.
  - `designdiff.design_diff_lint(root: Path, files: list[str], shas: list[str]) -> list[str]` — 상한 없음. 항목 형식:
    - `"{rel}: 지운 줄에서 {이름} {n}개가 줄었다(대체가 있는지 확인)"`
    - `"{rel}:{줄}: 확대 막기({조각})"`
    - `"{rel}:{줄}: 포커스 표시 제거({조각}, 파일에 focus-visible 없음)"`

- [ ] **Step 1: `lint` 의 상한 분리 테스트를 쓴다**

`orca-team/tests/test_lint.py` 의 import 줄을 다음으로 바꾼다.

```python
from orch.lint import DESIGN_ROLES, MAX_BYTES, MAX_FINDINGS, cap, design_findings, design_lint, hue_family, scan
```

`DesignLintTest` 클래스 끝에 다음 테스트를 넣는다(`self.put(rel, text)` 는 이 클래스에 이미 있다).

```python
    def test_design_findings_has_no_cap_and_cap_cuts(self):
        lines = MAX_FINDINGS + 5
        rel = self.put("src/ui/many.css", "linear-gradient(blue, purple)\n" * lines)
        self.assertEqual(len(design_findings(self.root, [rel])), lines)
        capped = design_lint(self.root, [rel])
        self.assertEqual(len(capped), MAX_FINDINGS + 1)
        self.assertEqual(capped[-1], "… 외 5건")
        self.assertEqual(cap(["a", "b"]), ["a", "b"])
```

- [ ] **Step 2: `designdiff` 테스트를 쓴다**

`orca-team/tests/test_designdiff.py` 를 만든다.

```python
import unittest

from helpers import ProjectCase
from orch.designdiff import design_diff_lint

LOST = "지운 줄에서 {name} {n}개가 줄었다(대체가 있는지 확인)"


class DesignDiffTest(ProjectCase):
    def commit(self, rel, text, message="[T-001] feat: ui"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", rel)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def lint(self, files, *shas):
        return design_diff_lint(self.root, list(files), list(shas))

    def test_removed_aria_label_is_reported(self):
        self.commit("src/Close.tsx", '<button aria-label="닫기"><X /></button>\n<button aria-label="열기"><O /></button>\n',
                    "chore: base")
        sha = self.commit("src/Close.tsx", "<button><X /></button>\n<button><O /></button>\n")
        self.assertEqual(self.lint(["src/Close.tsx"], sha), [f"src/Close.tsx: {LOST.format(name='aria-label', n=2)}"])

    def test_moved_or_rewrapped_signal_is_not_reported(self):
        self.commit("src/Close.tsx", '<button aria-label="닫기" className="a">x</button>\n', "chore: base")
        sha = self.commit("src/Close.tsx", '<button\n  className="b"\n  aria-label="닫기"\n>x</button>\n')
        self.assertEqual(self.lint(["src/Close.tsx"], sha), [])

    def test_labelledby_is_counted_apart_from_label(self):
        self.commit("src/Dialog.tsx", '<div role="dialog" aria-labelledby="t">\n', "chore: base")
        sha = self.commit("src/Dialog.tsx", '<div role="dialog" aria-label="설정">\n')
        self.assertEqual(self.lint(["src/Dialog.tsx"], sha),
                         [f"src/Dialog.tsx: {LOST.format(name='aria-labelledby', n=1)}"])

    def test_motion_and_korean_wrap_signals(self):
        self.commit("src/title.css", ".title { word-break: keep-all; }\n"
                    "@media (prefers-reduced-motion: no-preference) { .a { transition: scale 150ms; } }\n", "chore: base")
        css = self.commit("src/title.css", ".title { }\n.a { transition: scale 150ms; }\n")
        self.commit("src/Title.tsx", '<h1 className="break-keep">제목</h1>\n', "chore: base")
        tsx = self.commit("src/Title.tsx", "<h1>제목</h1>\n")
        self.assertEqual(self.lint(["src/title.css", "src/Title.tsx"], css, tsx), [
            f"src/title.css: {LOST.format(name='prefers-reduced-motion', n=1)}",
            f"src/title.css: {LOST.format(name='keep-all', n=1)}",
            f"src/Title.tsx: {LOST.format(name='break-keep', n=1)}",
        ])

    def test_app_signals(self):
        self.commit("lib/done.dart", 'Text("완료", semanticsLabel: "완료됨")\nIcon(Icons.close, semanticLabel: "닫기")\n',
                    "chore: base")
        dart = self.commit("lib/done.dart", 'Text("완료")\nIcon(Icons.close)\n')
        self.commit("app/Close.kt", 'Image(painter, contentDescription = "닫기")\n', "chore: base")
        kt = self.commit("app/Close.kt", "Image(painter)\n")
        self.assertEqual(self.lint(["lib/done.dart", "app/Close.kt"], dart, kt), [
            f"lib/done.dart: {LOST.format(name='semanticsLabel', n=1)}",
            f"lib/done.dart: {LOST.format(name='semanticLabel', n=1)}",
            f"app/Close.kt: {LOST.format(name='contentDescription', n=1)}",
        ])

    def test_zoom_block_in_added_lines(self):
        html = self.commit("src/index.html", '<meta name="viewport" content="width=device-width, initial-scale=1, '
                                             'maximum-scale=1.0, user-scalable=no">\n')
        ok = self.commit("src/ok.html", '<meta name="viewport" content="width=device-width, maximum-scale=5">\n')
        nxt = self.commit("src/app/layout.tsx", 'export const viewport = { width: "device-width", maximumScale: 1, '
                                                'userScalable: false };\n')
        self.assertEqual(self.lint(["src/index.html", "src/ok.html", "src/app/layout.tsx"], html, ok, nxt), [
            'src/index.html:1: 확대 막기(maximum-scale=1.0, user-scalable=no">)',
            "src/app/layout.tsx:1: 확대 막기(maximumScale: 1, userScalable: false };)",
        ])

    def test_focus_removed_without_focus_visible(self):
        bad = self.commit("src/btn.css", ".btn:focus { outline: none; }\n")
        good = self.commit("src/ok.css", ".btn { outline: none; }\n.btn:focus-visible { outline: 2px solid var(--ring); }\n")
        tw_good = self.commit("src/Ok.tsx", '<button className="outline-none focus-visible:ring-2">x</button>\n'
                                            '<a className="focus:outline-none focus:ring-2">y</a>\n')
        tw_bad = self.commit("src/Bad.tsx", '<button className="focus:outline-none">x</button>\n')
        offset = self.commit("src/offset.css", ".a { outline-offset: 0; }\n")
        files = ["src/btn.css", "src/ok.css", "src/Ok.tsx", "src/Bad.tsx", "src/offset.css"]
        self.assertEqual(self.lint(files, bad, good, tw_good, tw_bad, offset), [
            "src/btn.css:1: 포커스 표시 제거(outline: none; }, 파일에 focus-visible 없음)",
            'src/Bad.tsx:1: 포커스 표시 제거(outline-none">x</button>, 파일에 focus-visible 없음)',
        ])

    def test_only_lines_added_by_given_commits(self):
        self.commit("src/btn.css", ".btn { outline: none; }\n", "chore: base")
        sha = self.commit("src/btn.css", ".btn { outline: none; }\n.x { color: red; }\n")
        self.assertEqual(self.lint(["src/btn.css"], sha), [])

    def test_skips_non_targets_excluded_paths_and_git_errors(self):
        txt = self.commit("notes.txt", "outline: none\n")
        task = self.commit("docs/tasks/T-001/report.md", "outline: none\n")
        self.assertEqual(self.lint(["notes.txt", "docs/tasks/T-001/report.md"], txt, task), [])
        self.assertEqual(self.lint(["src/btn.css"], "0" * 40), [])

    def test_deleted_file_checks_removed_lines_only(self):
        self.commit("src/Icon.tsx", '<button aria-label="닫기">x</button>\n', "chore: base")
        self.git("rm", "-q", "--", "src/Icon.tsx")
        self.git("commit", "-q", "-m", "[T-001] chore: remove")
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.lint(["src/Icon.tsx"], sha), [f"src/Icon.tsx: {LOST.format(name='aria-label', n=1)}"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_lint test_designdiff -v 2>&1 | tail -5`
Expected: `ImportError`(`cap`, `design_findings`) 와 `ModuleNotFoundError: No module named 'orch.designdiff'` 로 실패.

- [ ] **Step 4: `lint.py` 를 고친다**

`orca-team/lib/orch/lint.py` 의 `design_lint` 함수 전체를 다음 세 함수로 바꾼다(반복문 본문은 지금과 같다).

```python
def cap(found: list[str]) -> list[str]:
    """MAX_FINDINGS 를 넘으면 앞 MAX_FINDINGS 개와 "… 외 N건" 한 줄로 줄인다."""
    if len(found) <= MAX_FINDINGS:
        return found
    return [*found[:MAX_FINDINGS], f"… 외 {len(found) - MAX_FINDINGS}건"]


def design_findings(root: Path, files: list[str]) -> list[str]:
    """files(저장소 기준 상대 경로) 중 검사 대상의 의심 위치 목록(상한 없음). 대상 확장자가 아니거나, 제외 경로거나,
    없거나, 1MB 를 넘거나, UTF-8 이 아니면 건너뛴다."""
    found: list[str] = []
    for rel in files:
        if Path(rel).suffix.lower() not in LINT_SUFFIXES or matches_any(rel, LINT_EXCLUDE):
            continue
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += scan(rel, text)
    return found


def design_lint(root: Path, files: list[str]) -> list[str]:
    """design_findings 를 MAX_FINDINGS 로 자른 것. 넘으면 마지막 항목이 "… 외 N건" 이다."""
    return cap(design_findings(root, files))
```

- [ ] **Step 5: `designdiff.py` 를 만든다**

`orca-team/lib/orch/designdiff.py`:

```python
"""디자인 변경 검사(orch review). 태스크 커밋 diff 에서 지운 줄로 줄어든 접근성·글자 신호와, 이 태스크가 추가한 줄의
확대 막기·포커스 표시 제거를 찾는다(.orchestration/design-rules.md §2·§4).

결과는 경고다. 같은 변경 안에 대체가 있을 수 있으므로 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import re
from pathlib import Path

from .globs import matches_any
from .lint import LINT_EXCLUDE, LINT_SUFFIXES, MAX_BYTES, snippet
from .textlint import diff_lines

APP_SUFFIXES = {".dart", ".swift", ".kt", ".xml"}
# 지운 줄의 일치 수가 추가한 줄의 일치 수보다 많으면 알리는 신호. (이름, 정규식)이고 이 순서로 알린다.
# 같은 속성을 옮기거나 줄만 바꾼 경우는 수가 같아 걸러진다.
SIGNALS = [
    ("aria-label", re.compile(r"aria-label(?!ledby)")),
    ("aria-labelledby", re.compile(r"aria-labelledby")),
    ("aria-describedby", re.compile(r"aria-describedby")),
    ("aria-live", re.compile(r"aria-live")),
    ("role", re.compile(r"(?<![\w-])role\s*=")),
    ("alt", re.compile(r"(?<![\w-])alt\s*=")),
    ("<label", re.compile(r"<label\b")),
    ("htmlFor", re.compile(r"\bhtmlFor\b")),
    (":focus", re.compile(r":focus\b")),
    ("focus-visible:", re.compile(r"(?<![\w-])focus-visible:")),
    ("focus:", re.compile(r"(?<![\w-])focus:")),
    (".focus(", re.compile(r"\.focus\(")),
    ("tabindex", re.compile(r"\btab[iI]ndex\b")),
    ("prefers-reduced-motion", re.compile(r"prefers-reduced-motion")),
    ("motion-reduce:", re.compile(r"(?<![\w-])motion-reduce:")),
    ("motion-safe:", re.compile(r"(?<![\w-])motion-safe:")),
    ("useReducedMotion", re.compile(r"\buseReducedMotion\b")),
    ("prefers-contrast", re.compile(r"prefers-contrast")),
    ("lang", re.compile(r"(?<![\w-])lang\s*=")),
    ("keep-all", re.compile(r"keep-all")),
    ("break-keep", re.compile(r"(?<![\w-])break-keep(?![\w-])")),
    ("tabular-nums", re.compile(r"tabular-nums")),
    ("semanticsLabel", re.compile(r"\bsemanticsLabel\b")),
    ("semanticLabel", re.compile(r"\bsemanticLabel\b")),
    ("Semantics(", re.compile(r"\bSemantics\(")),
    ("accessibilityLabel", re.compile(r"\baccessibilityLabel\b")),
    ("accessibilityHint", re.compile(r"\baccessibilityHint\b")),
    ("contentDescription", re.compile(r"\bcontentDescription\b")),
    ("accessibilityReduceMotion", re.compile(r"\baccessibilityReduceMotion\b")),
    ("isReduceMotionEnabled", re.compile(r"\bisReduceMotionEnabled\b")),
    ("disableAnimations", re.compile(r"\bdisableAnimations\b")),
]
# 뷰포트 메타(user-scalable=no, maximum-scale=1)와 Next.js viewport 객체(userScalable: false, maximumScale: 1).
ZOOM_RE = re.compile(r"user-scalable\s*=\s*[\"']?(?:no|0)(?![\w.])|maximum-scale\s*=\s*[\"']?1(?:\.0*)?(?![\d.])"
                     r"|\buserScalable\s*:\s*false\b|\bmaximumScale\s*:\s*1(?:\.0*)?(?![\d.])", re.I)
FOCUS_OFF_RE = re.compile(r"\boutline\s*:\s*[\"']?(?:none|0(?:px)?)(?![\w.%-])|(?<![\w-])outline-none(?![\w-])")
# 대체 포커스 표시: focus-visible(CSS·Tailwind), 또는 focus:outline-none 이 아닌 Tailwind focus: 클래스(focus:ring-2).
# :focus 셀렉터는 대체로 보지 않는다(:focus { outline: none } 이 바로 찾을 대상이다).
FOCUS_ALT_RE = re.compile(r"focus-visible|(?<![\w:-])focus:(?!outline-none(?![\w-]))[\w\[]")


def is_target(rel: str) -> bool:
    suffix = Path(rel).suffix.lower()
    return (suffix in LINT_SUFFIXES or suffix in APP_SUFFIXES) and not matches_any(rel, LINT_EXCLUDE)


def removed_signals(rel: str, added: list[str], removed: list[str]) -> list[str]:
    found: list[str] = []
    for name, pattern in SIGNALS:
        lost = sum(len(pattern.findall(line)) for line in removed) - sum(len(pattern.findall(line)) for line in added)
        if lost > 0:
            found.append(f"{rel}: 지운 줄에서 {name} {lost}개가 줄었다(대체가 있는지 확인)")
    return found


def read_current(root: Path, rel: str) -> str | None:
    path = root / rel
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def added_blockers(rel: str, text: str, added: set[str]) -> list[str]:
    """지금 파일에서 이 태스크가 추가한 줄의 확대 막기와 포커스 표시 제거. 줄 번호는 지금 파일 기준이다."""
    focus_kept = FOCUS_ALT_RE.search(text) is not None
    found: list[str] = []
    for number, line in enumerate(text.split("\n"), start=1):
        if line not in added:
            continue
        zoom = ZOOM_RE.search(line)
        if zoom:
            found.append(f"{rel}:{number}: 확대 막기({snippet(line[zoom.start():])})")
        focus = None if focus_kept else FOCUS_OFF_RE.search(line)
        if focus:
            found.append(f"{rel}:{number}: 포커스 표시 제거({snippet(line[focus.start():])}, 파일에 focus-visible 없음)")
    return found


def design_diff_lint(root: Path, files: list[str], shas: list[str]) -> list[str]:
    """files(저장소 기준 상대 경로) 중 대상 파일에서 shas 커밋들의 지운 줄로 줄어든 신호와, 추가한 줄의 확대 막기·포커스
    표시 제거. git 오류가 난 파일은 건너뛰고, 지금 없거나 1MB 를 넘거나 UTF-8 이 아닌 파일은 지운 줄 검사만 한다.
    상한은 부르는 쪽이 건다."""
    found: list[str] = []
    for rel in files:
        if not is_target(rel):
            continue
        lines = diff_lines(root, shas, rel)
        if lines is None:
            continue
        added, removed = lines
        found += removed_signals(rel, added, removed)
        text = read_current(root, rel)
        if text is not None:
            found += added_blockers(rel, text, set(added))
    return found
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_lint test_designdiff -v 2>&1 | tail -3`
Expected: `OK`. 실패하면 정규식보다 테스트 기대가 설계 §7.2 와 맞는지 먼저 본다. 설계와 다르게 고쳐야 하면 리포트에 이유를 적는다.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` (저장소 루트)
Expected: `OK`, 경고 없음.

- [ ] **Step 8: 커밋한다**

공개 저장소 grep 을 돌린 뒤:

```bash
git add orca-team/lib/orch/lint.py orca-team/lib/orch/designdiff.py orca-team/tests/test_lint.py orca-team/tests/test_designdiff.py
git commit -m "feat(orca-team): 태스크 diff 에서 줄어든 접근성 신호와 확대 막기·포커스 제거를 찾는 디자인 검사를 더한다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 리뷰 연결과 리뷰 방식 (`review.py`, `review-design.md`)

**Files:**
- Modify: `orca-team/lib/orch/review.py` (import 14행, `design_section` 116–124행, `text_section` 127–138행, `compose_prompt` 141–151행)
- Modify: `orca-team/templates/review-design.md` (전체)
- Test: `orca-team/tests/test_review.py` (`ReviewDesignSectionTest`), `orca-team/tests/test_templates.py` (`test_placeholders`)

**Interfaces:**
- Consumes: `lint.cap`, `lint.design_findings` (Task 2), `designdiff.design_diff_lint` (Task 2).
- Produces: `review.design_section(p: Project, cfg: Config, role: str, files: list[str], shas: list[str]) -> str`. `review.guarded(check, *args) -> list[str]`, `review.bullets(found: list[str]) -> str`.

- [ ] **Step 1: 실패하는 리뷰 테스트를 쓴다**

`orca-team/tests/test_review.py` 의 import 에 다음 줄을 더한다(`from orch import review` 아래).

```python
from orch.config import Config
```

`ReviewDesignSectionTest` 클래스의 `commit_text` 바로 뒤에 다음 헬퍼와 테스트를 넣는다. `mock` 은 이 파일에서 이미 import 한다(`from unittest import mock`).

```python
    def section(self, files, shas):
        p = Project(self.root)
        return review.design_section(p, Config.load(p.config_path), "developer", files, shas)

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def test_diff_lint_follows_content_lint(self):
        self.commit_text("src/close.tsx", '<button aria-label="닫기">x</button>\n')
        self.commit_text("src/close.tsx", "<span>🚀</span>\n<button>x</button>\n")
        section = self.section(["src/close.tsx"], [self.head()])
        emoji = section.index("- src/close.tsx:1: 이모지(🚀)")
        lost = section.index("- src/close.tsx: 지운 줄에서 aria-label 1개가 줄었다(대체가 있는지 확인)")
        self.assertLess(emoji, lost)

    def test_review_prompt_has_zoom_block(self):
        self.commit_text("src/index.html", '<meta name="viewport" content="width=device-width, user-scalable=no">\n')
        self.review()
        self.assertIn('- src/index.html:1: 확대 막기(user-scalable=no">)\n', self.prompt())

    def test_combined_findings_are_capped(self):
        with mock.patch("orch.review.design_findings", return_value=[f"a.css:{n}: x" for n in range(40)]), \
                mock.patch("orch.review.design_diff_lint", return_value=[f"b.css: y{n}" for n in range(20)]):
            section = self.section(["a.css"], [])
        self.assertIn("- b.css: y9\n", section)
        self.assertNotIn("b.css: y10", section)
        self.assertIn("- … 외 10건\n", section)

    def test_failing_check_keeps_the_other(self):
        self.commit_text("src/card.css", ".card {\n  border-left: 4px solid #2563eb;\n}\n")
        with mock.patch("orch.review.design_diff_lint", side_effect=RuntimeError("boom")):
            section = self.section(["src/card.css"], [self.head()])
        self.assertIn("- src/card.css:2: 왼쪽 줄 강조(border-left: 4px solid #2563eb;)\n", section)
        self.assertIn("- 자동 검사 실패: RuntimeError: boom\n", section)
```

- [ ] **Step 2: `review-design.md` 템플릿 테스트를 고친다**

`orca-team/tests/test_templates.py` 의 `test_placeholders` 에서 다음 네 줄을

```python
        design = (T / "review-design.md").read_text(encoding="utf-8")
        for key in ("{design_lint}", ".orchestration/design-rules.md", "docs/design/system/tokens.json",
                    "### 금지 목록 점검", "### 시스템 추가 제안", "REQUEST_CHANGES"):
            self.assertIn(key, design)
```

다음으로 바꾼다.

```python
        design = (T / "review-design.md").read_text(encoding="utf-8")
        for key in ("{design_lint}", ".orchestration/design-rules.md", "docs/design/system/tokens.json",
                    "### 디자인 점검", "### 금지 목록 점검", "### 시스템 추가 제안", "REQUEST_CHANGES", "`(새로 생김)`",
                    "`(회귀)`", "`(기존)`", "즉시 차단 항목", "prefers-reduced-motion", "320px", "지우기",
                    "확인 못 함", "15개", "box-shadow"):
            self.assertIn(key, design)
        self.assertEqual(design.count("{design_lint}"), 1)
        self.assertEqual(re.findall(r"^(\d+)\. ", design, re.M), [str(n) for n in range(5, 14)])
        self.assertNotRegex(design, r"\d~\d")
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_review.ReviewDesignSectionTest test_templates.TemplateTest.test_placeholders -v 2>&1 | tail -8`
Expected: `design_section() takes 4 positional arguments but 5 were given`(또는 mock 대상 이름이 없다는 `AttributeError`)와 템플릿 키 누락으로 실패.

- [ ] **Step 4: `review.py` 를 고친다**

import 줄 `from .lint import DESIGN_ROLES, design_lint` 를 지운다. `from .config import Config` 바로 다음 줄에 `from .designdiff import design_diff_lint` 를 넣고, `from .globs import matches_any` 다음 줄에 `from .lint import DESIGN_ROLES, cap, design_findings` 를 넣는다(모듈 이름 순서).

`design_section` 과 `text_section` 두 함수 전체를 다음으로 바꾼다.

```python
def guarded(check, *args) -> list[str]:
    """검사 하나의 결과. 검사 버그가 리뷰를 막으면 안 되므로 예외는 "자동 검사 실패" 한 줄(200자까지)이 된다.
    판정은 Codex 가 한다."""
    try:
        return check(*args)
    except Exception as exc:
        return [" ".join(f"자동 검사 실패: {type(exc).__name__}: {exc}".split())[:200]]


def bullets(found: list[str]) -> str:
    return "\n".join(f"- {item}" for item in found) if found else "- 없음"


def design_section(p: Project, cfg: Config, role: str, files: list[str], shas: list[str]) -> str:
    """디자인·UI 직무 리뷰에 넣는 기준과 orch 자동 검사 결과(templates/review-design.md). 자동 검사는 바뀐 파일 내용
    검사(lint.design_findings) 다음에 태스크 커밋 diff 검사(designdiff.design_diff_lint)를 이어 붙여 50건으로 자른다.
    다음 절과 사이의 빈 줄까지 붙여 돌려준다(review-prompt.md 의 자리표시가 다음 절 제목 바로 앞에 있다). 다른 직무와,
    director 직무가 없는 이전 버전(0.3.0) 프로젝트는 빈 문자열."""
    if role not in DESIGN_ROLES or plan.DIRECTOR_ROLE not in cfg.roles:
        return ""
    found = cap([*guarded(design_findings, p.root, files), *guarded(design_diff_lint, p.root, files, shas)])
    return render((TEMPLATES / "review-design.md").read_text(encoding="utf-8"), {"design_lint": bullets(found)}) + "\n"


def text_section(p: Project, files: list[str], shas: list[str]) -> str:
    """서비스 문구·문서를 바꾼 태스크 리뷰에 넣는 문구 기준과 orch 문구 자동 검사 결과(templates/review-text.md).
    직무와 무관하다. 다음 절과 사이의 빈 줄까지 붙여 돌려준다. text-rules.md 가 없는 이전 버전(0.4.x) 프로젝트와
    한글이 든 텍스트 대상 파일을 바꾸지 않은 태스크는 빈 문자열."""
    if not (p.root / RULES_FILE).is_file() or not text_targets(p.root, files):
        return ""
    found = guarded(text_lint, p.root, files, shas)
    return render((TEMPLATES / "review-text.md").read_text(encoding="utf-8"), {"text_lint": bullets(found)}) + "\n"
```

`compose_prompt` 의 렌더 값 줄

```python
        "design_section": design_section(p, cfg, doc.role, files),
```

을 다음으로 바꾼다.

```python
        "design_section": design_section(p, cfg, doc.role, files, [sha for sha, _ in commits]),
```

- [ ] **Step 5: `review-design.md` 를 쓴다**

`orca-team/templates/review-design.md` 전체를 다음으로 바꾼다.

```markdown
## 디자인·UI 기준
5. UI 를 바꾼 태스크면 `.orchestration/design-rules.md` 를 읽고 바뀐 화면·코드가 지키는지 본다. §1 금지 목록(이전 버전 프로젝트는 번호 목록 전체) 위반은 REQUEST_CHANGES 사유다. 워커 리포트 마지막 라운드의 `### 디자인 점검`(이전 버전 프로젝트는 `### 금지 목록 점검`)이 실제와 맞는지도 본다.
6. 디자인 시스템(`docs/design/system/tokens.json`, `docs/design/system/README.md`)이 있으면 그것을 기준으로 본다. designer 는 시스템에 없는 색·크기·간격·컴포넌트를 새로 만들지 않았는가(필요하면 리포트 마지막 라운드의 `### 시스템 추가 제안` 에 적어야 한다). developer 는 화면 코드가 토큰과 공통 컴포넌트만 쓰고 색·크기·간격·모서리·그림자를 하드코딩하지 않았는가.
7. 이 태스크가 만든 문제만 지적한다. 디자인 지적은 문제 앞에 `(새로 생김)`·`(회귀)`·`(기존)` 중 하나를 붙인다(예: `- [P1] src/Dialog.tsx:42 — (회귀) 닫기 아이콘 버튼의 aria-label 을 지웠다. 되살린다.`). 위치가 아니라 원인으로 가른다. 바뀐 토큰·공통 컴포넌트 때문에 손대지 않은 화면에 생긴 문제도 이 태스크의 것이다. 토큰이나 공통 컴포넌트를 바꿨으면 그것을 쓰는 화면을 5곳까지 보고, 보지 않은 곳의 수를 `## 직접 확인한 것` 에 적는다. `(기존)` 은 P3 로 3개까지만 적는다.
8. diff 의 지운 줄(`-`)을 읽는다. 접근 가능한 이름·설명(`aria-*`, `alt`, `<label>`), 포커스 표시, `prefers-reduced-motion`, `lang`, 한글 줄바꿈(`keep-all`), 고정폭 숫자, `div` 로 바뀐 `<button>`·`<a>`, 리터럴 값이나 더 옅은 토큰으로 바뀐 색 토큰, 지운 사용자 문구를 찾는다. 같은 변경 안에 대체가 있으면 지적하지 않는다: 보이는 글자를 가리키는 `aria-labelledby`, 같은 `:focus-visible` 의 box-shadow 링, 네이티브 요소로 바뀌어 필요 없어진 `role`·`tabindex`, 값이 같은 토큰, 번역 파일로 옮긴 문구. 대체가 없으면 `(회귀)` 다.
9. 빠진 상태를 찾는다. 새 변형·크기·테마가 hover·focus·눌림·비활성·로딩·선택 중 일부에만 적용됐는가. 새 컴포넌트에 빈 상태·로딩·오류·비활성·좁은 폭이 없는가. 새 컨트롤이 같은 종류의 이웃 화면에는 빠졌는가.
10. 디자인 지적의 심각도:
   - P1: §1 위반, 그리고 아래 즉시 차단 항목이 `(새로 생김)`·`(회귀)` 인 것. 화면이 사소해도 낮추지 않는다.
     - 접근 가능한 이름이 없는 컨트롤
     - 키보드로 닿는데 포커스 표시가 없는 컨트롤, 포인터로만 되는 조작
     - `prefers-reduced-motion` 을 무시하는 움직임·자동 재생
     - 320px 폭이나 200% 확대에서 잘리거나 겹치거나 닿을 수 없는 내용·컨트롤
     - 기준에 못 미치는 본문·컨트롤 글자 대비
     - 색만으로 전하는 상태·뜻, 뜻과 반대로 쓴 의미 색(위험 색을 일반 동작에)
     - 확인·되돌리기·구별되는 표시가 없는 삭제류 동작
     - 전체를 볼 방법이 없는 잘린 내용, 단서 없이 스크롤 끝이나 접힘 뒤에 숨은 내용·컨트롤
     - 복구 방법을 알려 주지 않는 오류
     - 움직임으로만 알리는 상태 변화
   - P2: 그 밖의 §2–§7 위반(`(새로 생김)`·`(회귀)`).
   - P3: 사용 영향이 작은 다듬기, `(기존)`.
   같은 등급 안에서는 영향 범위가 넓은 것(토큰·공통 컴포넌트)을 먼저 적는다. 원인이 같은 문제는 지적 하나에 위치를 모은다. 디자인 지적은 15개까지 적고, 채우려고 늘리지 않는다.
11. 고칠 방향은 되는 것 중 가장 앞의 것을 적는다: 지우기(여백으로 될 구분선, 잦은 조작의 애니메이션, 네이티브 요소로 필요 없어진 ARIA) → 플랫폼 기본(네이티브 요소·컨트롤, 브라우저 포커스 링) → 기존 토큰·간격·컴포넌트 재사용 → 값 교정(design-rules 가 정한 값) → 새로 추가. 새 토큰·컴포넌트가 필요하면 designer 는 `### 시스템 추가 제안`, developer 는 ask 로 넘기라고 적는다. 지우기로 고칠 것을 이 변경이 더했으면 그것도 지적이다.
12. 지적마다 파일:줄과 지금 코드를 붙인다. 렌더해야 알 수 있는 문제(대비, 잘림, 움직임)는 실행해 확인하고 명령과 결과를 `## 직접 확인한 것` 에 적는다. 확인하지 못했으면 지적하지 않고 "확인 못 함"으로 적는다. 코드만으로 분명한 문제(`aria-label` 이 없는 아이콘 버튼 등)는 렌더 없이 지적한다.
13. 밀도·모서리·말투처럼 시스템이 일관되게 정한 취향은 지적하지 않는다. 즉시 차단 항목은 시스템이 정했어도 지적한다.

orch 자동 검사(의심 위치. 정당한 쓰임도 있으니 직접 열어 보고 판단한다. "지운 줄에서 … 줄었다" 는 8번, "확대 막기"·"포커스 표시 제거" 는 10번의 즉시 차단 항목 후보다):
{design_lint}
```

- [ ] **Step 6: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_review test_templates -v 2>&1 | tail -3`
Expected: `OK`. 기존 `test_developer_prompt_has_design_rules_and_lint_warnings`(절 끝 `\n\n## REVIEW_FILE 형식`)와 `ReviewTextSectionTest` 의 "자동 검사 실패" 테스트도 그대로 통과해야 한다.

- [ ] **Step 7: 전체 테스트를 돌린다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` (저장소 루트)
Expected: `OK`, 경고 없음.

- [ ] **Step 8: 커밋한다**

공개 저장소 grep 을 돌린 뒤:

```bash
git add orca-team/lib/orch/review.py orca-team/templates/review-design.md orca-team/tests/test_review.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): Codex 디자인 리뷰를 변경분 분류·지운 줄 읽기·즉시 차단 항목 기준으로 바꾸고 diff 검사 결과를 싣는다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 규칙 파일과 직무 문서 (`design-rules.md`, roles)

**Files:**
- Modify: `orca-team/templates/design-rules.md` (전체)
- Modify: `orca-team/templates/roles/director.md`, `orca-team/templates/roles/designer.md`, `orca-team/templates/roles/developer.md`
- Modify: `orca-team/templates/text-rules.md` (§4 의 8번)
- Modify: `orca-team/lib/orch/lint.py` (모듈 설명 첫 줄)
- Test: `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: 없음(문서). Task 3 의 `review-design.md` 가 이 파일의 §1–§7 과 `### 디자인 점검` 을 가리킨다.
- Produces: `.orchestration/design-rules.md` 의 제목 `## §0 범위` … `## §7 상태`, `## 자동 검사`, `## 출처`. 리포트 소절 이름 `### 디자인 점검`.

- [ ] **Step 1: 템플릿 테스트를 고친다**

`orca-team/tests/test_templates.py` 에서:

1. `test_director_role_doc` 의 키 튜플에서 `"### 금지 목록 점검"` 을 `"### 디자인 점검"` 으로 바꾸고, 같은 튜플에 `"§3 의 7번"`, `"word-break: keep-all"`, `"눌림"` 을 더한다.
2. `test_design_rules` 전체를 다음으로 바꾼다.

```python
    def test_design_rules(self):
        text = (T / "design-rules.md").read_text(encoding="utf-8")
        headings = ("# 디자인 규칙", "## §0 범위", "## §1 금지 목록 (AI 티)", "## §2 접근성", "## §3 색", "## §4 글자",
                    "## §5 레이아웃", "## §6 표면·동작", "## §7 상태", "## 자동 검사", "## 출처")
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        banned = text[text.index("## §1"):text.index("## §2")]
        self.assertEqual(len(re.findall(r"^\d+\. ", banned, re.M)), 10)
        for key in ("이모지", "Lucide", "Phosphor", "Tabler", "border-left", "border-l-4", "파랑→보라", "글래스모피즘",
                    "블롭", "3열 카드", "가운데 정렬", "Inter", "한글 글꼴", "4.5:1", "3:1", "Unlock the power of",
                    "AI-powered", "lorem ipsum", "### 디자인 점검", "orch review", ":focus-visible",
                    "prefers-reduced-motion", "user-scalable=no", "word-break: keep-all", "tabular-nums", "0.96",
                    "@media (hover: hover)", "overscroll-behavior: contain", "Storybook", "커밋하지 않는다",
                    "44pt", "48dp", "https://github.com/jakubkrehel/skills", "MIT"):
            self.assertIn(key, text)
        self.assertNotIn("### 금지 목록 점검", text)
        self.assertNotRegex(text, r"\d~\d")
```

3. `test_designer_uses_project_design_system` 의 키 튜플에서 `` "`### 금지 목록 점검`" `` 을 `` "`### 디자인 점검`" `` 으로 바꾸고 `"§7 의 상태"` 를 더한다.
4. `test_developer_uses_tokens_and_components_only` 의 키 튜플에서 `` "`### 금지 목록 점검`" `` 을 `` "`### 디자인 점검`" `` 으로 바꾸고 `"Storybook"`, `"커밋하지 않는다"` 를 더한다.
5. `test_text_rules` 의 키 튜플에 다음 문자열을 더한다.

```python
"design-rules.md` §1 의 1번",
```

6. 클래스 끝(`test_docs_describe_auto_up` 뒤)에 다음 테스트를 더한다.

```python
    def test_role_docs_drop_old_report_name(self):
        for name in ("director", "designer", "developer"):
            with self.subTest(role=name):
                text = (T / "roles" / f"{name}.md").read_text(encoding="utf-8")
                self.assertIn("### 디자인 점검", text)
                self.assertNotIn("### 금지 목록 점검", text)
                self.assertIn("§2–§7", text)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates -v 2>&1 | tail -5`
Expected: `test_design_rules`, `test_director_role_doc`, `test_designer_uses_project_design_system`, `test_developer_uses_tokens_and_components_only`, `test_text_rules`, `test_role_docs_drop_old_report_name` 실패.

- [ ] **Step 3: `design-rules.md` 를 쓴다**

`orca-team/templates/design-rules.md` 전체를 다음으로 바꾼다. §1 의 1–10번은 지금 문장 그대로다.

```markdown
# 디자인 규칙

디자인 디렉터(director), 화면 디자이너(designer), 개발자(developer)의 UI 작업이 지키고, Codex 리뷰도 이 규칙으로 판정한다. UI 를 바꾼 태스크는 리포트 현재 라운드 `## r{round}` 절 안의 `### 디자인 점검` 소절에 다음을 적는다.
- §1 항목마다 지켰는지
- §2–§6 에서 어긴 곳과 예외·근거(지킨 항목은 늘어놓지 않는다)
- §7 에서 확인한 상태와 확인 방법

`orch review` 는 기계적으로 찾을 수 있는 의심 위치를 리뷰 프롬프트에 넣는다(아래 "자동 검사"). 판정은 Codex 가 한다.

## §0 범위
- 값은 웹(CSS) 기준이다. 앱(iOS·Android·Flutter)은 같은 원칙을 플랫폼 기능으로 지킨다: 접근성 라벨(`accessibilityLabel`·`contentDescription`·`Semantics`), 글자 크기 설정(Dynamic Type·글꼴 배율), 동작 줄이기 설정, 터치 영역 44pt(iOS)·48dp(Android).
- 프로젝트 디자인 시스템(`docs/design/system/`)이 정한 값이 먼저다. 시스템에 없는 값이 필요하면 designer 는 `### 시스템 추가 제안` 에 적고, developer 는 ask 로 묻는다. director 는 시스템을 만들 때 이 규칙을 반영한다.
- 밀도, 모서리 크기, 말투처럼 시스템이 일관되게 정한 취향은 위반이 아니다. 한 프로젝트 안에서 같은 것을 두 방식으로 하면 위반이다.

## §1 금지 목록 (AI 티)
위반은 REQUEST_CHANGES 사유다. 정당한 예외는 `### 디자인 점검` 에 근거와 함께 적는다.

1. 이모지를 아이콘, 제목 장식, 글머리, 상태 표시로 쓰지 않는다. 아이콘은 디자인 시스템이 정한 아이콘 세트 하나(Lucide, Phosphor, Tabler 중 하나)만 쓴다.
2. 왼쪽에 굵은 색 줄이 있는 카드·콜아웃(`border-left`·`border-inline-start` 강조, Tailwind `border-l-4` 같은 것)을 쓰지 않는다.
3. 파랑→보라(인디고·바이올렛) 그라디언트와 그라디언트 글자를 쓰지 않는다. 이유 없는 네온 빛, 글래스모피즘, 블롭 배경도 쓰지 않는다.
4. 모든 카드에 같은 큰 둥근 모서리와 부드러운 그림자를 반복하지 않는다. 모서리와 그림자는 시스템 토큰의 위계를 따른다.
5. 정해진 틀 같은 랜딩(큰 그라디언트 제목, 버튼 2개, "아이콘·제목·두 줄 설명" 3열 카드)을 쓰지 않는다.
6. 모든 섹션을 가운데 정렬하고 같은 간격만 반복하지 않는다. 정보 위계에 맞는 정렬과 리듬을 쓴다.
7. 근거 없이 Inter나 시스템 글꼴 기본값을 쓰지 않는다. 한국어 서비스는 한글 글꼴을 정한다.
8. 회색 글자를 남발하지 않는다. 본문 대비는 4.5:1 이상, 24px 이상 글자와 아이콘·테두리는 3:1 이상이다.
9. 뻔한 문구("Unlock the power of", "Seamless", "Elevate", "혁신적인")와 의미 없는 배지("New", "AI-powered")를 쓰지 않는다.
10. 시안과 화면은 그 서비스에 맞는 현실적인 데이터와 문구로 채운다. lorem ipsum이나 "사용자 1" 같은 더미를 쓰지 않는다.

## §2 접근성 (WCAG 2.2 AA)
1. 네이티브 요소를 먼저 쓴다. 동작은 `<button>`, 이동은 `<a href>` 이고 `<div onClick>` 을 쓰지 않는다. 네이티브 요소가 있으면 ARIA 로 흉내 내지 않는다.
2. 포커스 표시는 `:focus-visible` 에 둔다. 대체 표시 없이 `outline: none` 을 쓰지 않는다. 직접 만든 포커스 링은 맞닿는 색과 3:1 이상이다.
3. 포인터로 하는 모든 조작을 키보드로도 할 수 있다. Esc 는 겹친 창을 닫고, Tab 은 위젯 사이를, 화살표는 위젯 안을 움직이고, Enter·Space 가 버튼을 누른다. `tabindex` 는 `0` 과 `-1` 만 쓴다.
4. 모달은 `<dialog>` 와 `showModal()` 을 먼저 쓴다. 열면 포커스를 안으로 옮기고 닫으면 연 컨트롤로 돌려준다. 클라이언트 라우팅으로 화면이 바뀌면 문서 제목을 바꾸고 새 화면의 `<h1>` 이나 `<main>` 으로 포커스를 옮긴다.
5. 누를 수 있는 영역은 24×24px 이상이다. 터치 화면은 44×44px, 데스크톱은 40×40px 를 목표로 한다. 보이는 크기를 키울 수 없으면 가상 요소로 영역을 넓히되 이웃 영역과 겹치지 않는다. 끌기 조작에는 버튼이나 메뉴 같은 대안을 둔다.
6. 모든 입력란에 보이는 라벨(`<label for>`)을 둔다. 자리표시(placeholder)는 라벨이 아니다. 알맞은 `type`·`inputmode`·`autocomplete` 를 쓰고 붙여넣기를 막지 않는다.
7. 오류는 제출할 때 검사한다. 유효할 때까지 제출 버튼을 비활성화하지 않는다. 틀린 칸에 `aria-invalid="true"` 와 오류 문구를 가리키는 `aria-describedby` 를 두고, 첫 틀린 칸으로 포커스를 옮긴다.
8. 아이콘만 있는 버튼에 `aria-label` 을 둔다. 보이는 라벨 글자는 접근 가능한 이름에 들어간다. 장식 요소는 `aria-hidden="true"` 로 숨기되 포커스를 받는 요소에는 쓰지 않는다. 이미지 대체 텍스트는 목적에 따라 쓴다: 장식은 `alt=""`, 정보 이미지는 뜻, 기능 이미지는 동작.
9. 상태를 색만으로 알리지 않는다. 아이콘·글자·밑줄을 함께 쓴다.
10. 움직임은 `@media (prefers-reduced-motion: no-preference)` 안에서만 쓴다. 동작 줄이기 설정에서는 이동·확대를 투명도 전환으로 바꾸고 시차 효과(parallax)와 자동 재생을 끈다.
11. 5초 넘게 스스로 움직이거나 바뀌는 것에는 멈춤 컨트롤을 둔다. 동작 버튼이나 오류가 든 토스트는 사용자가 닫을 때까지 남긴다.
12. 화면 갱신을 알린다. 입력란 검증은 `aria-describedby`, 급하지 않은 알림(토스트·결과 수)은 `role="status"`, 급한 오류만 `role="alert"` 로 알린다.
13. 제목은 문서 개요를 이루고, 화면마다 `<main>` 을 하나 둔다. 반복 내비게이션이 본문 앞에 있으면 "본문 바로가기" 링크를 첫 포커스 요소로 둔다. 문서에 `lang="ko"` 를 둔다.
14. 글자 200% 확대와 320px 폭에서 가로 스크롤 없이 내용과 컨트롤이 다 보인다. 뷰포트 메타에 `maximum-scale=1`·`user-scalable=no` 를 쓰지 않는다.

## §3 색
1. 램프는 중립 하나, 강조 하나, 실제로 화면에 쓰는 상태(성공·경고·위험 등)만 둔다. 둘째 강조색은 두 대상을 한눈에 구별해야 할 때만 둔다.
2. 램프의 단계마다 역할이 있다(페이지 배경, 컴포넌트 hover, 테두리, 채움, 본문 글자). 쓰는 역할이 없는 단계를 만들지 않는다. 램프는 한 색상(hue)을 유지하고 눈대중이 아니라 색 도구로 만든다.
3. 기본 토큰은 색 이름(`blue-600`), 의미 토큰은 역할 이름(`color-text-secondary`)으로 짓는다. 컴포넌트는 의미 토큰만 쓴다.
4. 토큰은 그 역할에만 쓴다. 지금 값이 맞다고 테두리 토큰을 글자색으로 빌리지 않는다. 역할에 맞는 토큰이 없으면 시스템 추가 제안을 한다.
5. 한 색은 인터페이스 전체에서 한 뜻만 가진다. 위험 색을 일반 동작에 쓰지 않는다.
6. 색을 채운 주 버튼은 화면에 하나다. 동급의 다른 동작은 중립으로 둔다. 강조색은 글자가 아니라 배경에 둔다.
7. 대비는 그 글자가 실제로 렌더되는 배경과 잰다(페이지 배경이 아니다). 본문 4.5:1, 24px 이상(굵게 18.67px 이상) 글자 3:1, 컴포넌트 경계·상태·의미 있는 그래픽 3:1 이다. 비활성 컨트롤과 로고는 예외다. 대비가 모자라면 색 쌍, 측정값, 기준을 함께 적는다.

## §4 글자
1. 글꼴은 3개 이하로 쓴다. 크기 단계는 적게 두고, 단계마다 줄간격·굵기를 묶어 쓰임 이름(`text-body-sm`)을 붙인다. 제목 크기는 단계가 내려갈수록 작아지고 본문보다 작지 않다.
2. 쓰는 굵기와 스타일의 글꼴 파일을 모두 불러온다(브라우저가 굵기를 합성하지 않게). 한글 웹폰트는 서브셋이나 동적 서브셋으로 불러오고 `font-display: swap` 을 쓴다.
3. 줄간격은 단위 없는 값으로 쓴다. 큰 표시 글자 1.1, 제목 1.2–1.3, 본문 1.5–1.6 이다. 세 줄 넘게 감기는 글은 1.4 이상이다.
4. 글자 간격은 `em` 으로 쓴다. 24px 이상 제목은 `-0.01em`–`-0.02em`, 14px 이하 대문자 라벨은 `0.05em`, 본문은 0 이다.
5. 크기 하한은 긴 본문 16px, 입력·메뉴 14px, 캡션 13px 이다. 12px 미만은 쓰지 않는다. 단위는 `rem` 이다. 18px 미만 글자는 굵기 400 이상이다. 모바일 입력란 글자는 16px 이상이다(iOS 화면 확대 방지).
6. 한글 본문과 제목은 어절 단위로 줄을 바꾼다(`word-break: keep-all`). 긴 URL·ID 가 넘칠 수 있는 곳에는 `overflow-wrap: break-word` 를 함께 둔다. 제목·짧은 설명은 `text-wrap: balance`, 문단은 `text-wrap: pretty` 를 쓴다. 정렬은 `text-align: start` 이고 양쪽 정렬을 쓰지 않는다. 긴 본문은 줄 길이 상한(`max-width`)을 둔다.
7. 한글에는 기울임을 쓰지 않는다. 한글 글꼴에는 기울임체가 없어 기울여 합성된다. 강조는 굵기나 색으로 한다.
8. 타이머·카운터·가격·표의 숫자 열은 `font-variant-numeric: tabular-nums` 를 쓴다. 문장 속 숫자는 그대로 둔다.
9. 말줄임으로 자른 글에는 전체를 볼 방법(펼치기, 상세 화면, 키보드로도 닿는 툴팁)을 둔다.
10. 쓸모 있는 글은 선택할 수 있게 둔다. `user-select: none` 은 끌기 표면에만 쓴다.

## §5 레이아웃
1. 여백으로 먼저 묶고, 그다음 배경 면, 구분선은 마지막에 쓴다. 묶음 사이 간격은 묶음 안 간격의 2배 이상이다(안이 8px 이면 사이는 16px 이상).
2. 정렬 기준선을 몇 개로 정하고 모두 거기에 맞춘다. 중첩 단계마다 시스템 간격 한 단계를 쓴다.
3. 중요한 내용을 위와 시작 쪽에 둔다. 한 줄 안에서는 식별 내용이 앞, 메타 정보와 동작이 뒤다. 보이는 순서와 DOM 순서를 같게 둔다(`order`·`row-reverse` 로 바꾸지 않는다).
4. 화면마다 주 동작은 하나다. 보조 동작이 3개를 넘으면 메뉴로 묶는다.
5. 컨트롤은 배경 면·테두리·밑줄·고정 자리(도구 막대)로 본문 글과 구별한다.
6. 화면 밖이나 접힌 내용에는 보이는 단서를 둔다(다음 항목이 스크롤 끝에서 16–32px 보이게, 펼치기 컨트롤).
7. 테두리 없는 글자·아이콘 버튼 사이는 24px, 테두리나 채움이 있는 컨트롤 사이는 12px 에서 시작한다. 시스템 밀도가 있으면 그것을 따른다. 누르는 영역은 겹치지 않는다.
8. 배경과 미디어는 화면 끝까지, 컨트롤과 글은 레이아웃 여백과 안전 영역 안에 둔다. 모바일 전체 폭 버튼은 좌우 16px 안쪽에 둔다. 고정 헤더가 있으면 `scroll-padding-block-start` 를 그 높이로 둔다.
9. 글이 담기는 상자는 고정 폭·높이 대신 `max-width`·`min-height` 를 쓰고 줄이 감기게 둔다. 중요한 동작을 확대나 스크롤로 잘리는 곳에 두지 않는다.
10. 브레이크포인트는 기기 크기가 아니라 내용이 깨지는 지점에서 정한다. 가장 작은 폭과 가장 큰 폭을 먼저 확인한다.

## §6 표면·동작
1. 겹친 면이 고른 안쪽 여백을 가지면 바깥 모서리는 안쪽 모서리 + 여백 + 테두리 두께다. 여백이 24px 를 넘거나 일부러 고르지 않으면 각자 모서리 토큰을 쓴다.
2. 높이감은 그림자로, 구조(구분선·표·선택 상태·입력란 경계)는 테두리로 나타낸다.
3. 상호작용 상태 변화는 keyframes 가 아니라 transition 으로 한다(중간에 되돌릴 수 있다). `transition-property` 에 바뀌는 속성만 적고 `all` 을 쓰지 않는다.
4. 누른 버튼은 150ms `ease-out` 으로 0.96배로 줄어든다. 비활성 버튼은 줄지 않는다.
5. 입력, 행 hover, 탭 전환처럼 자주 하는 조작은 즉시 반응하거나 `opacity`·`background-color` 만 150ms 이하로 바꾼다. 큰 움직임은 첫 로드, 성공, 빈 상태처럼 드문 순간에만 쓴다.
6. 나갈 때는 들어올 때보다 짧고 작게 움직인다(150ms, 12px 이내). 처음 렌더할 때 기본 상태를 애니메이션으로 띄우지 않는다.
7. 움직임이 상태를 알리는 유일한 수단이 아니다. 움직임이 끝난 뒤에도 색·아이콘·라벨이 남는다.
8. hover 효과는 `@media (hover: hover)` 안에 둔다.
9. 스크롤되는 대화창·서랍·메뉴에 `overscroll-behavior: contain` 을 둔다.
10. 아이콘 선 굵기는 옆 글자 굵기를 따른다(굵기 400 에 1.5px, 700 에 2.5px). 한 화면에 아이콘 세트는 하나다.
11. 테마를 바꿀 때는 전환 효과를 잠시 끈다.

## §7 상태
1. 화면과 컴포넌트마다 해당하는 상태를 모두 만든다: 기본, hover, focus, 눌림, 비활성, 로딩, 빈 상태, 오류, 좁은 폭(320px), 긴 글(긴 이름, 번역으로 늘어난 라벨).
2. designer 는 이 상태를 시안에 넣는다. director 는 디자인 시스템 컴포넌트마다 상태별 미리보기를 둔다.
3. developer 는 구현한 상태를 실제로 렌더해 확인한다. 프로젝트에 상태 미리보기 도구(Storybook 등)나 E2E 도구가 있으면 그것을 쓴다. 없으면 임시 확인 페이지를 만들어 상태마다 데이터를 바꿔 보고, 확인한 뒤 지운다(커밋하지 않는다). 리포트에 확인한 상태, 확인 방법(명령·화면), 확인하지 못한 상태와 이유를 적는다.

## 자동 검사
`orch review` 는 Codex 를 부르기 전에 태스크가 바꾼 파일에서 다음 의심 위치를 찾아 리뷰 프롬프트에 넣는다. 정당한 쓰임이 있으므로 자동 불합격은 아니다. 나머지는 리뷰어가 화면과 코드를 직접 보고 판단한다.
- §1 의 1번(이모지), 2번(왼쪽 줄 강조), 3번(파랑·보라 그라디언트)
- 지운 줄에서 줄어든 접근성·글자 신호(접근 가능한 이름, 포커스, 동작 줄이기, 대체 텍스트, 라벨 연결, `lang`, `keep-all`, `tabular-nums`)
- 추가한 줄의 확대 막기(`user-scalable=no`·`maximum-scale=1`)와 `focus-visible` 없는 포커스 제거(`outline: none`)

## 출처
§2–§7 과 리뷰 방식(변경분 분류, 지운 줄 읽기, 즉시 차단 항목, 싼 수정 순서)은 jakubkrehel/skills(https://github.com/jakubkrehel/skills, MIT)의 better-accessibility·better-colors·better-typography·better-layout·better-ui·interface-review·better-interface·state-machine 에서 가져와 orca-team 흐름에 맞게 새로 압축했다. 한글 줄바꿈·기울임·웹폰트 서브셋, 앱 플랫폼 대응, 리포트·리뷰 연결은 orca-team 이 더했다.
```

- [ ] **Step 4: `director.md` 를 고친다**

`orca-team/templates/roles/director.md` 에서 다음을 바꾼다. 각 "지금" 문장은 파일에 한 번만 있다.

1. 입력 절
   - 지금: ``- `.orchestration/design-rules.md`: AI 티 금지 목록. 모든 단계의 산출물이 지킨다.``
   - 바꿀 것: ``- `.orchestration/design-rules.md`: 디자인 규칙(§1 AI 티 금지 목록, §2–§7 접근성·색·글자·레이아웃·표면·동작·상태). 모든 단계의 산출물이 지킨다.``
2. 단계 ③ 의 줄 `   - 토큰: 색(라이트 테마 필수, 다크는 기획이 요구할 때만), 글꼴 패밀리와 크기 단계, 간격, 모서리, 그림자` 바로 아래에 다음 두 줄을 넣는다.

```markdown
   - 색 토큰은 design-rules §3 을 따른다: 중립·강조 램프와 실제로 쓰는 상태 램프만 두고, 기본 토큰은 색 이름, 의미 토큰은 역할 이름으로 짓는다. 의미 토큰의 대비는 실제로 그 위에 렌더되는 배경과 잰다.
   - 글자 토큰은 design-rules §4 를 따른다: 크기 단계마다 줄간격·굵기를 묶어 쓰임 이름을 붙인다. 한글 줄바꿈(`word-break: keep-all`)과 웹폰트 불러오기 방법(서브셋, `font-display: swap`)을 README 사용 규칙에 적는다.
```

3. 단계 ③ 의 컴포넌트 줄에서 `상태(기본, hover, focus, disabled, error)별` 을 `상태(기본, hover, focus, 눌림, disabled, 로딩, error 중 해당하는 것)별` 로 바꾼다.
4. 산출물 표의 리포트 행에서 `` `### 금지 목록 점검` `` 을 `` `### 디자인 점검` `` 으로 바꾼다.
5. 표 아래 줄
   - 지금: ``- 리포트 `### 금지 목록 점검` 소절(현재 라운드 `## r{round}` 절 안)에는 `.orchestration/design-rules.md` 항목마다 지켰는지와, 예외가 있으면 그 근거를 적는다.``
   - 바꿀 것: ``- 리포트 `### 디자인 점검` 소절(현재 라운드 `## r{round}` 절 안)에는 `.orchestration/design-rules.md` 첫머리가 정한 세 가지를 적는다: §1 항목마다 지켰는지, §2–§6 에서 어긴 곳과 예외·근거, §7 에서 확인한 상태와 확인 방법.``
6. 품질 기준
   - 지금: `- 디자인 시스템의 색 대비는 본문 4.5:1, 24px 이상 글자와 아이콘·테두리 3:1 이상이다.`
   - 바꿀 것: ``- 디자인 시스템의 색 대비는 `.orchestration/design-rules.md` §3 의 7번을 지킨다(본문 4.5:1, 24px 이상 글자와 아이콘·테두리 3:1 이상).``

- [ ] **Step 5: `designer.md` 를 고친다**

`orca-team/templates/roles/designer.md`:

1. 입력 절: ``- `.orchestration/design-rules.md`(AI 티 금지 목록)`` → ``- `.orchestration/design-rules.md`(디자인 규칙: §1 AI 티 금지 목록, §2–§7 품질 규칙)``
2. 시스템 적용 규칙 절의 마지막 줄 ``- `.orchestration/design-rules.md` 를 지킨다.`` → ``- `.orchestration/design-rules.md` 를 지킨다. §7 의 상태(기본, hover, focus, 눌림, 비활성, 로딩, 빈 상태, 오류, 좁은 폭, 긴 글 중 해당하는 것)를 시안에 넣는다.``
3. 산출물 절의 리포트 줄에서 ``, `### 금지 목록 점검`(`.orchestration/design-rules.md` 항목마다 지켰는지, 예외와 근거)`` 를 ``, `### 디자인 점검`(`.orchestration/design-rules.md` 첫머리가 정한 세 가지: §1 항목마다 지켰는지, §2–§6 에서 어긴 곳과 예외·근거, §7 에서 시안에 넣은 상태)`` 로 바꾼다.
4. 품질 기준: `- 모든 화면에 빈 상태, 로딩, 오류 상태와 반응형(모바일·데스크톱 기준폭)을 넣는다.` → `- 모든 화면에 design-rules §7 의 상태(빈 상태, 로딩, 오류 포함)와 반응형(모바일·데스크톱 기준폭)을 넣는다.`

- [ ] **Step 6: `developer.md` 를 고친다**

`orca-team/templates/roles/developer.md`:

1. 입력 절: ``- `.orchestration/design-rules.md`(AI 티 금지 목록. UI 작업에 적용한다)`` → ``- `.orchestration/design-rules.md`(디자인 규칙: §1 AI 티 금지 목록, §2–§7 품질 규칙. UI 작업에 적용한다)``
2. UI 작업 규칙 절의 마지막 줄
   - 지금: ``- `.orchestration/design-rules.md` 를 지킨다. UI 를 바꾼 태스크의 리포트의 현재 라운드 `## r{round}` 절 안에 `### 금지 목록 점검` 소절(항목마다 지켰는지, 예외와 근거)을 둔다.``
   - 바꿀 것(두 줄):

```markdown
- `.orchestration/design-rules.md` 를 지킨다. UI 를 바꾼 태스크의 리포트의 현재 라운드 `## r{round}` 절 안에 `### 디자인 점검` 소절(§1 항목마다 지켰는지, §2–§6 에서 어긴 곳과 예외·근거, §7 에서 확인한 상태와 확인 방법)을 둔다.
- 구현한 화면·컴포넌트의 상태(design-rules §7)를 실제로 렌더해 확인한다. 상태 미리보기 도구(Storybook 등)나 E2E 도구가 있으면 그것을 쓰고, 없으면 임시 확인 페이지를 만들어 상태마다 데이터를 바꿔 본 뒤 지운다(커밋하지 않는다). 확인하지 못한 상태는 이유를 `### 디자인 점검` 에 적는다.
```

- [ ] **Step 7: `text-rules.md` 와 `lint.py` 설명을 고친다**

1. `orca-team/templates/text-rules.md`: ``8. 이모지 장식은 `.orchestration/design-rules.md` 1번을 따른다.`` → ``8. 이모지 장식은 `.orchestration/design-rules.md` §1 의 1번을 따른다.``
2. `orca-team/lib/orch/lint.py` 첫 줄의 `.orchestration/design-rules.md 1~3번` → `.orchestration/design-rules.md §1 의 1–3번`

- [ ] **Step 8: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_templates -v 2>&1 | tail -3`
Expected: `OK`

Run: `grep -rn "금지 목록 점검" orca-team/templates orca-team/lib` (저장소 루트)
Expected: `orca-team/templates/review-design.md` 의 5번(이전 버전 이름 받기) 한 줄만 나온다.

- [ ] **Step 9: 전체 테스트를 돌린다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` (저장소 루트)
Expected: `OK`, 경고 없음.

- [ ] **Step 10: 커밋한다**

공개 저장소 grep 을 돌린 뒤:

```bash
git add orca-team/templates/design-rules.md orca-team/templates/roles/director.md orca-team/templates/roles/designer.md orca-team/templates/roles/developer.md orca-team/templates/text-rules.md orca-team/lib/orch/lint.py orca-team/tests/test_templates.py
git commit -m "feat(orca-team): 디자인 규칙을 접근성·색·글자·레이아웃·표면·동작·상태로 넓히고 리포트를 디자인 점검으로 바꾼다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 버전 0.5.2 와 문서

**Files:**
- Modify: `orca-team/lib/orch/paths.py:8`, `orca-team/.claude-plugin/plugin.json:3`, `.claude-plugin/marketplace.json:10`
- Modify: `README.md` (`## 디자인 디렉터` 절)
- Modify: `docs/superpowers/specs/2026-10-30-orca-team-design.md` (§13 끝)
- Modify: `docs/superpowers/2026-11-05-orca-team-status.md`
- Test: `orca-team/tests/test_cli.py:9`, `orca-team/tests/test_templates.py`

**Interfaces:**
- Consumes: Task 1–4 의 이름(`diff_lines`, `design_findings`, `cap`, `design_diff_lint`, `guarded`, `### 디자인 점검`).
- Produces: 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

1. `orca-team/tests/test_cli.py:9` 의 `"orca-team 0.5.1"` 을 `"orca-team 0.5.2"` 로 바꾼다.
2. `orca-team/tests/test_templates.py` 클래스 끝에 다음을 더한다.

```python
    def test_docs_describe_design_rules(self):
        readme = (PLUGIN.parent / "README.md").read_text(encoding="utf-8")
        for key in ("### 디자인 점검", "https://github.com/jakubkrehel/skills", "`(새로 생김)`", "`(회귀)`", "즉시 차단 항목",
                    "0.5.1 이하로 만든 프로젝트", "docs/superpowers/specs/2026-11-06-orca-team-design-rules-design.md",
                    "docs/superpowers/plans/2026-11-06-orca-team-design-rules.md"):
            self.assertIn(key, readme)
        self.assertLess(readme.index("## 디자인 디렉터"), readme.index("https://github.com/jakubkrehel/skills"))
        self.assertLess(readme.index("https://github.com/jakubkrehel/skills"), readme.index("## 문구 규칙"))
        spec = (PLUGIN.parent / "docs/superpowers/specs/2026-10-30-orca-team-design.md").read_text(encoding="utf-8")
        section = spec[spec.index("## 13."):]
        for key in ("### 디자인 규칙 (2026-11-06, 0.5.2)", "design_diff_lint", "diff_lines", "lint.cap", "guarded"):
            self.assertIn(key, section)
        status = (PLUGIN.parent / "docs/superpowers/2026-11-05-orca-team-status.md").read_text(encoding="utf-8")
        for key in ("(2026-11-06, 0.5.2)", "| 0.5.2 |", "specs/2026-11-06-orca-team-design-rules-design.md"):
            self.assertIn(key, status)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_cli test_templates -v 2>&1 | tail -5`
Expected: 버전 문자열과 `test_docs_describe_design_rules` 실패.

- [ ] **Step 3: 버전을 올린다**

- `orca-team/lib/orch/paths.py`: `VERSION = "0.5.1"` → `VERSION = "0.5.2"`
- `orca-team/.claude-plugin/plugin.json`: `"version": "0.5.1"` → `"version": "0.5.2"`
- `.claude-plugin/marketplace.json`: `"version": "0.5.1"` → `"version": "0.5.2"`

- [ ] **Step 4: README 를 고친다**

`README.md` 의 `## 디자인 디렉터` 절에서 `- AI 티 금지 목록 \`.orchestration/design-rules.md\`(고정 이모지, …` 로 시작하는 한 줄 전체를 다음 세 줄로 바꾼다.

```markdown
- 디자인 규칙 `.orchestration/design-rules.md` 를 director·designer·developer 가 지키고 리포트 `### 디자인 점검` 에 점검 결과를 적는다. §1 은 AI 티 금지 목록(고정 이모지, 왼쪽 줄 카드, 파랑·보라 그라디언트 등 10개), §2–§7 은 접근성(WCAG 2.2 AA), 색, 글자(한글 어절 줄바꿈 포함), 레이아웃, 표면·동작, 상태 규칙이다. 품질 규칙은 [jakubkrehel/skills](https://github.com/jakubkrehel/skills)(MIT)에서 필요한 것만 가져와 압축하고 한국어·앱 플랫폼 내용을 더했다. developer 는 구현한 상태(빈 상태·로딩·오류·좁은 폭 등)를 실제로 렌더해 확인한다.
- Codex 디자인 리뷰는 이 태스크가 만든 문제만 `(새로 생김)`·`(회귀)`·`(기존)` 으로 나눠 지적하고, diff 의 지운 줄을 읽어 회귀를 찾는다. 접근 가능한 이름이 없는 컨트롤, 포커스 표시 없음, 동작 줄이기 무시, 320px·200% 확대에서 잘림, 대비 미달 같은 즉시 차단 항목은 P1 이다. 고칠 방향은 지우기 → 플랫폼 기본 → 기존 토큰 재사용 → 값 교정 → 새로 추가 순으로 고른다.
- `orch review` 는 Codex 를 부르기 전에 바뀐 파일에서 이모지·왼쪽 줄 강조·파랑·보라 그라디언트 의심 위치를, 태스크 diff 에서 지운 줄로 줄어든 접근성 신호(`aria-label`, 포커스, `prefers-reduced-motion`, `keep-all` 등)와 추가한 줄의 확대 막기·포커스 표시 제거를 찾아 리뷰 프롬프트에 경고로 넣는다(자동 불합격은 아니다).
```

같은 절의 마지막 줄(`  4. 새 흐름은 경로가 바뀐다:` 로 시작하는 줄) 바로 아래, `## 문구 규칙` 앞 빈 줄 전에 다음 두 줄을 넣는다.

```markdown
- 0.5.1 이하로 만든 프로젝트도 플러그인을 업데이트하면 새 리뷰 방식과 자동 검사가 바로 붙는다(리포트의 이전 소절 이름 `### 금지 목록 점검` 도 받는다). 워커가 새 규칙으로 작업하게 하려면 `orca-team/templates/design-rules.md` 를 `.orchestration/design-rules.md` 로, `orca-team/templates/roles/director.md`·`designer.md`·`developer.md` 를 `.orchestration/roles/` 로 복사한다(프로젝트에서 직무 문서를 고쳤다면 비교해 합친다). 코디네이터 터미널을 닫고 `/orca-team:up` 으로 다시 띄운다.
- 설계: `docs/superpowers/specs/2026-11-06-orca-team-design-rules-design.md`, 구현 계획: `docs/superpowers/plans/2026-11-06-orca-team-design-rules.md`.
```

- [ ] **Step 5: 기본 설계 §13 에 절을 더한다**

`docs/superpowers/specs/2026-10-30-orca-team-design.md` 끝(§13 "코디네이터 자동 부활" 절 다음)에 빈 줄 하나와 다음을 더한다. 구현하면서 설계와 다르게 정한 것이 있으면 이 목록에 한 줄씩 더한다.

```markdown
### 디자인 규칙 (2026-11-06, 0.5.2)

설계: `2026-11-06-orca-team-design-rules-design.md`. 구현에서 정한 것:
- `textlint.diff_lines` 가 태스크 커밋 diff 의 추가한 줄과 지운 줄을 수만큼 돌려준다. `added_lines` 는 그 추가한 줄의 집합이고, 문구 검사와 디자인 diff 검사가 같은 diff 읽기를 쓴다.
- `designdiff.design_diff_lint` 는 신호마다 지운 줄과 추가한 줄의 일치 수를 비교해 줄어든 것만 알린다. 같은 속성을 옮기거나 줄만 바꾼 경우는 수가 같아 걸러진다. 태스크가 지운 파일은 지운 줄 검사만 한다.
- 포커스 제거는 파일에 `focus-visible` 도, `focus:outline-none` 이 아닌 Tailwind `focus:` 클래스도 없을 때만 알린다. `:focus` 셀렉터는 대체로 보지 않는다(`:focus { outline: none }` 이 찾을 대상이다). 확대 막기는 뷰포트 메타와 Next.js `viewport` 객체(`userScalable: false`, `maximumScale: 1`)를 본다.
- 내용 검사는 `lint.design_findings`(상한 없음)로 나누고, `review.design_section` 이 내용 검사 다음에 diff 검사를 이어 붙여 `lint.cap` 으로 50건에서 자른다. `lint.design_lint` 는 `cap(design_findings(...))` 로 남았다.
- 검사 예외는 `review.guarded` 가 "자동 검사 실패" 한 줄로 바꾼다. 디자인 절의 두 검사와 문구 절이 같은 함수를 쓴다.
- 리포트 소절은 `### 디자인 점검` 이다. 셋업이 기존 파일을 덮어쓰지 않아 이전 프로젝트는 예전 직무 문서를 쓰므로, `review-design.md` 5번이 `### 금지 목록 점검` 과 번호만 있는 금지 목록도 받는다.
```

- [ ] **Step 6: 상태 문서를 고친다**

`docs/superpowers/2026-11-05-orca-team-status.md`:

1. 제목 `# orca-team 현재 상태 (2026-11-06, 0.5.1)` → `# orca-team 현재 상태 (2026-11-06, 0.5.2)`
2. 버전 표의 0.5.1 행 바로 아래에 다음 행을 더한다.

```markdown
| 0.5.2 | 디자인 규칙: design-rules.md 를 §0–§7(접근성·색·글자·레이아웃·표면·동작·상태)로 넓힘, Codex 디자인 리뷰를 변경분 분류·지운 줄 읽기·즉시 차단 항목 기준으로, 지운 줄 접근성 신호 자동 검사(designdiff) | `specs/2026-11-06-orca-team-design-rules-design.md`, `plans/2026-11-06-orca-team-design-rules.md` |
```

3. 검증 절의 `(543개, 2026-11-06 통과)` 의 543 을 Step 8 전체 테스트 출력 `Ran N tests` 의 N 으로 바꾼다.
4. `## 실환경에서 확인하지 않은 흐름` 목록 끝에 다음 줄을 더한다.

```markdown
- 0.5.2 디자인 규칙 전체(리포트 `### 디자인 점검`, Codex 의 `(새로 생김)`·`(회귀)` 분류와 즉시 차단 항목 P1, diff 자동 검사). 확인 계획은 설계 §13 에 있다.
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd orca-team/tests && python3 -m unittest test_cli test_templates -v 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 8: 전체 테스트를 돌린다**

Run: `PYTHONWARNINGS=default bash orca-team/tests/run.sh 2>&1 | tail -3` (저장소 루트)
Expected: `Ran N tests` 와 `OK`, 경고 없음. 이 N 을 Step 6 의 3번에 넣었는지 확인한다.

Run: `grep -nP '\d~\d' orca-team/templates/design-rules.md orca-team/templates/review-design.md docs/superpowers/2026-11-05-orca-team-status.md`
Expected: 출력 없음. README 는 새로 넣은 줄에 물결표가 없는지 눈으로 확인한다(기존 줄은 그대로 둔다).

- [ ] **Step 9: 커밋한다**

공개 저장소 grep 을 돌린 뒤:

```bash
git add orca-team/lib/orch/paths.py orca-team/.claude-plugin/plugin.json .claude-plugin/marketplace.json README.md docs/superpowers/specs/2026-10-30-orca-team-design.md docs/superpowers/2026-11-05-orca-team-status.md orca-team/tests/test_cli.py orca-team/tests/test_templates.py
git commit -m "docs: orca-team 0.5.2 디자인 규칙을 README·기본 설계·상태 문서에 적고 버전을 올린다

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 끝난 뒤 (컨트롤러)

1. 브랜치 전체 최종 리뷰(설계 대비, 정규식 오탐·누락, 이전 프로젝트 호환)를 받고 지적을 반영한다.
2. 실환경 확인(설계 §13)은 사용자 승인 뒤에 한다. 결과를 설계 문서 §13 아래에 적고 상태 문서의 "확인하지 않은 흐름" 줄을 고친다.
3. 병합·푸시는 사용자 결정에 따른다.
