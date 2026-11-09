"""리뷰: 커밋·소유 경로 사전 검사 → 내장 Codex 래퍼(scripts/codex-run.sh)로 Codex 리뷰 → 판정 기록·커밋."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import plan, testgate
from .config import Config
from .designdiff import design_diff_lint
from .globs import matches_any
from .lint import DESIGN_ROLES, cap, design_findings
from .paths import TEMPLATES, OrchError, Project, codex_runner
from .tasks import (ACTIVE, TaskDoc, all_states, deps_of, load_doc, load_state, must_state, owns_of, review_pid,
                    save_state)
from .textlint import RULES_FILE, text_lint, text_targets
from .util import acquire_pid, git, git_commit_paths, git_ok, now, read_json, release_pid, render, require_drive

VERDICT_RE = re.compile(r"^VERDICT:\s*(APPROVE|REQUEST_CHANGES)\s*$", re.M)
FINDINGS_RE = re.compile(r"^## 지적 사항[ \t]*\n(.*?)(?=^## |^VERDICT:|\Z)", re.M | re.S)
PRIOR_RE = re.compile(r"^## 이전 지적 확인[ \t]*\n(.*?)(?=^## |^VERDICT:|\Z)", re.M | re.S)
BASE_EXCLUDE = [".orchestration/**", "docs/reviews/**"]


def commit_paths(root: Path, sha: str) -> list[str]:
    out = git(root, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z", "--root", sha)
    return [name for name in out.split("\0") if name]


def task_commits(root: Path, task_id: str, base: str) -> list[tuple[str, str]]:
    """base 이후 `[T-xxx]` 작업 커밋. orch 가 만든 spec·review 커밋은 제목과 건드린 경로가 모두 맞을 때만 뺀다."""
    tid = re.escape(task_id)
    spec_re = re.compile(rf"^\[{tid}\] spec: ")
    review_re = re.compile(rf"^\[{tid}\] review r\d+: (APPROVE|REQUEST_CHANGES)$")
    commits = []
    for line in git(root, "log", "--reverse", "--format=%H%x09%s", f"{base}..HEAD").splitlines():
        sha, _, subject = line.partition("\t")
        if not subject.startswith(f"[{task_id}]"):
            continue
        if spec_re.match(subject) and all(matches_any(f, [".orchestration/**"]) for f in commit_paths(root, sha)):
            continue
        if review_re.match(subject) and all(matches_any(f, [f"docs/reviews/{task_id}/**"]) for f in commit_paths(root, sha)):
            continue
        commits.append((sha, subject))
    return commits


def changed_files(root: Path, shas: list[str]) -> list[str]:
    files = set()
    for sha in shas:
        files.update(commit_paths(root, sha))
    return sorted(files)


def violations(files: list[str], owns: list[str], task_id: str) -> list[str]:
    allowed = [*owns, f"docs/tasks/{task_id}/**"]
    return [f for f in files if not matches_any(f, allowed)]


def worktree_status(root: Path, untracked: bool = True) -> tuple[dict[str, str], set[str]]:
    """(추적 파일 변경 경로 → XY, 추적 안 된 파일 경로). 이름 바꾸기는 지움과 추가로 본다(--no-renames)."""
    entries = git(root, "status", "--porcelain=v1", "-z", "--no-renames",
                  f"--untracked-files={'all' if untracked else 'no'}").split("\0")
    tracked, new, i = {}, set(), 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":  # --no-renames 라 나오지 않지만 원래 경로 항목을 건너뛴다
            i += 1
        if status == "??":
            new.add(path)
        else:
            tracked[path] = status
    return tracked, new


def content_hash(target: Path) -> str:
    """파일 내용 해시. 없으면 "-", 읽을 수 없으면(동시에 쓰거나 지우는 중) "?"."""
    try:
        return hashlib.sha1(target.read_bytes()).hexdigest() if target.is_file() else "-"
    except OSError:
        return "?"


def snapshot(root: Path, exclude: list[str]) -> tuple[dict[str, str], set[str]]:
    """(exclude 밖 추적 파일 변경의 내용 해시, 추적 안 된 파일 전체). 추적 안 된 파일은 호출한 쪽이 거른다."""
    tracked, untracked = worktree_status(root)
    hashes = {path: content_hash(root / path) for path in tracked if not matches_any(path, exclude)}
    return hashes, untracked


def uncommitted_owned(root: Path, owns: list[str], task_id: str) -> list[str]:
    """이 태스크 경로(소유 경로와 리포트)에서 커밋하지 않은 추적 파일 변경."""
    tracked, _ = worktree_status(root, untracked=False)
    allowed = [*owns, f"docs/tasks/{task_id}/**"]
    return sorted(path for path in tracked if matches_any(path, allowed))


def read_verdict(path: Path) -> str | None:
    if not path.is_file():
        return None
    found = VERDICT_RE.findall(path.read_text(encoding="utf-8"))
    return found[-1] if found else None


def run_dir_for(p: Project, task_id: str, rnd: int, attempt: int) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / "orca-team" / "runs" / p.root.name / f"{task_id}-r{rnd}-{stamp}-a{attempt}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def guarded(check, *args) -> list[str]:
    """검사 하나의 결과. 검사 버그가 리뷰를 막으면 안 되므로 예외는 "자동 검사 실패" 한 줄(200자까지)이 된다.
    판정은 Codex 가 한다."""
    try:
        return check(*args)
    except Exception as exc:
        return [" ".join(f"자동 검사 실패: {type(exc).__name__}: {exc}".split())[:200]]


def bullets(found: list[str]) -> str:
    return "\n".join(f"- {item}" for item in found) if found else "- 없음"


VERIFY_DEFAULT = "리포트의 검증 출력을 그대로 믿지 말고 가능하면 직접 실행한다."
VERIFY_GATED = ("테스트는 아래 orch 테스트 결과를 쓴다. 전체 테스트를 돌리지 않고, 결과가 의심되면 그 테스트 하나만 "
                "직접 돌린다. 빌드·화면 확인은 직접 해도 된다.")
STATUS_TEXT = {"passed": "통과", "flaky": "통과(불안정 의심: 첫 실행은 실패했고 다시 실행해 통과했다)",
               "skipped": "돌리지 않음(관련 파일 없음)", "failed": "실패", "timeout": "시간 초과"}
DIRTY_SHOWN = 20
GROUP_FILES_SHOWN = 15


def describe(entry: dict, kind: str) -> list[str]:
    """테스트 기록 한 건을 프롬프트·리뷰 파일용 줄로."""
    head = f"{kind}: {STATUS_TEXT.get(entry.get('status'), entry.get('status'))}"
    if entry.get("command"):
        head += f" ({entry.get('seconds')}초, exit {entry.get('exit')})"
    lines = [head]
    if entry.get("command"):
        lines.append(f"{kind} 명령: `{entry['command']}`")
    if entry.get("log"):
        lines.append(f"{kind} 로그: {entry['log']}")
    first = entry.get("first")
    if isinstance(first, dict) and first.get("log"):
        lines.append(f"{kind} 첫 실행 로그: {first['log']}")
    dirty = entry.get("dirty") or []
    if dirty:
        more = f" 외 {len(dirty) - DIRTY_SHOWN}개" if len(dirty) > DIRTY_SHOWN else ""
        lines.append("실행 때 커밋 안 된 다른 파일: " + ", ".join(dirty[:DIRTY_SHOWN]) + more)
    return lines


def test_section(gate: dict | None) -> str:
    """quick 이 돌았으면(developer) 그 결과 절(templates/review-tests.md). 다음 절과 사이의 빈 줄까지 붙인다.
    tester 는 quick 이 없고 묶음 절이 full 을 보여 주므로 빈 문자열."""
    if not gate or "quick" not in gate:
        return ""
    return render((TEMPLATES / "review-tests.md").read_text(encoding="utf-8"),
                  {"test_lines": bullets(describe(gate["quick"], "quick"))}) + "\n"


def group_rows(p: Project, doc: TaskDoc) -> list[str]:
    rows = []
    for dep in deps_of(doc):
        st = load_state(p, dep) or {}
        commits = task_commits(p.root, dep, st["base"]) if st.get("base") else []
        files = changed_files(p.root, [sha for sha, _ in commits])
        shown = ", ".join(files[:GROUP_FILES_SHOWN]) or "없음"
        more = f" 외 {len(files) - GROUP_FILES_SHOWN}개" if len(files) > GROUP_FILES_SHOWN else ""
        rows.append(f"{dep} [{st.get('role', '?')}] {st.get('title', '')} — 작업 커밋 {len(commits)}개, "
                    f"바꾼 파일: {shown}{more}")
    return rows


def group_section(p: Project, doc: TaskDoc, gate: dict | None) -> str:
    """묶음 테스트(tester) 리뷰 절(templates/review-group.md). full 결과가 없으면 빈 문자열."""
    if not gate or "full" not in gate:
        return ""
    full = gate["full"]
    failed = full.get("status") not in testgate.PASSED and full.get("tail")
    tail = f"출력 끝:\n```\n{full['tail']}\n```\n" if failed else ""
    st = load_state(p, doc.id) or {}
    return render((TEMPLATES / "review-group.md").read_text(encoding="utf-8"), {
        "group_key": st.get("plan_key") or doc.id, "group_tasks": bullets(group_rows(p, doc)),
        "full_lines": bullets(describe(full, "full")), "full_tail": tail}) + "\n"


def gate_failure(task_id: str, entry: dict) -> str:
    """quick 이 통과하지 못했을 때 orch 사전 검사 리뷰 본문."""
    lines = ["orch 테스트 관문: quick 이 통과하지 못했다.", "", *(f"- {line}" for line in describe(entry, "quick"))]
    if entry.get("tail"):
        lines += ["", "출력 끝:", "```", entry["tail"], "```"]
    lines += ["", f"고친 뒤 `.orchestration/bin/orch test {task_id}` 로 다시 확인한다.",
              "실패가 이 태스크 탓이 아니면(소유 경로 밖 코드, 위의 커밋 안 된 다른 파일) 코드를 고치지 말고 근거를 리포트에 "
              "적은 뒤 프리앰블의 ask 명령으로 묻는다."]
    return "\n".join(lines)


def design_section(p: Project, cfg: Config, role: str, files: list[str], shas: list[str]) -> str:
    """디자인·UI 직무 리뷰에 넣는 기준과 orch 자동 검사 결과(templates/review-design.md). 자동 검사는 태스크 커밋 diff
    검사(designdiff.design_diff_lint) 다음에 바뀐 파일 내용 검사(lint.design_findings)를 이어 붙여 50건으로 자른다.
    다음 절과 사이의 빈 줄까지 붙여 돌려준다(review-prompt.md 의 자리표시가 다음 절 제목 바로 앞에 있다). 다른 직무와,
    director 직무가 없는 이전 버전(0.3.0) 프로젝트는 빈 문자열."""
    if role not in DESIGN_ROLES or plan.DIRECTOR_ROLE not in cfg.roles:
        return ""
    found = cap([*guarded(design_diff_lint, p.root, files, shas), *guarded(design_findings, p.root, files)])
    return render((TEMPLATES / "review-design.md").read_text(encoding="utf-8"), {"design_lint": bullets(found)}) + "\n"


def text_section(p: Project, files: list[str], shas: list[str]) -> str:
    """서비스 문구·문서를 바꾼 태스크 리뷰에 넣는 문구 기준과 orch 문구 자동 검사 결과(templates/review-text.md).
    직무와 무관하다. 다음 절과 사이의 빈 줄까지 붙여 돌려준다. text-rules.md 가 없는 이전 버전(0.4.x) 프로젝트와
    한글이 든 텍스트 대상 파일을 바꾸지 않은 태스크는 빈 문자열."""
    if not (p.root / RULES_FILE).is_file() or not text_targets(p.root, files):
        return ""
    found = guarded(text_lint, p.root, files, shas)
    return render((TEMPLATES / "review-text.md").read_text(encoding="utf-8"), {"text_lint": bullets(found)}) + "\n"


def last_codex_review(history) -> dict | None:
    """가장 최근의 Codex 리뷰 기록. orch 사전 검사 기록은 건너뛴다."""
    for entry in reversed(list(history)):
        if entry.get("stage") == "review" and entry.get("source") == "codex":
            return entry
    return None


def round_section(doc: TaskDoc, rnd: int, commits: list[tuple[str, str]], history) -> str:
    """라운드 2부터 좁혀 보는 범위(templates/review-round.md). 앞 라운드에 Codex 리뷰가 없으면 빈 문자열(전체를 본다)."""
    prev = last_codex_review(history)
    if rnd <= 1 or prev is None:
        return ""
    shas = [sha for sha, _ in commits]
    prev_head = prev.get("head")
    if prev_head in shas:
        new = commits[shas.index(prev_head) + 1:]
        new_commits = "\n".join(f"- {sha[:10]} {subject}" for sha, subject in new) or "- 없음(새 커밋이 없다)"
        head_text = f"`{prev_head[:10]}`"
    else:
        new_commits = "- 지난 리뷰의 커밋 기록이 없다. 위 리뷰 대상 커밋 전체를 본다"
        head_text = "기록 없음"
    prev_rnd = prev.get("round")
    return render((TEMPLATES / "review-round.md").read_text(encoding="utf-8"), {
        "round": rnd, "prev": prev_rnd, "prev_review": f"docs/reviews/{doc.id}/r{prev_rnd}.md",
        "prev_head": head_text, "new_commits": new_commits}) + "\n"


STRICT_ID_RE = re.compile(r"r\d+-F\d+")
STRICT_GRADE_RE = re.compile(r"\[(P[123])\]", re.I)
NONE_LINE_RE = re.compile(r"^\s*(?:[-+*#]+|\d+[.)])?[\s*#]*(?:해당\s*)?없음")
STRICT_PRIOR_RE = re.compile(r"^\s*(?:[-+]|\d+[.)])?\s*r\d+-F\d+\s*(?:\[P1\]\s*)?[:：]\s*미해결", re.I)
BROAD_P1_RE = re.compile(r"(?<![A-Za-z0-9])P1(?![A-Za-z0-9])", re.I)


def clean_line(line: str) -> str:
    """강조(`*`)와 코드 표기(백틱)를 뗀 줄. 엄격한 셈이 형식 차이에 흔들리지 않게 한다."""
    return line.replace("*", "").replace("`", "")


def strict_finding(line: str) -> bool:
    """`rN-Fk` 모양 id 가 있고 첫 등급 표시가 [P1] 인 줄. 순서와 글머리는 상관없다. 글머리 뒤가 `없음`·`해당 없음` 으로 시작하는 줄은 세지 않는다."""
    line = clean_line(line)
    if NONE_LINE_RE.match(line):
        return False
    grade = STRICT_GRADE_RE.search(line)
    return bool(STRICT_ID_RE.search(line) and grade and grade.group(1).upper() == "P1")


def p1_count(text: str) -> int | None:
    """넓은 셈(RC→APPROVE 로 내리는 것을 막는 데만): `## 지적 사항` 절에서 `P1`(앞뒤에 영숫자가 없다, 대소문자 무시)이 든 줄과
    `## 이전 지적 확인` 절에서 `미해결` 이 든 줄. `## 지적 사항` 절이 없으면(이전 형식) None."""
    match = FINDINGS_RE.search(text)
    if not match:
        return None
    count = sum(1 for line in match.group(1).splitlines() if BROAD_P1_RE.search(line))
    prior = PRIOR_RE.search(text)
    if prior:
        count += sum(1 for line in prior.group(1).splitlines() if "미해결" in line)
    return count


def strict_p1_count(text: str) -> int | None:
    """엄격한 셈(APPROVE→RC 로 올리는 데만): `## 지적 사항` 에서 `*` 를 지운 줄에 `rN-Fk` id 가 있고 첫 등급 표시가 [P1] 인 줄과
    `## 이전 지적 확인` 에서 `rN-Fk[ [P1]]: 미해결` 모양의 줄. 문장 속 `[P1]`·`미해결` 은 세지 않는다. 절이 없으면 None."""
    match = FINDINGS_RE.search(text)
    if not match:
        return None
    count = sum(1 for line in match.group(1).splitlines() if strict_finding(line))
    prior = PRIOR_RE.search(text)
    if prior:
        count += sum(1 for line in prior.group(1).splitlines() if STRICT_PRIOR_RE.match(clean_line(line)))
    return count


def align_verdict(path: Path, verdict: str) -> str | None:
    """판정을 맞춘다. RC 인데 넓은 셈이 0 이면 APPROVE 로, APPROVE 인데 엄격한 셈이 있으면 RC 로 바꾼다. 그 밖에는 그대로 둔다.
    바꿨으면 "원래→바꾼 것", 아니면 None."""
    text = path.read_text(encoding="utf-8")
    broad = p1_count(text)
    if broad is None:
        return None
    if verdict == "REQUEST_CHANGES":
        if broad:
            return None
        want, count = "APPROVE", 0
    else:
        count = strict_p1_count(text) or 0
        if not count:
            return None
        want = "REQUEST_CHANGES"
    last = list(VERDICT_RE.finditer(text))[-1]
    note = (f"> orch 판정 맞추기: 막는 지적(`## 지적 사항` 의 P1 줄, `## 이전 지적 확인` 의 미해결 줄) {count}개라 "
            f"{want} 로 바꿨다(원래 {verdict}).\n\n")
    path.write_text(text[:last.start()] + note + f"VERDICT: {want}" + text[last.end():], encoding="utf-8")
    return f"{verdict}→{want}"


def compose_prompt(p: Project, cfg: Config, doc: TaskDoc, rnd: int, review_rel: str,
                   commits: list[tuple[str, str]], files: list[str], gate: dict | None = None,
                   history=()) -> str:
    prior = [f"docs/reviews/{doc.id}/r{n}.md" for n in range(1, rnd)
             if (p.root / f"docs/reviews/{doc.id}/r{n}.md").is_file()]
    shas = [sha for sha, _ in commits]
    return render((TEMPLATES / "review-prompt.md").read_text(encoding="utf-8"), {
        "task_id": doc.id, "round": rnd, "role": doc.role, "role_title": cfg.title(doc.role), "title": doc.title,
        "review_path": review_rel, "prior_reviews": ", ".join(prior) or "없음",
        "commits": "\n".join(f"- {sha[:10]} {subject}" for sha, subject in commits),
        "files": "\n".join(f"- {f}" for f in files),
        "verify_rule": VERIFY_GATED if gate else VERIFY_DEFAULT,
        "test_section": test_section(gate),
        "round_section": round_section(doc, rnd, commits, history),
        "group_section": group_section(p, doc, gate),
        "design_section": design_section(p, cfg, doc.role, files, shas),
        "text_section": text_section(p, files, shas)})


def review_exclusions(p: Project, task_id: str) -> list[str]:
    exclude = list(BASE_EXCLUDE)
    for st in all_states(p):
        if st["id"] != task_id and st.get("status") in ACTIVE:
            exclude += [*st.get("owns", []), f"docs/tasks/{st['id']}/**"]
    return exclude


def codex_review(p: Project, cfg: Config, doc: TaskDoc, rnd: int, review_rel: str,
                 prompt: str) -> tuple[str, list[str]]:
    """(판정, 리뷰 중 새로 생긴 추적 안 된 파일). 추적 파일 내용 변경이나 HEAD 이동이 있으면 판정 무효(OrchError)."""
    launch = cfg.codex(doc.role, doc.front)
    runner = codex_runner()
    review_file = p.root / review_rel
    exclude_start = review_exclusions(p, doc.id)
    last_dir, untracked_new = None, set()
    for attempt in (1, 2):
        review_file.unlink(missing_ok=True)
        before, untracked_before = snapshot(p.root, exclude_start)
        head_before = git(p.root, "rev-parse", "HEAD").strip()
        run_dir = last_dir = run_dir_for(p, doc.id, rnd, attempt)
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        try:
            proc = subprocess.run([str(runner), "--profile", "run", "--cwd", str(p.root),
                                   "--prompt-file", str(run_dir / "prompt.txt"), "--run-dir", str(run_dir),
                                   "--model", launch.model, "--effort", launch.effort, "--bypass-hook-trust"],
                                  capture_output=True, text=True)
        except OSError as exc:
            raise OrchError(f"Codex 래퍼를 실행하지 못했다({runner}): {exc}", 12) from exc
        if proc.returncode in (2, 12):
            raise OrchError(f"Codex 래퍼 실행 불가(exit {proc.returncode}): {(proc.stdout + proc.stderr)[-600:]}",
                            12 if proc.returncode == 12 else 1)
        # 리뷰 도중 시작된 태스크의 소유 경로도 뺀다(시작 전·후 진행 태스크의 합집합).
        exclude = exclude_start + review_exclusions(p, doc.id)
        after, untracked_after = snapshot(p.root, exclude)
        head_after = git(p.root, "rev-parse", "HEAD").strip()
        if head_after != head_before:
            ff = git_ok(p.root, "merge-base", "--is-ancestor", head_before, head_after)
            moved = []
            if ff:
                log = git(p.root, "log", "--format=", "--name-only", "--no-renames", "-z",
                          f"{head_before}..{head_after}")
                net = git(p.root, "diff", "--name-only", "--no-renames", "-z", head_before, head_after)
                names = {n.strip("\n") for n in (log + "\0" + net).split("\0")}
                moved = sorted(n for n in names if n and not matches_any(n, exclude))
            if not ff or moved:
                why = "되돌리기/재작성(fast-forward 아님)" if not ff else f"허용 밖 경로 커밋: {', '.join(moved)}"
                raise OrchError(f"Codex 가 리뷰 중 HEAD 를 바꿨다({head_before[:10]} -> {head_after[:10]}): {why}. "
                                f"판정 무효. 변경은 그대로 두고 orch notify --type question 으로 메인에 올린다. "
                                f"run-dir: {run_dir}")
        touched = sorted(path for path in set(before) | set(after)
                         if before.get(path) != after.get(path) and not matches_any(path, exclude))
        if touched:
            raise OrchError(f"Codex 가 리뷰 중 추적 파일을 바꿨다: {', '.join(touched)}. 판정 무효. 변경은 그대로 두고 "
                            f"orch notify --type question 으로 메인에 올린다. run-dir: {run_dir}")
        # 테스트·빌드 산출물(__pycache__, dist 등)은 판정을 무효로 하지 않고 경고로만 남긴다.
        untracked_new |= {path for path in untracked_after - untracked_before if not matches_any(path, exclude)}
        status = read_json(run_dir / "status.json", {})
        if status.get("terminal_event") == "turn.completed":
            verdict = read_verdict(review_file)
            if verdict:
                return verdict, sorted(untracked_new)
    raise OrchError(f"Codex 리뷰가 2회 모두 판정을 내지 못했다. run-dir: {last_dir}. "
                    "orch notify --type question 으로 메인에 올린다")


def auto_review(p: Project, review_rel: str, task_id: str, rnd: int, reason: str) -> str:
    path = p.root / review_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {task_id} r{rnd} 리뷰 (orch 사전 검사)\n\n{reason}\n\nVERDICT: REQUEST_CHANGES\n",
                    encoding="utf-8")
    return "REQUEST_CHANGES"


def plan_problem(p: Project, cfg: Config, files: list[str]) -> str | None:
    """이 태스크 커밋이 tasks.toml 을 바꿨으면 plan apply 와 같은 규칙으로 검증하고, 어긴 곳이 있으면 그 설명을 돌려준다.

    accept 뒤(done)에는 fix 로 기획 태스크를 되돌릴 수 없으므로 리뷰에서 먼저 잡는다."""
    if plan.PLAN_FILE not in files:
        return None
    try:
        plan.check(p, cfg, append=bool(plan.load_plan_state(p)["tasks"]))
    except OrchError as exc:
        return str(exc)
    return None


def review(p: Project, cfg: Config, task_id: str) -> dict:
    """리뷰는 태스크마다 하나만 돈다(review.pid). reviewing 이 남았는데 리뷰 프로세스가 없으면 다시 리뷰한다."""
    require_drive(p)
    doc = load_doc(p, cfg, task_id)
    pid = review_pid(p, task_id)
    acquire_pid(pid, f"orch review {task_id}")
    try:
        return run_review(p, cfg, doc)
    finally:
        release_pid(pid)


def run_review(p: Project, cfg: Config, doc: TaskDoc) -> dict:
    task_id = doc.id
    st = must_state(p, task_id)
    if st.get("status") not in ("working", "reviewing"):
        raise OrchError(f"{task_id}: 리뷰는 워커가 끝난 working 상태(또는 리뷰가 끊긴 reviewing)에서만 한다"
                        f"(status={st.get('status')})", 2)
    rnd, owns = st["round"], owns_of(doc, cfg)
    review_rel = f"docs/reviews/{task_id}/r{rnd}.md"
    st["status"] = "reviewing"
    save_state(p, st)
    untracked: list[str] = []
    gate = None
    commits: list[tuple[str, str]] = []
    adjusted = None
    try:
        commits = task_commits(p.root, task_id, st["base"])
        files = changed_files(p.root, [sha for sha, _ in commits])
        bad = violations(files, owns, task_id)
        if not commits:
            reason = f"`[{task_id}]` 로 시작하는 작업 커밋이 base({st['base'][:10]}) 이후에 없다."
            verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
        elif bad:
            reason = (f"소유 경로 밖 파일을 커밋했다(허용: {', '.join(owns)}, docs/tasks/{task_id}/**):\n"
                      + "\n".join(f"- {f}" for f in bad))
            verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
        else:
            dirty = uncommitted_owned(p.root, owns, task_id)
            if dirty:
                raise OrchError(f"{task_id}: 소유 경로에 커밋하지 않은 변경이 있다: {', '.join(dirty)}. 리뷰는 커밋만 본다. "
                                f"워커가 먼저 커밋해야 한다. `orch fix {task_id} --note \"커밋 안 된 변경을 커밋\"` 으로 "
                                "되돌려 보낸다", 2)
            problem = plan_problem(p, cfg, files)
            if problem:
                reason = f"`{plan.PLAN_FILE}` 가 orch plan apply 검증을 통과하지 못한다:\n{problem}"
                verdict, source = auto_review(p, review_rel, task_id, rnd, reason), "orch"
            else:
                gate = testgate.gate_results(p, task_id, doc.role, rnd, testgate.code_head(p, task_id, commits))
                if gate and "quick" in gate and gate["quick"]["status"] not in testgate.PASSED:  # developer 만
                    verdict, source = auto_review(p, review_rel, task_id, rnd, gate_failure(task_id, gate["quick"])), "orch"
                else:
                    prompt = compose_prompt(p, cfg, doc, rnd, review_rel, commits, files, gate, st["history"])
                    (verdict, untracked), source = codex_review(p, cfg, doc, rnd, review_rel, prompt), "codex"
                    adjusted = align_verdict(p.root / review_rel, verdict)
                    if adjusted:
                        verdict = adjusted.split("→")[1]
        message = f"[{task_id}] review r{rnd}: {verdict}"
        if adjusted:
            message += f"\n\n판정 맞추기: {adjusted}(`## 지적 사항` 의 P1 줄과 `## 이전 지적 확인` 의 미해결 줄 수 기준)"
        if untracked:
            message += ("\n\n리뷰 중 새로 생긴 추적 안 된 파일(판정에는 영향 없음):\n"
                        + "\n".join(f"- {f}" for f in untracked))
        git_commit_paths(p.root, [review_rel], message)
    except BaseException as exc:
        st["status"] = "working"
        save_state(p, st)
        if isinstance(exc, (OSError, UnicodeDecodeError)):
            raise OrchError(f"리뷰 중 입출력 오류: {exc}") from exc
        raise
    st.setdefault("verdicts", {})[str(rnd)] = verdict
    st["status"] = "approved" if verdict == "APPROVE" else "changes"
    entry = {"stage": "review", "round": rnd, "at": now(), "verdict": verdict, "source": source,
             "head": commits[-1][0] if commits else None}
    result = {"id": task_id, "round": rnd, "verdict": verdict, "source": source, "review": review_rel}
    if gate:
        entry["tests"] = result["tests"] = {kind: rec["status"] for kind, rec in gate.items()}
    if adjusted:
        entry["verdict_adjusted"] = result["verdict_adjusted"] = adjusted
    if untracked:
        entry["untracked"] = result["untracked"] = untracked
    st["history"].append(entry)
    save_state(p, st)
    return result
