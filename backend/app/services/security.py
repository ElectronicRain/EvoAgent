from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import RuntimeSecurityConfig
from .common import dumps, loads


SECURITY_PROFILES: dict[str, dict[str, Any]] = {
    "workspace": {"filesystem_mode": "workspace"},
    "custom": {"filesystem_mode": "custom"},
    "unrestricted": {"filesystem_mode": "unrestricted"},
    "read_only": {"read_only": True, "command_mode": "deny"},
    "workspace_ask": {"filesystem_mode": "workspace", "command_mode": "always_ask"},
    "workspace_auto": {"filesystem_mode": "workspace", "command_mode": "auto"},
    "custom_ask": {"filesystem_mode": "custom", "command_mode": "always_ask"},
    "custom_auto": {"filesystem_mode": "custom", "command_mode": "auto"},
    "unrestricted_ask": {
        "filesystem_mode": "unrestricted",
        "command_mode": "always_ask",
    },
    "unrestricted_auto": {
        "filesystem_mode": "unrestricted",
        "command_mode": "auto",
    },
}


@dataclass
class RuntimeSecurityContext:
    profile: str
    filesystem_mode: str
    roots: list[str]
    command_mode: str
    block_critical_commands: bool
    read_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeSecurityService:
    @staticmethod
    def _normalize_roots(values: list[str]) -> list[str]:
        roots: list[str] = []
        for value in values:
            raw = str(value).strip().strip('"')
            if not raw:
                continue
            path = Path(raw).expanduser().resolve()
            text = str(path)
            if text not in roots:
                roots.append(text)
        if not roots:
            roots.append(str(settings.workspace_root.resolve()))
        return roots

    async def get_or_create(self, db: AsyncSession) -> RuntimeSecurityConfig:
        item = await db.get(RuntimeSecurityConfig, "default")
        if item:
            return item
        item = RuntimeSecurityConfig(
            id="default",
            filesystem_mode="workspace",
            workspace_roots_json=dumps([str(settings.workspace_root.resolve())]),
            command_mode="risk_based",
            block_critical_commands=True,
        )
        db.add(item)
        await db.flush()
        return item

    async def update(
        self,
        db: AsyncSession,
        *,
        filesystem_mode: str,
        workspace_roots: list[str],
        command_mode: str,
        block_critical_commands: bool,
    ) -> RuntimeSecurityConfig:
        item = await self.get_or_create(db)
        roots = self._normalize_roots(workspace_roots)
        for root in roots:
            Path(root).mkdir(parents=True, exist_ok=True)
        item.filesystem_mode = filesystem_mode
        item.workspace_roots_json = dumps(roots)
        item.command_mode = command_mode
        item.block_critical_commands = block_critical_commands
        await db.flush()
        return item

    async def resolve(
        self, db: AsyncSession, profile: str = "default"
    ) -> RuntimeSecurityContext:
        item = await self.get_or_create(db)
        configured_roots = self._normalize_roots(loads(item.workspace_roots_json, []))
        override = SECURITY_PROFILES.get(profile, {}) if profile != "default" else {}
        filesystem_mode = str(override.get("filesystem_mode", item.filesystem_mode))
        if filesystem_mode == "workspace":
            roots = [str(settings.workspace_root.resolve())]
        else:
            roots = configured_roots
        return RuntimeSecurityContext(
            profile=profile,
            filesystem_mode=filesystem_mode,
            roots=roots,
            command_mode=str(override.get("command_mode", item.command_mode)),
            block_critical_commands=item.block_critical_commands,
            read_only=bool(override.get("read_only", False)),
        )

    async def response(self, db: AsyncSession) -> dict[str, Any]:
        item = await self.get_or_create(db)
        roots = self._normalize_roots(loads(item.workspace_roots_json, []))
        return {
            "id": item.id,
            "filesystem_mode": item.filesystem_mode,
            "workspace_roots": roots,
            "application_workspace": str(settings.workspace_root.resolve()),
            "command_mode": item.command_mode,
            "block_critical_commands": item.block_critical_commands,
            "updated_at": item.updated_at,
            "profiles": ["default", *SECURITY_PROFILES],
        }


runtime_security_service = RuntimeSecurityService()
