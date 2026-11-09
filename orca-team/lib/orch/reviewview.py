"""orch review-view <에이전트>: 리뷰 보기 pane. 그 고정 에이전트가 맡은 태스크의 Codex 리뷰 진행을 보여 주는 읽기 전용 창.

리뷰 실행은 관제의 orch review 자식 프로세스가 한다. 이 창이 죽어도 리뷰에는 영향이 없다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .config import Config
from .paths import OrchError, Project
from .tasks import all_states, load_state, parse_task, task_path

POLL_SECONDS = 2.0
SLACK_SECONDS = 2.0


def runs_dir(p: Project) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "orca-team" / "runs" / p.root.name


def run_mtime(run: Path) -> float:
    """run dir 의 시각. dir 은 파일이 새로 생길 때만 바뀌므로 progress.log 의 쓰기 시각도 본다."""
    log = run / "progress.log"
    return max(run.stat().st_mtime, log.stat().st_mtime if log.is_file() else 0.0)


def latest_run(p: Project, task_id: str, rnd: int, since: float = 0.0) -> Path | None:
    base = runs_dir(p)
    if not base.is_dir():
        return None
    found = [d for d in sorted(base.glob(f"{task_id}-r{rnd}-*"))
             if d.is_dir() and run_mtime(d) >= since]
    return found[-1] if found else None


def round_of(st: dict) -> int:
    try:
        return int(st.get("round") or 1)
    except (TypeError, ValueError):
        return 1


class Viewer:
    def __init__(self, p: Project, cfg: Config, name: str, out=sys.stdout):
        self.p, self.cfg, self.name, self.out = p, cfg, name, out
        self.seen: tuple[str, int] | None = None
        self.run: Path | None = None
        self.offset = 0
        self.since = 0.0
        self.last_error: str | None = None

    def write(self, text: str) -> None:
        self.out.write(text)
        self.out.flush()

    def current(self) -> dict | None:
        for st in all_states(self.p):
            if st.get("agent") == self.name and st.get("status") == "reviewing":
                return st
        return None

    def step(self) -> None:
        st = self.current()
        if self.seen and (st is None or (st.get("id", ""), round_of(st)) != self.seen):
            self.finish()
        if st is None:
            return
        key = (st.get("id", ""), round_of(st))
        if key != self.seen:
            self.start(st, key)
        self.follow_run(latest_run(self.p, *key, since=self.since))
        self.drain()

    def follow_run(self, run: Path | None) -> None:
        """리뷰가 다시 시도하면 새 run dir(aN)로 넘어간다. 옛 dir 의 남은 기록을 먼저 다 찍는다."""
        if run is None or run == self.run:
            return
        if self.run is not None:
            self.drain()
            self.write(f"--- 다시 시도 ({run.name.rsplit('-', 1)[-1]}) ---\n")
        self.run, self.offset = run, 0

    def start(self, st: dict, key: tuple[str, int]) -> None:
        self.write(f"\n{key[0]} r{key[1]} 리뷰 중{self.model_note(st)}\n")
        self.seen = key
        self.since = time.time() - SLACK_SECONDS
        self.run, self.offset = None, 0

    def model_note(self, st: dict) -> str:
        """태스크 front matter 의 override 를 리뷰와 같이 적용한다. 실패하면 모델 없이 적는다."""
        front = {}
        spec = task_path(self.p, st.get("id", ""))
        try:
            if spec.is_file():
                front = parse_task(spec).front
        except (OrchError, OSError):
            front = {}
        try:
            launch = self.cfg.codex(st.get("role", ""), front)
        except OrchError:
            return ""
        return f" ({launch.model}, {launch.effort})"

    def drain(self) -> None:
        if self.run is None:
            return
        log = self.run / "progress.log"
        if not log.is_file():
            return
        with log.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
            self.offset = fh.tell()
        if chunk:
            self.write(chunk)

    def finish(self) -> None:
        task_id, rnd = self.seen
        if self.run is None:
            self.run = latest_run(self.p, task_id, rnd, since=self.since)
        self.drain()
        st = load_state(self.p, task_id) or {}
        verdict = next((h.get("verdict") for h in reversed(st.get("history") or [])
                        if h.get("stage") == "review" and h.get("round") == rnd), None)
        if verdict:
            line = f"{task_id} r{rnd} {verdict} — docs/reviews/{task_id}/r{rnd}.md"
        elif st.get("status") == "working":
            line = f"{task_id} r{rnd} 리뷰 중단"
        else:
            line = f"{task_id} r{rnd} 리뷰 끝(판정 없음) — docs/reviews/{task_id}/r{rnd}.md"
        self.write(line + "\n")
        self.seen, self.run, self.offset = None, None, 0

    def report_error(self, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        if msg != self.last_error:
            self.write(f"리뷰 보기 오류: {msg}\n")
            self.last_error = msg

    def loop(self) -> None:
        agent = self.cfg.agents[self.name]
        self.write(f"리뷰 보기: {agent.title} 에이전트. 리뷰가 시작되면 Codex 진행 기록을 보여 준다.\n")
        try:
            while True:
                try:
                    self.step()
                    self.last_error = None
                except Exception as exc:
                    self.report_error(exc)
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            return


def main(p: Project, cfg: Config, name: str) -> int:
    if name not in cfg.agents:
        raise OrchError(f"고정 에이전트가 아니다: {name}. 가능한 이름: {', '.join(cfg.agents) or '없음'}", 2)
    Viewer(p, cfg, name).loop()
    return 0
