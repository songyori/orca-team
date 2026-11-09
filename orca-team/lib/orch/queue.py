"""메인 → drive 요청 큐(.orchestration/state/drive/queue/).

메인이 Orca 를 부르는 명령을 실행하면 요청 파일(<번호>.json)을 쓰고 결과 파일(<번호>.result.json)을 기다린다.
drive 만 요청을 실행한다(메인 터미널에서 worker-start 를 부르면 Orca 가 메인에 Run 을 묶는다). 기다리다 그만둔 요청은
<번호>.abandoned 표시를 남기고(신호로 끝날 때도), drive 는 그 결과를 outbox `result` 로 보낸다."""
from __future__ import annotations

import time
from pathlib import Path

from . import comm
from .paths import OrchError, Project
from .util import drive_alive, now, pid_alive, read_json, write_json

WAIT_SECONDS = 90.0  # Bash 도구 기본 제한(120초)보다 짧게 둔다. Bash 가 먼저 끝내면 그만둔 표시를 남기지 못한다
POLL_SECONDS = 0.2
NAME_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9].json"


class DriveRefused(OrchError):
    """관제가 요청을 실행했지만 실패했다(관제가 꺼져 있어 요청을 넣지 못한 것과 다르다)."""


def down_text(p: Project) -> str:
    """관제가 살아 있지 않을 때의 거부 문구. 프로세스는 살아 있는데 heartbeat 만 멈췄으면(up 은 hung) 꺼진 것과 다르게 알린다."""
    if pid_alive(p.drive_pid):
        return ("관제(drive)가 멈춘 것 같다(프로세스는 살아 있는데 heartbeat 가 끊겼다). 긴 작업 중일 수 있으니 잠시 뒤 다시 한다. "
                "계속되면 사용자에게 관제 탭을 확인해 달라고 한다(관제를 다시 띄우는 명령은 이 상태를 고치지 않는다)")
    return "관제(drive)가 꺼져 있다. 메인에서 /orca-team:up 으로 살린 뒤 다시 한다"


def _paths(p: Project, number: str) -> tuple[Path, Path, Path]:
    return (p.queue_dir / f"{number}.json", p.queue_dir / f"{number}.result.json", p.queue_dir / f"{number}.abandoned")


def _next_number(p: Project) -> str:
    numbers = [int(f.name[:6]) for f in p.queue_dir.iterdir() if f.name[:6].isdigit()]
    return f"{max(numbers, default=0) + 1:06d}"


def submit(p: Project, command: str, args: dict, wait: float = WAIT_SECONDS) -> dict:
    """요청을 넣고 결과를 기다린다. drive 가 살아 있지 않으면 exit 1. 실패 결과는 그 코드로 DriveRefused.
    wait 초 안에 끝나지 않으면 {"status": "queued"} 를 돌려주고 결과는 outbox `result` 로 온다.
    기다리다 신호(SIGTERM·SIGHUP·SIGINT)로 끝나도 그만둔 표시를 남긴다(_abandon)."""
    if not drive_alive(p):
        raise OrchError(down_text(p), 1)
    p.queue_dir.mkdir(parents=True, exist_ok=True)
    with comm._file_lock(p.drive_dir / "queue.lock"):  # 잠금 파일은 큐 폴더 밖에 둔다(큐 폴더는 요청만 담는다)
        number = _next_number(p)
        request_path, result_path, abandoned = _paths(p, number)
        request = {"id": f"q-{number}", "command": command, "args": args, "at": now()}
        write_json(request_path, request)
    deadline = time.monotonic() + wait
    try:
        with comm._sigterm_exits():
            while True:
                if result_path.exists():
                    return _take(result_path)
                if time.monotonic() >= deadline:
                    break
                time.sleep(POLL_SECONDS)
    except (SystemExit, KeyboardInterrupt):
        _abandon(p, number, request)
        raise
    return _finish_wait(p, number)


def _abandon(p: Project, number: str, request: dict) -> None:
    """신호로 기다림을 그만둔다. 결과가 이미 왔으면 버리지 않고 outbox `result` 로 남기고, 아니면 그만둔 표시를 남긴다."""
    _, result_path, abandoned = _paths(p, number)
    with comm._file_lock(p.drive_dir / "queue.lock"):
        if result_path.exists():
            result = read_json(result_path, {})
            result_path.unlink(missing_ok=True)
            _post_result(p, request, result)
        else:
            abandoned.touch()


def _finish_wait(p: Project, number: str) -> dict:
    """기다림이 끝난 뒤 결과를 정한다. 잠금 안에서 결과 파일이 있으면 가져가고, 없으면 그만둔 표시를 남긴다.
    complete 도 같은 잠금을 쓰므로 '표시' 와 '결과 파일' 중 한쪽만 일어난다."""
    _, result_path, abandoned = _paths(p, number)
    with comm._file_lock(p.drive_dir / "queue.lock"):
        if result_path.exists():
            return _take(result_path)
        abandoned.touch()
    return {"status": "queued", "request": f"q-{number}",
            "message": "관제가 처리 중이다. 결과는 orch watch 의 result 메시지로 온다"}


def _take(result_path: Path) -> dict:
    data = read_json(result_path, {})
    result_path.unlink(missing_ok=True)
    if not data.get("ok"):
        raise DriveRefused(str(data.get("error") or "관제가 요청을 처리하지 못했다"), int(data.get("code") or 1))
    return data.get("result") if isinstance(data.get("result"), dict) else {"result": data.get("result")}


def pending(p: Project) -> list[Path]:
    """drive 가 실행할 요청 파일. 넣은 순서."""
    if not p.queue_dir.is_dir():
        return []
    return sorted(p.queue_dir.glob(NAME_GLOB))


def complete(p: Project, path: Path, request: dict, result: dict) -> None:
    """요청을 끝낸다. 잠금 안에서 그만둔 표시가 있으면 outbox `result` 를 보내고 표시를 지운다. 없으면 결과 파일을 쓴다.
    _finish_wait 와 같은 잠금을 쓰므로 한 요청의 결과는 한 번만 전달된다."""
    number = path.name[:6]
    _, result_path, abandoned = _paths(p, number)
    with comm._file_lock(p.drive_dir / "queue.lock"):
        if abandoned.exists():
            _post_result(p, request, result)
            abandoned.unlink(missing_ok=True)
        else:
            write_json(result_path, result)
    path.unlink(missing_ok=True)


def _post_result(p: Project, request: dict, result: dict) -> None:
    status = "완료" if result.get("ok") else f"실패: {result.get('error')}"
    comm.post(p, "result", f"{request.get('command')} {request.get('args', {}).get('id', '')} — {status}".strip(),
              {"request": request.get("id"), "command": request.get("command"), "result": result})
