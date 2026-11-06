"""config.toml 해석: 직무, 소유 경로, 모델 우선순위(overrides > 직무 > defaults), 병렬 상한, 사용자 승인 직무."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .paths import OrchError

DEFAULT_MAX_PARALLEL = 4
DEFAULT_APPROVAL_ROLES = ()  # 키가 없으면 0.2.0 흐름(승인 게이트 없음). 새 프로젝트는 템플릿이 ["designer"] 를 적는다


@dataclass(frozen=True)
class Launch:
    model: str
    effort: str


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            return cls(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:  # TOMLDecodeError·UnicodeDecodeError 는 ValueError 하위
            raise OrchError(f"{path} 읽기 실패: {exc}", 2) from exc

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

    def coordinator(self) -> Launch:
        section = self.data.get("coordinator", {})
        return Launch(str(section.get("model", "opus")), str(section.get("effort", "xhigh")))

    @property
    def auto_up(self) -> bool:
        """[coordinator] auto_up. 메인 세션 훅이 죽은 코디네이터를 자동으로 다시 띄울지. 없으면 true."""
        value = self.data.get("coordinator", {}).get("auto_up", True)
        if not isinstance(value, bool):
            raise OrchError(f"config.toml [coordinator].auto_up 은 true 또는 false 여야 한다: {value!r}", 2)
        return value

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
