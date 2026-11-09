"""메인 → drive 요청 큐(.orchestration/state/drive/queue/).

메인이 Orca 를 부르는 명령을 실행하면 요청 파일(<번호>.json)을 쓰고 결과 파일(<번호>.result.json)을 기다린다.
drive 만 요청을 실행한다(메인 터미널에서 worker-start 를 부르면 Orca 가 메인에 Run 을 묶는다). 기다리다 그만둔 요청은
<번호>.abandoned 표시를 남기고, drive 는 그 결과를 outbox `result` 로 보낸다."""
from __future__ import annotations

import time
from pathlib import Path

from . import comm
from .paths import OrchError, Project
from .util import drive_alive, now, read_json, write_json

WAIT_SECONDS = 120.0
POLL_SECONDS = 0.2
NAME_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9].json"


def _paths(p: Project, number: str) -> tuple[Path, Path, Path]:
    return (p.queue_dir / f"{number}.json", p.queue_dir / f"{number}.result.json", p.queue_dir / f"{number}.abandoned")


def _next_number(p: Project) -> str:
    numbers = [int(f.name[:6]) for f in p.queue_dir.iterdir() if f.name[:6].isdigit()]
    return f"{max(numbers, default=0) + 1:06d}"


def submit(p: Project, command: str, args: dict, wait: float = WAIT_SECONDS) -> dict:
    """요청을 넣고 결과를 기다린다. drive 가 꺼져 있으면 exit 1. 실패 결과는 그 코드로 OrchError.
    wait 초 안에 끝나지 않으면 {"status": "queued"} 를 돌려주고 결과는 outbox `result` 로 온다."""
    if not drive_alive(p):
        raise OrchError("관제(drive)가 꺼져 있다. 메인에서 /orca-team:up 으로 살린 뒤 다시 한다", 1)
    p.queue_dir.mkdir(parents=True, exist_ok=True)
    with comm._file_lock(p.drive_dir / "queue.lock"):  # 잠금 파일은 큐 폴더 밖에 둔다(큐 폴더는 요청만 담는다)
        number = _next_number(p)
        request_path, result_path, abandoned = _paths(p, number)
        write_json(request_path, {"id": f"q-{number}", "command": command, "args": args, "at": now()})
    deadline = time.monotonic() + wait
    while True:
        if result_path.exists():
            return _take(result_path)
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    abandoned.touch()
    if result_path.exists():  # 표시를 남기는 사이에 끝났다
        abandoned.unlink(missing_ok=True)
        return _take(result_path)
    return {"status": "queued", "request": f"q-{number}",
            "message": "관제가 처리 중이다. 결과는 orch watch 의 result 메시지로 온다"}


def _take(result_path: Path) -> dict:
    data = read_json(result_path, {})
    result_path.unlink(missing_ok=True)
    if not data.get("ok"):
        raise OrchError(str(data.get("error") or "관제가 요청을 처리하지 못했다"), int(data.get("code") or 1))
    return data.get("result") if isinstance(data.get("result"), dict) else {"result": data.get("result")}


def pending(p: Project) -> list[Path]:
    """drive 가 실행할 요청 파일. 넣은 순서."""
    if not p.queue_dir.is_dir():
        return []
    return sorted(p.queue_dir.glob(NAME_GLOB))


def complete(p: Project, path: Path, request: dict, result: dict) -> None:
    """요청을 끝낸다. 요청한 쪽이 기다리면 결과 파일을, 그만뒀으면 outbox `result` 를 쓴다."""
    number = path.name[:6]
    _, result_path, abandoned = _paths(p, number)
    if abandoned.exists():
        _post_result(p, request, result)
        abandoned.unlink(missing_ok=True)
    else:
        write_json(result_path, result)
        if abandoned.exists() and result_path.exists():  # 결과를 쓰는 사이에 그만뒀다
            result_path.unlink(missing_ok=True)
            _post_result(p, request, result)
            abandoned.unlink(missing_ok=True)
    path.unlink(missing_ok=True)


def _post_result(p: Project, request: dict, result: dict) -> None:
    status = "완료" if result.get("ok") else f"실패: {result.get('error')}"
    comm.post(p, "result", f"{request.get('command')} {request.get('args', {}).get('id', '')} — {status}".strip(),
              {"request": request.get("id"), "command": request.get("command"), "result": result})
