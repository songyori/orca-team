"""소유 경로 glob. `**` 는 여러 단계, `*`·`?` 는 한 단계 안에서만 맞는다."""
from __future__ import annotations

import re
from functools import lru_cache

GLOB_CHARS = "*?["


@lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, pattern: str) -> bool:
    return bool(_regex(pattern).match(path))


def matches_any(path: str, patterns) -> bool:
    return any(matches(path, p) for p in patterns)


def literal_prefix(pattern: str) -> str:
    cut = min((pattern.index(c) for c in GLOB_CHARS if c in pattern), default=len(pattern))
    return pattern[:cut]


def overlaps(a: str, b: str) -> bool:
    """보수적 판정. 리터럴 접두가 서로의 접두면 겹친다고 본다."""
    pa, pb = literal_prefix(a), literal_prefix(b)
    if pa == a and pb == b:
        return a == b
    if pa == a:
        return matches(a, b)
    if pb == b:
        return matches(b, a)
    return pa.startswith(pb) or pb.startswith(pa)


def any_overlap(xs, ys) -> list[tuple[str, str]]:
    return [(x, y) for x in xs for y in ys if overlaps(x, y)]
