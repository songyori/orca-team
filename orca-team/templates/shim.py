#!/usr/bin/env python3
"""orca-team shim. orca-team 플러그인의 bin/orch 를 실행한다(플러그인 갱신이 바로 반영된다).

플러그인 위치 우선순위: ORCA_TEAM_PLUGIN_ROOT > .orchestration/state/plugin-root(등록된 경로일 때만) > installed_plugins.json."""
import json
import os
import sys
from pathlib import Path


def real(path):
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return None


def directory_sources():
    """directory 소스 마켓플레이스의 <path>/orca-team 목록. Claude Code 는 이 폴더에서 스킬·훅을 읽는다."""
    known = Path(os.environ.get("ORCA_TEAM_KNOWN_MARKETPLACES_JSON")
                 or Path.home() / ".claude" / "plugins" / "known_marketplaces.json")
    found = []
    try:
        for entry in json.loads(known.read_text(encoding="utf-8")).values():
            source = entry.get("source") if isinstance(entry, dict) else None
            if isinstance(source, dict) and source.get("source") == "directory" and source.get("path"):
                found.append(Path(source["path"], "orca-team"))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return found


def trusted_roots():
    """state/plugin-root 가 가리켜도 되는 경로: installed_plugins.json 의 installPath, directory 소스 마켓플레이스의 <path>/orca-team."""
    roots = set()
    registry = Path(os.environ.get("ORCA_TEAM_PLUGINS_JSON")
                    or Path.home() / ".claude" / "plugins" / "installed_plugins.json")
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
        for key, entries in data.get("plugins", data).items():
            if key.split("@", 1)[0] == "orca-team" and isinstance(entries, list):
                roots.update(real(e["installPath"]) for e in entries if isinstance(e, dict) and e.get("installPath"))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    roots.update(real(path) for path in directory_sources())
    roots.discard(None)
    return roots


def plugin_root():
    override = os.environ.get("ORCA_TEAM_PLUGIN_ROOT")
    if override:
        return Path(override)
    # 세션 시작 훅이 적어 둔 플러그인 루트. 훅·스킬이 실제로 쓰는 코드와 같은 곳을 가리킨다(shim 은 .orchestration/bin/ 에 있다).
    # 저장소 안 파일이 실행 코드를 바꾸지 못하게 설치·마켓플레이스 등록 경로와 일치할 때만 믿는다.
    marker = Path(os.path.abspath(__file__)).parent.parent / "state" / "plugin-root"
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        recorded = ""
    if recorded and Path(recorded, "bin", "orch").is_file() and real(recorded) in trusted_roots():
        return Path(recorded)
    for path in directory_sources():  # 디렉터리 소스는 installPath(오래된 캐시)보다 앞선다
        if (path / "bin" / "orch").is_file():
            return path
    registry = Path(os.environ.get("ORCA_TEAM_PLUGINS_JSON")
                    or Path.home() / ".claude" / "plugins" / "installed_plugins.json")
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key, entries in data.get("plugins", data).items():
        if key.split("@", 1)[0] != "orca-team" or not isinstance(entries, list):
            continue
        for entry in sorted(entries, key=lambda e: e.get("scope") != "user"):
            path = entry.get("installPath")
            if path and Path(path, "bin", "orch").is_file():
                return Path(path)
    return None


root = plugin_root()
if root is None:
    sys.stderr.write("orca-team 플러그인이 설치돼 있지 않다: /plugin install orca-team@setup-with-orca\n")
    sys.exit(12)
os.execv(sys.executable, [sys.executable, str(root / "bin" / "orch"), *sys.argv[1:]])
