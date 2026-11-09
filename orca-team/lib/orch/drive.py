"""관제(orch drive). 관제 탭의 셸에서 돌며 Run 을 갖는다.

메인이 넣은 요청(queue.py)을 실행하고, Orca 메일·리뷰 자식 프로세스를 돌리며 정해진 절차를 실행한다. 판단이 필요한
일은 outbox decide 로 메인에 올린다(Task 11). 상태는 모두 파일에 있어 다시 띄우면 이어서 한다."""
from __future__ import annotations

import os
import time

from . import agents, orca, plan, queue, setup, tasks
from .config import Config
from .paths import OrchError, Project
from .util import acquire_pid, now, read_json, release_pid, update_run, write_text_atomic

WAIT_MS = 10000  # 메일을 한 번에 기다리는 시간. 메인 요청 큐도 이 간격으로 본다
IDLE_SECONDS = 1.0


class Drive:
    def __init__(self, p: Project, cfg: Config):
        self.p, self.cfg = p, cfg

    def log(self, text: str) -> None:
        line = f"{now()} {text}"
        print(line, flush=True)
        try:
            self.p.drive_dir.mkdir(parents=True, exist_ok=True)
            with self.p.drive_log.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def beat(self) -> None:
        write_text_atomic(self.p.drive_heartbeat, now())

    def bind(self) -> dict:
        return setup.bind(self.p)

    def run_queue(self) -> int:
        """메인 요청을 넣은 순서로 실행한다. 실행한 수."""
        done = 0
        for path in queue.pending(self.p):
            request = read_json(path, {})
            try:
                result = {"ok": True, "result": self.execute(request)}
            except OrchError as exc:
                if exc.code == 12:
                    raise
                result = {"ok": False, "error": str(exc), "code": exc.code}
            queue.complete(self.p, path, request, result)
            self.log(f"요청 {request.get('id')} {request.get('command')}: {'완료' if result['ok'] else result['error']}")
            done += 1
        return done

    def execute(self, request: dict) -> dict:
        command, a = request.get("command"), request.get("args") or {}
        p, cfg = self.p, self.cfg
        if command == "start":
            return tasks.start(p, cfg, a["id"], bool(a.get("retry")), a.get("overrides") or {})
        if command == "start-ready":
            return plan.start_ready(p, cfg)
        if command == "fix":
            return tasks.fix(p, cfg, a["id"], a.get("note"), bool(a.get("retry")), a.get("overrides") or {},
                             bool(a.get("force")), a.get("supplement"), bool(a.get("extra_round")))
        if command == "accept":
            return self.accepted(a["id"], tasks.accept(p, a["id"]))
        if command == "drop":
            return tasks.drop(p, a["id"], a["reason"], bool(a.get("force")))
        if command == "reply":
            orca.call(["orchestration", "reply", "--id", str(a["message_id"]), f"--body={a['body']}"])
            return {"replied": a["message_id"]}
        if command == "design-decide":
            return plan.design_decide(p, cfg, a["id"], list(a.get("approve") or []), dict(a.get("fixes") or {}))
        if command == "scope":
            return tasks.scope(p, a["id"], a["body"])
        if command == "agent-reset":
            return agents.request_reset(p, cfg, a["name"])
        raise OrchError(f"알 수 없는 관제 요청: {command}", 2)

    def accepted(self, task_id: str, result: dict) -> dict:
        """accept 뒤 처리. Task 11 에서 기획 적용·묶음 후속·디자인 흐름을 붙인다."""
        return result

    def tick(self, wait_ms: int = WAIT_MS) -> None:
        self.beat()
        self.run_queue()

    def run_forever(self) -> None:
        while True:
            self.tick()
            time.sleep(IDLE_SECONDS)


def main(p: Project, cfg: Config) -> int:
    """관제 탭 셸에서 `orch drive` 로 띄운다. 하나만 돈다(state/drive/drive.pid)."""
    if os.environ.get("CLAUDECODE"):
        raise OrchError("orch drive 는 관제 탭 셸에서만 돈다. Claude 세션 안에서는 띄우지 않는다(메인은 /orca-team:up)", 2)
    handle = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not handle:
        raise OrchError("ORCA_TERMINAL_HANDLE 이 없다. orch drive 는 Orca 터미널(관제 탭)에서 돈다", 2)
    p.ensure_state()
    p.drive_dir.mkdir(parents=True, exist_ok=True)
    acquire_pid(p.drive_pid, "orch drive")
    try:
        update_run(p, lambda r: (r.update(drive_handle=handle), r.pop("launched_at", None)))
        drive = Drive(p, cfg)
        drive.beat()
        drive.log(f"관제 시작({handle}): {drive.bind()['status']}")
        drive.run_forever()
    finally:
        release_pid(p.drive_pid)
    return 0
