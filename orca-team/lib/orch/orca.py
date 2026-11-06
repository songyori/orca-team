"""orca CLI 호출. 모든 호출은 --json 이다."""
from __future__ import annotations

import json
import subprocess

from .paths import OrchError, orca_bin


def last_json(text: str) -> dict | None:
    """여러 JSON 객체가 이어진 출력에서 `_keepalive` 가 아닌 마지막 객체."""
    decoder, index, last = json.JSONDecoder(), 0, None
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            obj, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return last
        if isinstance(obj, dict) and not obj.get("_keepalive"):
            last = obj
    return last


def call(args: list[str], check: bool = True) -> tuple[int, dict]:
    cmd = [orca_bin(), *args, "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise OrchError(f"orca 실행 파일 없음: {cmd[0]}", 12) from exc
    data = last_json(proc.stdout)
    label = " ".join(args[:2])
    if data is None:
        raise OrchError(f"orca {label} 출력 파싱 실패(exit {proc.returncode}): {(proc.stdout + proc.stderr)[-800:]}")
    if check and (proc.returncode != 0 or not data.get("ok", False)):
        raise OrchError(f"orca {label} 실패(exit {proc.returncode}): {json.dumps(data, ensure_ascii=False)[:1500]}")
    return proc.returncode, data


def find_key(node, names: tuple[str, ...]):
    """응답 JSON 에서 names 중 처음 맞는 키의 값을 깊이 우선으로 찾는다."""
    if isinstance(node, dict):
        for name in names:
            if isinstance(node.get(name), (str, int)):
                return node[name]
        for value in node.values():
            found = find_key(value, names)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_key(item, names)
            if found is not None:
                return found
    return None


def _terminal_verdict(code: int, data: dict) -> str:
    """`orca terminal show` 응답 → "alive" | "dead" | "unknown".

    dead 는 닫힌 핸들(terminal_handle_stale, Orca 재시작 뒤 옛 핸들 포함)이거나, 터미널은 있는데 연결이 끊겼거나
    claude 가 끝나 셸만 남은 경우다. Orca 1.4.240 의 orphaned 는 렌더러 pane 마운트 여부일 뿐 생존과 무관해 보지 않는다."""
    if code == 0 and data.get("ok"):
        term = (data.get("result") or {}).get("terminal")
        if not isinstance(term, dict) or not term:
            return "unknown"
        return "alive" if term.get("connected") is True and term.get("agentIdentity") == "claude" else "dead"
    error = data.get("error")
    if isinstance(error, dict) and error.get("code") == "terminal_handle_stale":
        return "dead"
    return "unknown"


def terminal_state(handle: str | None) -> str:
    """코디네이터 자동 부활용 생존 판정. 예외를 내지 않는다(orca 없음·파싱 실패도 unknown)."""
    if not handle:
        return "unknown"
    try:
        code, data = call(["terminal", "show", "--terminal", handle], check=False)
    except OrchError:
        return "unknown"
    return _terminal_verdict(code, data)


def terminal_alive(handle: str | None) -> bool:
    """터미널이 연결돼 있고 그 안에서 claude 에이전트가 도는지. 코드 12(orca 없음)는 전파한다."""
    if not handle:
        return False
    try:
        code, data = call(["terminal", "show", "--terminal", handle], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return False
    return _terminal_verdict(code, data) == "alive"


def worker_info(dispatch_id: str) -> dict | None:
    """worker-show 요약 {handle, verdict, stage, state}. 조회 실패면 None. 코드 12(orca 없음)는 전파."""
    try:
        code, data = call(["orchestration", "worker-show", "--dispatch", dispatch_id], check=False)
    except OrchError as exc:
        if exc.code == 12:
            raise
        return None
    if code != 0 or not data.get("ok"):
        return None
    result = data.get("result") or {}
    worker = result.get("worker") or {}
    return {"handle": worker.get("agentTerminalHandle"),
            "verdict": ((result.get("projection") or {}).get("liveness") or {}).get("verdict"),
            "stage": worker.get("stage"), "state": worker.get("state")}


def worker_terminal(dispatch_id: str) -> tuple[str | None, str | None]:
    """(에이전트 터미널 handle, liveness verdict). 조회 실패면 (None, None)."""
    info = worker_info(dispatch_id)
    return (None, None) if info is None else (info["handle"], info["verdict"])
