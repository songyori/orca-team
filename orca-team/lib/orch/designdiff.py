"""디자인 변경 검사(orch review). 태스크 커밋 diff 에서 지운 줄로 줄어든 접근성·글자 신호와, 이 태스크가 추가한 줄의
확대 막기·포커스 표시 제거를 찾는다(.orchestration/design-rules.md §2·§4).

결과는 경고다. 같은 변경 안에 대체가 있을 수 있으므로 판정은 Codex 가 한다. 표준 라이브러리 정규식만 쓴다."""
from __future__ import annotations

import re
from pathlib import Path

from .globs import matches_any
from .lint import LINT_EXCLUDE, LINT_SUFFIXES, read_source, snippet
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
    (":focus-visible", re.compile(r":focus-visible\b")),
    (":focus", re.compile(r":focus\b(?!-)")),
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
# 대체 포커스 표시: focus-visible(CSS·Tailwind), 또는 focus:outline-none 이 아닌 Tailwind focus: 클래스(focus:ring-2,
# 겹친 변형 dark:focus:ring-2, 중요 표시 focus:!ring-2).
# :focus 셀렉터는 대체로 보지 않는다(:focus { outline: none } 이 바로 찾을 대상이다).
FOCUS_ALT_RE = re.compile(r"focus-visible|(?<![\w-])focus:(?!outline-none(?![\w-]))[\w\[!]")


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
        text = read_source(root, rel)
        if text is not None:
            found += added_blockers(rel, text, set(added))
    return found
