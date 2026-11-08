#!/usr/bin/env python3
"""가짜 Codex 래퍼(scripts/codex-run.sh). --bypass-hook-trust 를 받는다.

argv 를 $FAKE_DIR/codex-run.log 에 남기고 $FAKE_DIR/codex_run_modes.txt 의 첫 단어(없으면 approve)대로 행동한다.
모드: approve | changes | noverdict | fail | modify | touch_other | commit | other_commit | rewrite | evil_merge
      | pycache | edit_tracked | start_other | mv_staged | rename_commit | body
body: $FAKE_DIR/review_body.md 를 리뷰 파일로 그대로 쓴다(판정 줄 포함).
리뷰 파일 경로는 프롬프트의 `REVIEW_FILE: <경로>` 줄에서 읽는다.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

fake = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with (fake / "codex-run.log").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\n")


def opt(name):
    return args[args.index(name) + 1]


modes_file = fake / "codex_run_modes.txt"
modes = modes_file.read_text(encoding="utf-8").split() if modes_file.exists() else []
mode = modes.pop(0) if modes else "approve"
modes_file.write_text("".join(m + "\n" for m in modes), encoding="utf-8")

cwd, run_dir = Path(opt("--cwd")), Path(opt("--run-dir"))
prompt = Path(opt("--prompt-file")).read_text(encoding="utf-8")
review = cwd / re.search(r"^REVIEW_FILE: (\S+)$", prompt, re.M).group(1)
tasks_state = cwd / ".orchestration" / "state" / "tasks"
(fake / "review_pids_seen.json").write_text(json.dumps(sorted(
    str(pid.relative_to(tasks_state)) for pid in tasks_state.glob("*/review.pid"))), encoding="utf-8")
status = {"exit": 0, "terminal_event": "turn.completed"}
if mode == "fail":
    status = {"exit": 11, "terminal_event": None}
else:
    verdict = {"approve": "APPROVE", "changes": "REQUEST_CHANGES", "modify": "APPROVE",
               "touch_other": "APPROVE", "commit": "APPROVE", "other_commit": "APPROVE", "rewrite": "APPROVE", "evil_merge": "APPROVE",
               "pycache": "APPROVE", "edit_tracked": "APPROVE", "start_other": "APPROVE", "mv_staged": "APPROVE",
               "rename_commit": "APPROVE"}.get(mode)
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# 리뷰\n\n없음\n" + (f"\nVERDICT: {verdict}\n" if verdict else ""), encoding="utf-8")
    if mode == "body":
        review.write_text((fake / "review_body.md").read_text(encoding="utf-8"), encoding="utf-8")
    if mode == "modify":
        (cwd / "src").mkdir(exist_ok=True)
        (cwd / "src" / "codex-touched.txt").write_text("x\n", encoding="utf-8")
    if mode == "touch_other":
        (cwd / "tests").mkdir(exist_ok=True)
        (cwd / "tests" / "concurrent.txt").write_text("x\n", encoding="utf-8")
    if mode == "pycache":  # 테스트 실행이 남기는 추적 안 된 산출물
        (cwd / "__pycache__").mkdir(exist_ok=True)
        (cwd / "__pycache__" / "x.pyc").write_bytes(b"\x00pyc")
    if mode == "edit_tracked":
        with (cwd / "src" / "login.ts").open("a", encoding="utf-8") as fh:
            fh.write("// codex\n")
    if mode == "start_other":  # 리뷰 도중 코디네이터가 tests/** 를 가진 T-002 를 시작하고 그 워커가 파일을 고친다
        state = cwd / ".orchestration" / "state" / "tasks" / "T-002.json"
        state.write_text(json.dumps({"id": "T-002", "role": "tester", "title": "x", "status": "working",
                                     "round": 1, "owns": ["tests/**"], "history": []}), encoding="utf-8")
        with (cwd / "tests" / "a.txt").open("a", encoding="utf-8") as fh:
            fh.write("concurrent\n")
    def git(*a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)
    if mode in ("mv_staged", "rename_commit"):
        (cwd / "tests").mkdir(exist_ok=True)
        git("mv", "src/login.ts", "tests/login.ts")
        if mode == "rename_commit":
            git("commit", "-q", "-m", "move")
    if mode in ("commit", "other_commit"):
        sub, name = ("src", "sneaky.txt") if mode == "commit" else ("tests", "other-task.txt")
        (cwd / sub).mkdir(exist_ok=True)
        (cwd / sub / name).write_text("x\n", encoding="utf-8")
        git("add", "--", f"{sub}/{name}")
        git("commit", "-q", "-m", "concurrent commit")
    if mode == "evil_merge":
        git("checkout", "-q", "-b", "side")
        git("commit", "-q", "--allow-empty", "-m", "side")
        git("checkout", "-q", "-")
        git("commit", "-q", "--allow-empty", "-m", "main")
        git("merge", "-q", "--no-ff", "--no-commit", "side")
        (cwd / "src").mkdir(exist_ok=True)
        (cwd / "src" / "evil.txt").write_text("x\n", encoding="utf-8")
        git("add", "--", "src/evil.txt")
        git("commit", "-q", "-m", "merge")
    if mode == "rewrite":
        git("commit", "-q", "--amend", "--allow-empty", "-m", "rewritten")
(run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
sys.exit(status["exit"])
