from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..models import Approval
from .common import audit, dumps, loads
from .policies import approval_policy_service
from .security import RuntimeSecurityContext


ToolHandler = Callable[
    [dict[str, Any], RuntimeSecurityContext], Awaitable[dict[str, Any]]
]


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
            "exec": self._run_powershell,
        }

    def schemas(self) -> list[dict[str, Any]]:
        definitions = {
            "list_directory": {
                "description": "列出安全策略允许访问的本地目录；支持绝对路径及“桌面”等常用目录",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
            "read_file": {
                "description": "读取安全策略允许访问的本地 UTF-8 文本文件",
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
                "description": "在安全策略允许访问的本地目录中按文件名或正文搜索",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["query"],
            },
            "run_powershell": {
                "description": "按当前安全模式在本地执行受控 PowerShell 命令",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            "exec": {
                "description": "执行本地 PowerShell 命令；工作目录和审批方式由本轮安全策略决定",
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
                if item["function"]["name"] in {"run_powershell", "exec"}
                else "medium"
                if item["function"]["name"] == "write_file"
                else "low",
            }
            for item in self.schemas()
        ]

    @staticmethod
    def _default_security() -> RuntimeSecurityContext:
        return RuntimeSecurityContext(
            profile="default",
            filesystem_mode="workspace",
            roots=[str(settings.workspace_root.resolve())],
            command_mode="risk_based",
            block_critical_commands=True,
            read_only=False,
        )

    @staticmethod
    def _known_folder(value: str) -> Path | None:
        normalized = value.strip().strip('"').strip("'").replace("\\", "/").lower()
        aliases = {
            "桌面": Path.home() / "Desktop",
            "desktop": Path.home() / "Desktop",
            "文档": Path.home() / "Documents",
            "documents": Path.home() / "Documents",
            "下载": Path.home() / "Downloads",
            "downloads": Path.home() / "Downloads",
            "主目录": Path.home(),
            "home": Path.home(),
            "~": Path.home(),
        }
        if normalized in aliases:
            return aliases[normalized]
        for alias, folder in aliases.items():
            prefix = f"{alias}/"
            if normalized.startswith(prefix):
                relative = normalized[len(prefix) :].strip("/")
                return folder / relative
        return None

    def _resolve(self, value: str, security: RuntimeSecurityContext) -> Path:
        raw = value.strip() or "."
        known = self._known_folder(raw)
        roots = [Path(item).expanduser().resolve() for item in security.roots]
        primary = roots[0] if roots else self.root
        candidate = known or Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = primary / candidate
        candidate = candidate.resolve()
        if security.filesystem_mode != "unrestricted" and not any(
            candidate == root or root in candidate.parents for root in roots
        ):
            allowed = "；".join(str(item) for item in roots)
            raise PermissionError(f"路径超出授权工作区：{candidate}；当前允许：{allowed}")
        return candidate

    @staticmethod
    def _display_path(path: Path, security: RuntimeSecurityContext) -> str:
        for value in security.roots:
            root = Path(value).resolve()
            if path == root:
                return "."
            if root in path.parents:
                return str(path.relative_to(root))
        return str(path)

    def _validate_powershell(
        self, command: str, security: RuntimeSecurityContext
    ) -> None:
        if security.read_only:
            raise PermissionError("当前为只读模式，禁止执行 PowerShell 命令")
        if security.filesystem_mode == "unrestricted":
            return
        path_candidates = re.findall(
            r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/][^\s'\"|;]+|\.\.(?:[\\/][^\s'\"|;]*)?)",
            command,
        )
        for value in path_candidates:
            self._resolve(value.rstrip(",)"), security)
        escape_constructs = [
            r"\$env:(?:USERPROFILE|HOMEDRIVE|HOMEPATH|SYSTEMROOT|WINDIR)",
            r"\$(?:HOME|PROFILE)\b",
            r"GetFolderPath\s*\(",
            r"\[Environment\]::",
        ]
        if any(re.search(pattern, command, re.I) for pattern in escape_constructs):
            raise PermissionError("工作区模式禁止通过系统目录变量绕过路径限制")

    @staticmethod
    def is_local_path_request(task: str) -> bool:
        explicit_command = re.search(
            r"(?:执行|运行|run|exec|execute)(?:一下|命令|command)?\s*[`'\"“]?"
            r"(?:npm|pnpm|yarn|pytest|python|pip|git|cargo|go|powershell|Get-[A-Za-z]+)",
            task,
            re.I,
        )
        local_place = re.search(
            r"桌面|本地|文件夹|目录|磁盘|硬盘|工作区|文档|下载|"
            r"[A-Za-z]:[\\/]|(?:^|\s)[.~]{1,2}[\\/]",
            task,
            re.I,
        )
        local_action = re.search(
            r"读取|打开|查看|列出|浏览|找出|查找|搜索|写入|保存|修改|删除|执行|"
            r"read|open|list|browse|find|search|write|save|edit|run|exec",
            task,
            re.I,
        )
        return bool(explicit_command or (local_place and local_action))

    @staticmethod
    def _explicit_command(task: str) -> str | None:
        fenced = re.search(r"`([^`\r\n]{2,1000})`", task)
        if fenced and re.search(r"执行|运行|run|exec|命令|command", task, re.I):
            return fenced.group(1).strip()
        direct = re.search(
            r"(?:执行|运行|run|exec|execute)(?:一下|命令|command)?\s*[：:]?\s*"
            r"[\"“']?((?:npm|pnpm|yarn|pytest|python|pip|git|cargo|go|powershell|Get-[A-Za-z]+)"
            r"[^\r\n，。；;！？!?]*)",
            task,
            re.I,
        )
        return direct.group(1).strip().rstrip("\"”'") if direct else None

    @staticmethod
    def plan_local_request(task: str) -> dict[str, Any] | None:
        if not ToolRuntime.is_local_path_request(task):
            return None
        command = ToolRuntime._explicit_command(task)
        if command:
            return {"tool": "exec", "arguments": {"command": command}}
        quoted = re.search(r"[\"“']([A-Za-z]:[\\/][^\"”']+)[\"”']", task)
        explicit = quoted or re.search(r"[A-Za-z]:[\\/][^\s，。；;！？!?]+", task)
        if explicit:
            path = explicit.group(1 if quoted else 0).strip().rstrip("，。")
        else:
            alias_match = re.search(r"桌面|desktop|下载|downloads|文档|documents", task, re.I)
            alias = alias_match.group(0) if alias_match else "."
            canonical = {
                "desktop": "桌面",
                "downloads": "下载",
                "documents": "文档",
            }.get(alias.lower(), alias)
            filename_match = re.search(
                rf"(?:{re.escape(alias)})(?:上|中|里|下)?(?:的)?(?:文件)?"
                r"[\s：:]*[\"“']?([^\"”'，。；;！？!?\s]+\.[A-Za-z0-9]{1,10})",
                task,
                re.I,
            ) if alias_match else None
            path = f"{canonical}/{filename_match.group(1)}" if filename_match else canonical
        file_like = bool(re.search(r"\.[A-Za-z0-9]{1,10}(?:\s|$|[，。])", path))
        if re.search(r"读取|打开|read|open", task, re.I) and file_like:
            return {"tool": "read_file", "arguments": {"path": path}}
        if re.search(r"查找|搜索|find|search", task, re.I):
            query = re.sub(
                r"帮我|请|在|从|本地|桌面|文档|下载|文件夹|目录|中|里|查找|搜索|找出|"
                r"find|search|desktop|documents|downloads",
                " ",
                task,
                flags=re.I,
            )
            query = " ".join(query.split()).strip(" ：:，,。") or "*"
            return {"tool": "search_files", "arguments": {"path": path, "query": query}}
        return {"tool": "list_directory", "arguments": {"path": path}}

    def risk_level(self, tool: str, arguments: dict[str, Any]) -> str:
        if tool in {"run_powershell", "exec"}:
            command = str(arguments.get("command", ""))
            if any(re.search(pattern, command, re.I) for pattern in self.HIGH_RISK_PATTERNS):
                return "critical"
            return "high"
        if tool == "write_file":
            return "medium"
        return "low"

    @staticmethod
    async def wait_for_approval(approval_id: str) -> dict[str, Any]:
        """Wait without retaining the caller's SQLite transaction or page connection."""
        for _attempt in range(1200):
            async with SessionLocal() as session:
                approval = await session.get(Approval, approval_id)
                if approval and approval.status != "pending":
                    if approval.status == "approved":
                        result = loads(approval.execution_result_json, {})
                        return result or {"status": "completed", "message": "操作已批准"}
                    return {"status": "denied", "message": "用户拒绝了该操作"}
            await asyncio.sleep(0.5)
        return {"status": "denied", "message": "等待用户审批超时"}

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
        security_context: RuntimeSecurityContext | None = None,
    ) -> dict[str, Any]:
        if tool not in self._handlers:
            raise ValueError(f"未知工具: {tool}")
        security = security_context or self._default_security()
        risk = self.risk_level(tool, arguments)
        needs_approval = risk in {"medium", "high", "critical"}

        if security.read_only and tool in {"write_file", "run_powershell", "exec"}:
            return {"status": "denied", "risk": risk, "message": "当前安全模式为只读"}
        if tool in {"run_powershell", "exec"}:
            self._validate_powershell(str(arguments.get("command", "")), security)
        if risk == "critical" and security.block_critical_commands:
            return {"status": "denied", "risk": risk, "message": "关键风险命令已被硬拦截"}

        policy_decision = None
        if security.command_mode == "risk_based" and policy_id:
            policy_decision = await approval_policy_service.decide(
                db, tool=tool, risk=risk, agent_id=agent_id, policy_id=policy_id
            )
            permission_mode = policy_decision.decision
        elif security.command_mode == "always_ask":
            permission_mode = "ask"
        elif security.command_mode in {"auto", "deny"}:
            permission_mode = security.command_mode

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
                payload_json=dumps(
                    {"arguments": arguments, "security_context": security.as_dict()}
                ),
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
                "security": security.as_dict(),
            }

        try:
            result = await self._handlers[tool](arguments, security)
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

    async def _list_directory(
        self, arguments: dict[str, Any], security: RuntimeSecurityContext
    ) -> dict[str, Any]:
        path = self._resolve(str(arguments.get("path", ".")), security)
        if not path.is_dir():
            raise ValueError("目标不是目录")
        items = []
        for item in sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            items.append(
                {
                    "name": item.name,
                    "path": self._display_path(item, security),
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"path": self._display_path(path, security), "items": items[:500]}

    async def _read_file(
        self, arguments: dict[str, Any], security: RuntimeSecurityContext
    ) -> dict[str, Any]:
        path = self._resolve(str(arguments["path"]), security)
        if not path.is_file():
            raise FileNotFoundError("文件不存在")
        if path.stat().st_size > settings.max_file_bytes:
            raise ValueError("文件超过读取大小限制")
        return {"path": self._display_path(path, security), "content": path.read_text("utf-8")}

    async def _write_file(
        self, arguments: dict[str, Any], security: RuntimeSecurityContext
    ) -> dict[str, Any]:
        path = self._resolve(str(arguments["path"]), security)
        content = str(arguments.get("content", ""))
        if len(content.encode("utf-8")) > settings.max_file_bytes:
            raise ValueError("内容超过写入大小限制")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": self._display_path(path, security), "bytes": len(content.encode("utf-8"))}

    async def _search_files(
        self, arguments: dict[str, Any], security: RuntimeSecurityContext
    ) -> dict[str, Any]:
        root = self._resolve(str(arguments.get("path", ".")), security)
        query = str(arguments["query"]).lower()
        matches: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            if len(matches) >= 100:
                break
            if not path.is_file() or path.stat().st_size > settings.max_file_bytes:
                continue
            if query in path.name.lower():
                matches.append({"path": self._display_path(path, security), "match": "filename"})
                continue
            try:
                for line_number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                    if query in line.lower():
                        matches.append(
                            {
                                "path": self._display_path(path, security),
                                "line": line_number,
                                "preview": line[:240],
                            }
                        )
                        break
            except (UnicodeDecodeError, OSError):
                continue
        return {"query": query, "matches": matches}

    async def _run_powershell(
        self, arguments: dict[str, Any], security: RuntimeSecurityContext
    ) -> dict[str, Any]:
        command = str(arguments["command"])
        if security.block_critical_commands and any(
            re.search(pattern, command, re.I) for pattern in self.HIGH_RISK_PATTERNS
        ):
            raise PermissionError("命令命中高危规则，已阻止执行")
        root = Path(security.roots[0]).resolve() if security.roots else self.root
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            cwd=str(root),
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
