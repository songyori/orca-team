"""config.toml 해석: 직무, 소유 경로, 모델 우선순위, 고정 에이전트([agents]), 병렬 상한, 사용자 승인 직무."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .paths import OrchError

DEFAULT_MAX_PARALLEL = 4
DEFAULT_APPROVAL_ROLES = ()  # 키가 없으면 0.2.0 흐름(승인 게이트 없음). 새 프로젝트는 템플릿이 ["designer"] 를 적는다


AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class Launch:
    model: str
    effort: str


@dataclass(frozen=True)
class Agent:
    """고정 에이전트([agents.<name>]). roles 의 직무 태스크를 한 세션이 이어 맡는다."""
    name: str
    title: str
    roles: tuple[str, ...]
    launch: Launch


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            cfg = cls(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
            raise OrchError(f"{path} 읽기 실패: {exc}", 2) from exc
        if "coordinator" in cfg.data:
            raise OrchError(f"{path}: [coordinator] 는 0.7.0 에서 없어졌다(코디네이터 대신 관제 orch drive). "
                            "`orch upgrade` 로 [drive]·[agents] 설정으로 옮긴다", 2)
        return cfg

    @property
    def roles(self) -> dict:
        return self.data.get("roles", {})

    def role(self, name: str) -> dict:
        if name not in self.roles:
            raise OrchError(f"알 수 없는 직무: {name}. 가능한 직무: {', '.join(sorted(self.roles))}", 2)
        return self.roles[name]

    def title(self, name: str) -> str:
        return str(self.role(name).get("title", name))

    def owns(self, name: str, override: list[str] | None = None) -> list[str]:
        owns = list(override or self.role(name).get("owns", []))
        if not owns:
            raise OrchError(f"{name}: owns 가 비어 있다(config.toml [roles.{name}].owns)", 2)
        return owns

    def claude(self, name: str, overrides: dict | None = None) -> Launch:
        return self._launch(name, "claude", overrides or {})

    def codex(self, name: str, overrides: dict | None = None) -> Launch:
        return self._launch(name, "codex", overrides or {})

    def _launch(self, name: str, kind: str, overrides: dict) -> Launch:
        role, defaults = self.role(name), self.data.get("defaults", {})
        values = []
        for field in ("model", "effort"):
            key = f"{kind}_{field}"
            value = overrides.get(key) or role.get(key) or defaults.get(key)
            if not value:
                raise OrchError(f"{name}: {key} 미설정(config.toml)", 2)
            values.append(str(value))
        return Launch(*values)

    @property
    def auto_up(self) -> bool:
        """[drive] auto_up. 메인 세션 훅이 죽은 관제(orch drive)를 자동으로 다시 띄울지. 없으면 true."""
        return self._flag("drive", "auto_up")

    @property
    def overflow(self) -> bool:
        """[overflow] enabled. 고정 에이전트가 바쁠 때 준비된 태스크를 임시 워커로 돌릴지. 없으면 true."""
        return self._flag("overflow", "enabled")

    def _flag(self, section: str, key: str) -> bool:
        value = self.data.get(section, {}).get(key, True)
        if not isinstance(value, bool):
            raise OrchError(f"config.toml [{section}].{key} 은 true 또는 false 여야 한다: {value!r}", 2)
        return value

    @property
    def agents(self) -> dict[str, Agent]:
        """[agents.<name>] 고정 에이전트. 한 직무는 한 에이전트에만, 직무는 [roles] 에 있어야 한다."""
        section = self.data.get("agents", {})
        if not isinstance(section, dict):
            raise OrchError("config.toml [agents] 는 표여야 한다", 2)
        defaults, found, owner = self.data.get("defaults", {}), {}, {}
        for name, body in section.items():
            if not AGENT_NAME_RE.match(name) or not isinstance(body, dict):
                raise OrchError(f"config.toml [agents.{name}] 형식이 틀렸다(이름은 소문자·숫자·-, 값은 표)", 2)
            roles = body.get("roles")
            if not isinstance(roles, list) or not roles or not all(isinstance(r, str) and r for r in roles):
                raise OrchError(f"config.toml [agents.{name}].roles 는 직무 이름 목록이다(값: {roles!r})", 2)
            unknown = [r for r in roles if r not in self.roles]
            if unknown:
                raise OrchError(f"config.toml [agents.{name}].roles 에 정의되지 않은 직무가 있다: {', '.join(unknown)}", 2)
            for role in roles:
                if role in owner:
                    raise OrchError(f"config.toml 직무 {role} 가 [agents.{owner[role]}] 와 [agents.{name}] 에 함께 있다", 2)
                owner[role] = name
            values = []
            for key in ("claude_model", "claude_effort"):
                value = body.get(key) or defaults.get(key)
                if not value:
                    raise OrchError(f"[agents.{name}]: {key} 미설정(config.toml)", 2)
                values.append(str(value))
            found[name] = Agent(name, str(body.get("title", name)), tuple(roles), Launch(*values))
        return found

    def agent_for(self, role: str) -> Agent | None:
        return next((agent for agent in self.agents.values() if role in agent.roles), None)

    @property
    def max_rounds(self) -> int:
        try:
            return int(self.data.get("review", {}).get("max_rounds", 3))
        except (ValueError, TypeError) as exc:
            raise OrchError(f"config.toml [review].max_rounds 가 정수가 아니다: {exc}", 2) from exc

    @property
    def max_parallel(self) -> int:
        """orch start-ready 가 동시에 띄워 두는 working 태스크 수(최상위 max_parallel). 없으면 4."""
        value = self.data.get("max_parallel", DEFAULT_MAX_PARALLEL)
        try:
            number = int(value)
        except (ValueError, TypeError) as exc:
            raise OrchError(f"config.toml max_parallel 이 정수가 아니다: {exc}", 2) from exc
        if isinstance(value, bool) or number < 1:
            raise OrchError(f"config.toml max_parallel 은 1 이상의 정수다(값: {value!r})", 2)
        return number

    @property
    def user_approval_roles(self) -> list[str]:
        """Codex APPROVE 뒤 사용자 승인을 한 번 더 받는 직무(최상위 user_approval_roles).

        없으면 [](0.2.0 처럼 승인 게이트 없음). 새 프로젝트는 templates/config.toml 이 ["designer"] 를 적는다.
        [roles] 에 없는 직무는 exit 2 다(오타로 게이트가 조용히 꺼지지 않게)."""
        value = self.data.get("user_approval_roles", list(DEFAULT_APPROVAL_ROLES))
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise OrchError(f"config.toml user_approval_roles 는 직무 이름 목록이다(값: {value!r})", 2)
        names = [v.strip() for v in value]
        unknown = [name for name in names if name not in self.roles]
        if unknown:
            raise OrchError(f"config.toml user_approval_roles 에 정의되지 않은 직무가 있다: {', '.join(unknown)}. "
                            f"가능한 직무: {', '.join(sorted(self.roles))}", 2)
        return names
