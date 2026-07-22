from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Approval
from .common import audit, dumps
from .policies import approval_policy_service


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRuntime:
    """Permission-aware local tool runtime restricted to one workspace."""

    HIGH_RISK_PATTERNS = [
        r"\bRemove-Item\b",
        r"\bdel\b",
        r"\berase\b",
        r"\bFormat-Volume\b",
        r"\bClear-Disk\b",
        r"\bStop-Computer\b",
        r"\bRestart-Computer\b",
        r"\breg\s+(add|delete)\b",
        r"\bnet\s+user\b",
    ]

    def __init__(self) -> None:
        self.root = settings.workspace_root.resolve()
        self._handlers: dict[str, ToolHandler] = {
            "list_directory": self._list_directory,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "search_files": self._search_files,
            "run_powershell": self._run_powershell,
        }

    def schemas(self) -> list[dict[str, Any]]:
        definitions = {
            "list_directory": {
                "description": "列出授权工作区中的文件",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
            "read_file": {
                "description": "读取授权工作区中的 UTF-8 文本文件",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "write_file": {
                "description": "写入授权工作区中的文本文件，需要写入权限",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            "search_files": {
                "description": "按文件名或文本内容搜索授权工作区",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["query"],
            },
            "run_powershell": {
                "description": "在授权工作区执行受控 PowerShell 命令",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": value["description"],
                    "parameters": {
                        "type": "object",
                        "properties": value["properties"],
                        "required": value["required"],
                    },
                },
            }
            for name, value in definitions.items()
        ]

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item["function"]["name"],
                "description": item["function"]["description"],
                "risk": "high"
                if item["function"]["name"] == "run_powershell"
                else "medium"
                if item["function"]["name"] == "write_file"
                else "low",
            }
            for item in self.schemas()
        ]

    def _resolve(self, value: str = ".") -> Path:
        candidate = (self.root / value).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError("路径超出授权工作区")
        return candidate

    def risk_level(self, tool: str, arguments: dict[str, Any]) -> str:
        if tool == "run_powershell":
            command = str(arguments.get("command", ""))
            if any(re.search(pattern, command, re.I) for pattern in self.HIGH_RISK_PATTERNS):
                return "critical"
            return "high"
        if tool == "write_file":
            return "medium"
        return "low"

    async def execute(
        self,
        db: AsyncSession,
        tool: str,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        policy_id: str | None = None,
        permission_mode: str = "ask",
        preapproved: bool = False,
    ) -> dict[str, Any]:
        if tool not in self._handlers:
            raise ValueError(f"未知工具: {tool}")
        risk = self.risk_level(tool, arguments)
        needs_approval = risk in {"medium", "high", "critical"}

        policy_decision = None
        if policy_id:
            policy_decision = await approval_policy_service.decide(
                db, tool=tool, risk=risk, agent_id=agent_id, policy_id=policy_id
            )
            permission_mode = policy_decision.decision

        if permission_mode == "deny" and needs_approval:
            return {"status": "denied", "risk": risk, "message": "策略禁止该操作"}
        if permission_mode == "ask" and needs_approval and not preapproved:
            approval = Approval(
                run_id=run_id,
                policy_id=policy_decision.policy_id if policy_decision else None,
                action_type=f"tool:{tool}",
                summary=(
                    f"Agent 请求执行 {tool}"
                    + (f"（策略：{policy_decision.policy_name}）" if policy_decision else "")
                ),
                payload_json=dumps(arguments),
                risk_level=risk,
            )
            db.add(approval)
            await db.flush()
            await audit(
                db,
                "approval.requested",
                "tool",
                approval.id,
                {"tool": tool, "risk": risk},
            )
            return {
                "status": "approval_required",
                "approval_id": approval.id,
                "risk": risk,
                "message": "操作已暂停，等待用户批准",
                "policy": policy_decision.policy_name if policy_decision else "direct-mode",
                "matched_rule": policy_decision.matched_rule if policy_decision else None,
            }

        try:
            result = await self._handlers[tool](arguments)
            await audit(db, "tool.executed", "tool", detail={"tool": tool, "risk": risk})
            return {"status": "completed", "risk": risk, "result": result}
        except Exception as exc:
            await audit(
                db,
                "tool.failed",
                "tool",
                detail={"tool": tool, "error": str(exc)},
                success=False,
            )
            raise

    async def _list_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(str(arguments.get("path", ".")))
        if not path.is_dir():
            raise ValueError("目标不是目录")
        items = []
        for item in sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            items.append(
                {
                    "name": item.name,
                    "path": str(item.relative_to(self.root)),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"path": str(path.relative_to(self.root)) or ".", "items": items[:500]}

    async def _read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(str(arguments["path"]))
        if not path.is_file():
            raise FileNotFoundError("文件不存在")
        if path.stat().st_size > settings.max_file_bytes:
            raise ValueError("文件超过读取大小限制")
        return {"path": str(path.relative_to(self.root)), "content": path.read_text("utf-8")}

    async def _write_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(str(arguments["path"]))
        content = str(arguments.get("content", ""))
        if len(content.encode("utf-8")) > settings.max_file_bytes:
            raise ValueError("内容超过写入大小限制")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path.relative_to(self.root)), "bytes": len(content.encode("utf-8"))}

    async def _search_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root = self._resolve(str(arguments.get("path", ".")))
        query = str(arguments["query"]).lower()
        matches: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if len(matches) >= 100:
                break
            if not path.is_file() or path.stat().st_size > settings.max_file_bytes:
                continue
            if query in path.name.lower():
                matches.append({"path": str(path.relative_to(self.root)), "match": "filename"})
                continue
            try:
                for line_number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                    if query in line.lower():
                        matches.append(
                            {
                                "path": str(path.relative_to(self.root)),
                                "line": line_number,
                                "preview": line[:240],
                            }
                        )
                        break
            except (UnicodeDecodeError, OSError):
                continue
        return {"query": query, "matches": matches}

    async def _run_powershell(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = str(arguments["command"])
        if any(re.search(pattern, command, re.I) for pattern in self.HIGH_RISK_PATTERNS):
            raise PermissionError("命令命中高危规则，已阻止执行")
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=settings.command_timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError("命令执行超时") from None
        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[-20_000:],
            "stderr": stderr.decode("utf-8", errors="replace")[-20_000:],
        }


tool_runtime = ToolRuntime()
