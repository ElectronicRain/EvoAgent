from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Extension, Skill
from .common import audit, dumps, loads


def parse_skill_file(path: Path) -> dict[str, str]:
    content = path.read_text("utf-8")
    metadata: dict[str, Any] = {}
    instructions = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            metadata = yaml.safe_load(parts[1]) or {}
            instructions = parts[2].strip()
    return {
        "name": str(metadata.get("name") or path.parent.name),
        "description": str(metadata.get("description") or ""),
        "version": str(metadata.get("version") or "1.0.0"),
        "instructions": instructions,
    }


class ExtensionService:
    async def sync_skills(self, db: AsyncSession) -> list[Skill]:
        synced: list[Skill] = []
        for path in settings.skills_root.glob("*/SKILL.md"):
            data = parse_skill_file(path)
            skill = await db.scalar(select(Skill).where(Skill.name == data["name"]))
            if not skill:
                skill = Skill(name=data["name"], instructions=data["instructions"])
                db.add(skill)
            skill.description = data["description"]
            skill.version = data["version"]
            skill.instructions = data["instructions"]
            skill.source_path = str(path)
            synced.append(skill)
        await audit(db, "skills.synced", "skill", detail={"count": len(synced)})
        return synced

    async def sync_plugins(self, db: AsyncSession) -> list[Extension]:
        synced: list[Extension] = []
        for path in settings.plugins_root.glob("*/plugin.json"):
            manifest = json.loads(path.read_text("utf-8"))
            name = str(manifest.get("name") or path.parent.name)
            extension = await db.scalar(select(Extension).where(Extension.name == name))
            if not extension:
                extension = Extension(name=name, kind="plugin")
                db.add(extension)
            extension.description = str(manifest.get("description") or "")
            extension.config_json = dumps({**manifest, "manifest_path": str(path)})
            extension.permissions_json = dumps(manifest.get("permissions") or [])
            extension.health = "ready"
            synced.append(extension)
        await audit(db, "plugins.synced", "extension", detail={"count": len(synced)})
        return synced

    async def test_connection(self, extension: Extension) -> dict[str, Any]:
        if extension.kind != "mcp":
            return {"status": "ready", "message": "插件清单有效"}
        config = loads(extension.config_json, {})
        transport = config.get("transport", "http")
        try:
            if transport == "http":
                result = await self._http_rpc(
                    str(config["url"]), "initialize", {"protocolVersion": "2025-03-26"}
                )
            elif transport == "stdio":
                result = await self._stdio_rpc(config, "initialize", {"protocolVersion": "2025-03-26"})
            else:
                raise ValueError("MCP transport 必须是 http 或 stdio")
            extension.health = "healthy"
            return {"status": "healthy", "result": result}
        except Exception as exc:
            extension.health = "unhealthy"
            return {"status": "unhealthy", "error": str(exc)}

    async def list_mcp_tools(self, extension: Extension) -> dict[str, Any]:
        config = loads(extension.config_json, {})
        if config.get("transport", "http") == "stdio":
            return await self._stdio_rpc(config, "tools/list", {})
        return await self._http_rpc(str(config["url"]), "tools/list", {})

    async def call_mcp_tool(
        self, extension: Extension, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        config = loads(extension.config_json, {})
        params = {"name": name, "arguments": arguments}
        if config.get("transport", "http") == "stdio":
            return await self._stdio_rpc(config, "tools/call", params)
        return await self._http_rpc(str(config["url"]), "tools/call", params)

    async def _http_rpc(self, url: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result") or {}

    async def _stdio_rpc(
        self, config: dict[str, Any], method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        command = str(config["command"])
        args = [str(item) for item in config.get("args", [])]
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(payload.encode("utf-8")), timeout=20
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError("MCP stdio 响应超时") from None
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        if not lines:
            raise RuntimeError(stderr.decode("utf-8", errors="replace") or "MCP 未返回数据")
        data = json.loads(lines[-1])
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result") or {}


extension_service = ExtensionService()

