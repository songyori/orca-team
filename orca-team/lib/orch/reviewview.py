"""orch review-view <에이전트>: 리뷰 보기 pane. 그 고정 에이전트가 맡은 태스크의 Codex 리뷰 진행을 보여 주는 읽기 전용 창.

리뷰 실행은 관제의 orch review 자식 프로세스가 한다. 이 창이 죽어도 리뷰에는 영향이 없다."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from .config import Config
from .paths import OrchError, Project
from .tasks import all_states, load_state

POLL_SECONDS = 2.0


def runs_dir(p: Project) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "orca-team" / "runs" / p.root.name


def latest_run(p: Project, task_id: str, rnd: int) -> Path | None:
    found = sorted(runs_dir(p).glob(f"{task_id}-r{rnd}-*")) if runs_dir(p).is_dir() else []
    return found[-1] if found else None


class Viewer:
    def __init__(self, p: Project, cfg: Config, name: str, out=sys.stdout):
        self.p, self.cfg, self.name, self.out = p, cfg, name, out
        self.seen: tuple[str, int] | None = None
        self.run: Path | None = None
        self.offset = 0

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
        if self.seen and (st is None or (st["id"], int(st.get("round") or 1)) != self.seen):
            self.finish()
        if st is None:
            return
        key = (st["id"], int(st.get("round") or 1))
        if key != self.seen:
            launch = self.cfg.codex(st["role"])
            self.write(f"\n{key[0]} r{key[1]} 리뷰 중 ({launch.model}, {launch.effort})\n")
            self.seen, self.run, self.offset = key, None, 0
        run = latest_run(self.p, *key)
        if run != self.run:
            self.run, self.offset = run, 0
        log = run / "progress.log" if run else None
        if log and log.is_file():
            with log.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
            if chunk:
                self.write(chunk)

    def finish(self) -> None:
        task_id, rnd = self.seen
        st = load_state(self.p, task_id) or {}
        verdict = next((h.get("verdict") for h in reversed(st.get("history") or [])
                        if h.get("stage") == "review" and h.get("round") == rnd), None)
        self.write(f"{task_id} r{rnd} {verdict or '리뷰 끝(판정 없음)'} — docs/reviews/{task_id}/r{rnd}.md\n")
        self.seen, self.run, self.offset = None, None, 0

    def loop(self) -> None:
        agent = self.cfg.agents[self.name]
        self.write(f"리뷰 보기: {agent.title} 에이전트. 리뷰가 시작되면 Codex 진행 기록을 보여 준다.\n")
        while True:
            self.step()
            time.sleep(POLL_SECONDS)


def main(p: Project, cfg: Config, name: str) -> int:
    if name not in cfg.agents:
        raise OrchError(f"고정 에이전트가 아니다: {name}. 가능한 이름: {', '.join(cfg.agents) or '없음'}", 2)
    Viewer(p, cfg, name).loop()
    return 0
