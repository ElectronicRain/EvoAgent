from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


async def audit(
    db: AsyncSession,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    success: bool = True,
    actor: str = "local-user",
    module: str | None = None,
    duration_ms: int = 0,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail_json=dumps(detail or {}),
            success=success,
        )
    )
    # Telemetry is a privacy-filtered local outbox. A failure here must never
    # break the user's primary operation or the immutable local audit record.
    try:
        from .telemetry import telemetry_service

        await telemetry_service.record(
            db,
            action,
            username=actor,
            module=module,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            duration_ms=duration_ms,
            detail=detail,
        )
    except Exception:
        pass
