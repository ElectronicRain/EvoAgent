from __future__ import annotations

import hashlib
import json
import platform
import secrets
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import TelemetryDevice, TelemetryEvent, UserAccount


SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "authorization",
    "api_key",
    "secret",
    "content",
    "input_text",
    "output_text",
    "prompt",
    "document_text",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class TelemetryService:
    def __init__(self) -> None:
        self._identity_cache: dict[str, str] | None = None

    @property
    def identity_path(self) -> Path:
        return settings.workspace_root.parent / "telemetry_identity.json"

    def identity(self) -> dict[str, str]:
        if self._identity_cache is not None:
            return dict(self._identity_cache)
        path = self.identity_path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("installation_id"):
                self._identity_cache = value
                return dict(value)
        except (OSError, ValueError, TypeError):
            pass
        value = {
            "installation_id": secrets.token_hex(24),
            "device_token": "",
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(_json(value), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # A stable process-local fallback is still preferable to losing events.
            value["installation_id"] = hashlib.sha256(
                f"{socket.gethostname()}:{settings.workspace_root.parent}".encode()
            ).hexdigest()[:48]
        self._identity_cache = value
        return dict(value)

    def save_identity(self, value: dict[str, str]) -> None:
        path = self.identity_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(_json(value), encoding="utf-8")
        temporary.replace(path)
        self._identity_cache = dict(value)

    @staticmethod
    def module_for(event_type: str) -> str:
        prefix = event_type.split(".", 1)[0]
        return {
            "user": "account",
            "auth": "account",
            "agent": "agent",
            "workflow": "workflow",
            "knowledge": "knowledge",
            "learning": "learning",
            "teaching": "teaching",
            "teaching_studio": "teaching",
            "research": "research",
            "update": "update",
            "frontend": "frontend",
            "page": "navigation",
        }.get(prefix, prefix or "system")

    @classmethod
    def sanitize(cls, value: Any, *, depth: int = 0) -> Any:
        if depth > 4:
            return "[truncated]"
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in list(value.items())[:40]:
                lowered = str(key).lower()
                if any(secret in lowered for secret in SENSITIVE_KEYS):
                    result[str(key)] = "[redacted]"
                else:
                    result[str(key)] = cls.sanitize(item, depth=depth + 1)
            return result
        if isinstance(value, list):
            return [cls.sanitize(item, depth=depth + 1) for item in value[:30]]
        if isinstance(value, str):
            # Do not upload absolute local paths or arbitrarily large text.
            if ":\\" in value or value.startswith(("/Users/", "/home/")):
                return "[local-path]"
            return value[:500]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return str(value)[:200]

    async def record(
        self,
        db: AsyncSession,
        event_type: str,
        *,
        username: str = "anonymous",
        user_id: str | None = None,
        module: str | None = None,
        resource_type: str = "",
        resource_id: str | None = None,
        success: bool = True,
        duration_ms: int = 0,
        detail: dict[str, Any] | None = None,
    ) -> TelemetryEvent | None:
        if not settings.telemetry_enabled:
            return None
        if user_id is None and username not in {"", "anonymous", "system", "local-user"}:
            user = await db.scalar(select(UserAccount).where(UserAccount.username == username))
            if user:
                user_id = user.id
                user.last_active_at = _utcnow()
        safe_detail = self.sanitize(detail or {})
        error_text = str(safe_detail.get("error") or safe_detail.get("message") or "")
        event = TelemetryEvent(
            installation_id=self.identity()["installation_id"],
            user_id=user_id,
            username=username[:80] or "anonymous",
            event_type=event_type[:100],
            module=(module or self.module_for(event_type))[:60],
            resource_type=resource_type[:60],
            resource_id=resource_id[:100] if resource_id else None,
            success=success,
            duration_ms=max(0, int(duration_ms or 0)),
            detail_json=_json(safe_detail),
            error_fingerprint=(
                hashlib.sha256(f"{event_type}:{error_text}".encode()).hexdigest()
                if not success or error_text
                else ""
            ),
            client_version=settings.version,
            occurred_at=_utcnow(),
            sync_status="pending",
        )
        db.add(event)
        await db.flush()
        return event

    def device_payload(self) -> dict[str, str]:
        return {
            "installation_id": self.identity()["installation_id"],
            "device_name": socket.gethostname()[:160],
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}"[:120],
            "app_version": settings.version,
        }

    async def _ensure_device_token(self, client: httpx.AsyncClient) -> str:
        identity = self.identity()
        if identity.get("device_token"):
            return identity["device_token"]
        response = await client.post(
            f"{settings.telemetry_hub_url.rstrip('/')}/api/telemetry-hub/devices/register",
            json=self.device_payload(),
        )
        response.raise_for_status()
        identity["device_token"] = str(response.json()["device_token"])
        self.save_identity(identity)
        return identity["device_token"]

    @staticmethod
    def event_payload(item: TelemetryEvent) -> dict[str, Any]:
        return {
            "id": item.id,
            "installation_id": item.installation_id,
            "user_id": item.user_id,
            "username": item.username,
            "event_type": item.event_type,
            "module": item.module,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "success": item.success,
            "duration_ms": item.duration_ms,
            "detail": _loads(item.detail_json, {}),
            "error_fingerprint": item.error_fingerprint,
            "client_version": item.client_version,
            "occurred_at": item.occurred_at.isoformat(),
        }

    async def sync_pending(self, db: AsyncSession) -> dict[str, Any]:
        pending_count = await db.scalar(
            select(func.count(TelemetryEvent.id)).where(
                TelemetryEvent.sync_status.in_(["pending", "failed"])
            )
        ) or 0
        if not settings.telemetry_enabled:
            return {"enabled": False, "uploaded": 0, "pending": pending_count}
        if not settings.telemetry_hub_url:
            return {
                "enabled": True,
                "configured": False,
                "uploaded": 0,
                "pending": pending_count,
                "message": "中央同步服务尚未配置；事件已安全保存在本地队列。",
            }
        items = (
            await db.scalars(
                select(TelemetryEvent)
                .where(TelemetryEvent.sync_status.in_(["pending", "failed"]))
                .order_by(TelemetryEvent.occurred_at)
                .limit(settings.telemetry_batch_size)
            )
        ).all()
        if not items:
            return {"enabled": True, "configured": True, "uploaded": 0, "pending": 0}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                token = await self._ensure_device_token(client)
                response = await client.post(
                    f"{settings.telemetry_hub_url.rstrip('/')}/api/telemetry-hub/events/batch",
                    headers={"Authorization": f"Device {token}"},
                    json={"events": [self.event_payload(item) for item in items]},
                )
                response.raise_for_status()
            now = _utcnow()
            for item in items:
                item.sync_status = "synced"
                item.synced_at = now
                item.last_sync_error = ""
            await db.flush()
            return {
                "enabled": True,
                "configured": True,
                "uploaded": len(items),
                "pending": max(0, pending_count - len(items)),
            }
        except Exception as exc:
            message = str(exc)[:500]
            for item in items:
                item.sync_status = "failed"
                item.sync_attempts += 1
                item.last_sync_error = message
            await db.flush()
            return {
                "enabled": True,
                "configured": True,
                "uploaded": 0,
                "pending": pending_count,
                "error": message,
            }

    async def register_hub_device(
        self, db: AsyncSession, payload: dict[str, Any]
    ) -> tuple[TelemetryDevice, str]:
        installation_id = str(payload["installation_id"])
        device = await db.scalar(
            select(TelemetryDevice).where(
                TelemetryDevice.installation_id == installation_id
            )
        )
        token = secrets.token_urlsafe(40)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if device is None:
            device = TelemetryDevice(
                installation_id=installation_id,
                device_name=str(payload.get("device_name", ""))[:160],
                platform=str(payload.get("platform", ""))[:120],
                app_version=str(payload.get("app_version", ""))[:30],
                token_hash=token_hash,
            )
            db.add(device)
        else:
            device.device_name = str(payload.get("device_name", device.device_name))[:160]
            device.platform = str(payload.get("platform", device.platform))[:120]
            device.app_version = str(payload.get("app_version", device.app_version))[:30]
            device.token_hash = token_hash
            device.status = "active"
            device.last_seen_at = _utcnow()
        await db.flush()
        return device, token

    async def authenticate_device(
        self, db: AsyncSession, authorization: str | None
    ) -> TelemetryDevice | None:
        if not authorization or not authorization.startswith("Device "):
            return None
        token_hash = hashlib.sha256(authorization[7:].strip().encode()).hexdigest()
        return await db.scalar(
            select(TelemetryDevice).where(
                TelemetryDevice.token_hash == token_hash,
                TelemetryDevice.status == "active",
            )
        )

    async def ingest_events(
        self, db: AsyncSession, device: TelemetryDevice, values: list[dict[str, Any]]
    ) -> int:
        received = 0
        now = _utcnow()
        for value in values[:1000]:
            event_id = str(value.get("id", ""))
            if not event_id or await db.get(TelemetryEvent, event_id):
                continue
            occurred_at = now
            try:
                occurred_at = datetime.fromisoformat(str(value.get("occurred_at", "")))
            except ValueError:
                pass
            db.add(
                TelemetryEvent(
                    id=event_id,
                    installation_id=device.installation_id,
                    device_id=device.id,
                    user_id=str(value.get("user_id") or "")[:36] or None,
                    username=str(value.get("username") or "anonymous")[:80],
                    event_type=str(value.get("event_type") or "unknown")[:100],
                    module=str(value.get("module") or "system")[:60],
                    resource_type=str(value.get("resource_type") or "")[:60],
                    resource_id=str(value.get("resource_id") or "")[:100] or None,
                    success=bool(value.get("success", True)),
                    duration_ms=max(0, int(value.get("duration_ms") or 0)),
                    detail_json=_json(self.sanitize(value.get("detail") or {})),
                    error_fingerprint=str(value.get("error_fingerprint") or "")[:64],
                    client_version=str(value.get("client_version") or "")[:30],
                    occurred_at=occurred_at,
                    received_at=now,
                    sync_status="received",
                    synced_at=now,
                )
            )
            received += 1
        device.last_seen_at = now
        device.last_sync_at = now
        await db.flush()
        return received

    async def remote_admin_get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any | None:
        if not settings.telemetry_hub_url or not settings.telemetry_hub_admin_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{settings.telemetry_hub_url.rstrip('/')}/api/telemetry-hub/admin/{path}",
                    headers={"X-Admin-Key": settings.telemetry_hub_admin_key},
                    params=params,
                )
                response.raise_for_status()
                return response.json()
        except Exception:
            return None

    async def pending_count(self, db: AsyncSession) -> int:
        return int(
            await db.scalar(
                select(func.count(TelemetryEvent.id)).where(
                    or_(
                        TelemetryEvent.sync_status == "pending",
                        TelemetryEvent.sync_status == "failed",
                    )
                )
            )
            or 0
        )


telemetry_service = TelemetryService()
