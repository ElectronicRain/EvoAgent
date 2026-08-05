from __future__ import annotations

import hashlib
import io
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..config import settings
from ..models import Skill
from .common import dumps

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 100
MAX_EXPANDED_BYTES = 5 * 1024 * 1024
SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
TEXT_SUFFIXES = {
    "",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".py",
    ".ps1",
    ".sh",
    ".js",
    ".ts",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".css",
    ".html",
}
BLOCKED_SUFFIXES = {
    ".exe",
    ".dll",
    ".msi",
    ".com",
    ".scr",
    ".pif",
    ".lnk",
    ".jar",
    ".class",
    ".so",
    ".dylib",
    ".sys",
    ".vbs",
    ".vbe",
    ".wsf",
}


THREAT_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "critical",
        "destructive-command",
        re.compile(
            r"(?i)(rm\s+-rf\s+[/~]|format(?:\.com)?\s+[a-z]:|diskpart\b|"
            r"remove-item\b[^\n]{0,100}-(?:recurse|r)\b[^\n]{0,80}-(?:force|fo)\b|"
            r"del\s+/[sq]\s+[a-z]:\\)"
        ),
        "包含可能破坏磁盘或大范围删除文件的命令",
    ),
    (
        "critical",
        "credential-exfiltration",
        re.compile(
            r"(?is)(api[_ -]?key|password|token|credential|\.ssh|\.env).{0,180}"
            r"(upload|exfiltrat|send\s+to|httpx?\.post|requests\.post|curl\s+[^\\n]*-[dF])"
        ),
        "疑似读取凭据并向外发送",
    ),
    (
        "high",
        "persistence",
        re.compile(
            r"(?i)(currentversion\\run|schtasks\s+/create|startup\\|"
            r"new-service\b|sc(?:\.exe)?\s+create\b)"
        ),
        "包含创建开机启动、计划任务或系统服务的持久化行为",
    ),
    (
        "high",
        "encoded-execution",
        re.compile(
            r"(?i)(frombase64string|powershell(?:\.exe)?\s+-(?:enc|encodedcommand)|"
            r"invoke-expression|\biex\s*\(|eval\s*\(\s*base64)"
        ),
        "包含编码载荷或动态执行行为",
    ),
    (
        "high",
        "prompt-injection",
        re.compile(
            r"(?i)(ignore\s+(?:all\s+)?(?:previous|system|developer)\s+instructions|"
            r"reveal\s+(?:the\s+)?system\s+prompt|绕过.{0,12}(系统|安全|权限)|"
            r"忽略.{0,12}(系统|开发者|安全).{0,12}(指令|要求))"
        ),
        "包含试图覆盖系统指令或绕过安全边界的提示注入",
    ),
    (
        "medium",
        "unbounded-network-download",
        re.compile(
            r"(?i)(invoke-webrequest|curl|wget).{0,160}(\|\s*(?:sh|bash)|"
            r"start-process|invoke-expression|\biex\b)"
        ),
        "包含下载后直接执行远程内容的行为",
    ),
    (
        "medium",
        "security-tampering",
        re.compile(
            r"(?i)(set-mppreference.{0,80}disable|disable.*(?:defender|firewall|"
            r"smart\s*app\s*control)|bcdedit.{0,80}(?:testsigning|nointegritychecks))"
        ),
        "包含关闭系统安全保护或代码完整性的行为",
    ),
]
RISK_ORDER = {"unknown": -1, "none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class SkillPackageError(ValueError):
    def __init__(self, message: str, report: dict[str, Any] | None = None):
        super().__init__(message)
        self.report = report or {}


class SkillSecurityService:
    @staticmethod
    def _finding(
        severity: str,
        code: str,
        message: str,
        *,
        path: str = "SKILL.md",
        line: int | None = None,
    ) -> dict[str, Any]:
        return {
            "severity": severity,
            "code": code,
            "message": message,
            "path": path,
            "line": line,
        }

    @staticmethod
    def _decode_text(path: str, content: bytes) -> str:
        if b"\x00" in content:
            raise UnicodeError("binary")
        return content.decode("utf-8")

    @staticmethod
    def _parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
        if not content.startswith("---"):
            raise ValueError("SKILL.md 必须以 YAML frontmatter 开头")
        parts = content.split("---", 2)
        if len(parts) != 3:
            raise ValueError("SKILL.md frontmatter 未正确闭合")
        metadata = yaml.safe_load(parts[1])
        if not isinstance(metadata, dict):
            raise TypeError("SKILL.md frontmatter 必须是对象")
        instructions = parts[2].strip()
        if not instructions:
            raise ValueError("SKILL.md 必须包含执行指令")
        return metadata, instructions

    @staticmethod
    def _hash_files(files: dict[str, bytes]) -> str:
        digest = hashlib.sha256()
        for path in sorted(files):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(files[path])
            digest.update(b"\0")
        return digest.hexdigest()

    def validate_files(self, files: dict[str, bytes]) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        checks = {
            "has_skill_md": "SKILL.md" in files,
            "valid_frontmatter": False,
            "valid_name": False,
            "has_description": False,
            "has_instructions": False,
            "safe_paths": True,
            "no_blocked_binaries": True,
            "static_scan_passed": False,
        }
        metadata: dict[str, Any] = {}
        instructions = ""

        if "SKILL.md" not in files:
            findings.append(
                self._finding("critical", "missing-skill-md", "压缩包根目录缺少 SKILL.md")
            )
        else:
            try:
                skill_text = self._decode_text("SKILL.md", files["SKILL.md"])
                metadata, instructions = self._parse_skill_markdown(skill_text)
                checks["valid_frontmatter"] = True
                name = str(metadata.get("name") or "").strip()
                description = str(metadata.get("description") or "").strip()
                checks["valid_name"] = bool(SKILL_NAME.fullmatch(name))
                checks["has_description"] = bool(description)
                checks["has_instructions"] = len(instructions) >= 20
                if not checks["valid_name"]:
                    findings.append(
                        self._finding(
                            "high",
                            "invalid-name",
                            "Skill name 必须为 1-64 位小写字母、数字或连字符",
                        )
                    )
                if not description:
                    findings.append(
                        self._finding("medium", "missing-description", "缺少触发场景说明 description")
                    )
                if len(instructions) < 20:
                    findings.append(
                        self._finding("high", "instructions-too-short", "Skill 执行指令过短")
                    )
            except (
                TypeError,
                UnicodeError,
                UnicodeDecodeError,
                yaml.YAMLError,
                ValueError,
            ) as exc:
                findings.append(
                    self._finding("high", "invalid-skill-md", f"SKILL.md 格式无效：{exc}")
                )

        for path, content in files.items():
            pure = PurePosixPath(path)
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or ":" in pure.parts[0]
                or len(path) > 240
            ):
                checks["safe_paths"] = False
                findings.append(
                    self._finding("critical", "unsafe-path", "包内路径可能越界", path=path)
                )
                continue
            suffix = pure.suffix.lower()
            if suffix in BLOCKED_SUFFIXES:
                checks["no_blocked_binaries"] = False
                findings.append(
                    self._finding(
                        "critical",
                        "blocked-binary",
                        f"Skill 包不允许携带 {suffix or '二进制'} 可执行文件",
                        path=path,
                    )
                )
                continue
            if len(content) > MAX_FILE_BYTES:
                findings.append(
                    self._finding("high", "oversized-file", "单个文件超过 1 MB", path=path)
                )
                continue
            if suffix not in TEXT_SUFFIXES:
                findings.append(
                    self._finding(
                        "medium",
                        "unknown-file-type",
                        f"无法静态审查的文件类型：{suffix or '无扩展名'}",
                        path=path,
                    )
                )
                continue
            try:
                text = self._decode_text(path, content)
            except (UnicodeError, UnicodeDecodeError):
                findings.append(
                    self._finding("high", "binary-content", "文本文件包含二进制内容", path=path)
                )
                continue
            for severity, code, pattern, message in THREAT_RULES:
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(
                        self._finding(severity, code, message, path=path, line=line)
                    )

        highest = "none"
        for finding in findings:
            if RISK_ORDER[finding["severity"]] > RISK_ORDER[highest]:
                highest = finding["severity"]
        required_checks = (
            checks["has_skill_md"],
            checks["valid_frontmatter"],
            checks["valid_name"],
            checks["has_description"],
            checks["has_instructions"],
            checks["safe_paths"],
            checks["no_blocked_binaries"],
        )
        verified = all(required_checks) and RISK_ORDER[highest] <= RISK_ORDER["low"]
        checks["static_scan_passed"] = RISK_ORDER[highest] <= RISK_ORDER["low"]
        return {
            "is_skill": all(required_checks[:5]),
            "safe": verified,
            "status": "verified" if verified else "rejected",
            "risk_level": highest,
            "metadata": {
                "name": str(metadata.get("name") or ""),
                "description": str(metadata.get("description") or ""),
                "version": str(metadata.get("version") or "1.0.0"),
            },
            "instructions": instructions,
            "checks": checks,
            "findings": findings,
            "files": sorted(files),
            "content_hash": self._hash_files(files),
            "scanner_version": "1.0.0",
            "scanned_at": datetime.now(UTC).isoformat(),
        }

    def package_files(self, filename: str, payload: bytes) -> dict[str, bytes]:
        if len(payload) > MAX_UPLOAD_BYTES:
            raise SkillPackageError("Skill 上传文件不能超过 6 MB")
        lower_name = filename.lower()
        if lower_name.endswith(".md"):
            return {"SKILL.md": payload}
        if not lower_name.endswith(".zip"):
            raise SkillPackageError("仅支持上传 SKILL.md 或 .zip Skill 包")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise SkillPackageError("上传文件不是有效 ZIP") from exc
        infos = [item for item in archive.infolist() if not item.is_dir()]
        if not infos or len(infos) > MAX_FILES:
            raise SkillPackageError(f"Skill 包文件数必须在 1-{MAX_FILES} 之间")
        expanded = sum(item.file_size for item in infos)
        if expanded > MAX_EXPANDED_BYTES:
            raise SkillPackageError("Skill 包解压后不能超过 5 MB")
        for item in infos:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            mode = item.external_attr >> 16
            if (
                path.is_absolute()
                or ".." in path.parts
                or (path.parts and ":" in path.parts[0])
                or stat.S_ISLNK(mode)
            ):
                raise SkillPackageError(f"Skill 包含不安全路径或符号链接：{item.filename}")
            if item.file_size > MAX_FILE_BYTES:
                raise SkillPackageError(f"Skill 包单个文件超过 1 MB：{item.filename}")
            if item.compress_size and item.file_size / item.compress_size > 200:
                raise SkillPackageError(f"Skill 包疑似压缩炸弹：{item.filename}")
        skill_entries = [
            PurePosixPath(item.filename.replace("\\", "/"))
            for item in infos
            if PurePosixPath(item.filename.replace("\\", "/")).name == "SKILL.md"
        ]
        if len(skill_entries) != 1:
            raise SkillPackageError("ZIP 中必须且只能包含一个 SKILL.md")
        root = skill_entries[0].parent
        files: dict[str, bytes] = {}
        for item in infos:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            files[relative.as_posix()] = archive.read(item)
        return files

    def validate_upload(self, filename: str, payload: bytes) -> tuple[dict[str, bytes], dict[str, Any]]:
        files = self.package_files(filename, payload)
        return files, self.validate_files(files)

    def validate_directory(self, directory: Path) -> dict[str, Any]:
        files: dict[str, bytes] = {}
        if not directory.exists():
            return self.validate_files(files)
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(directory).as_posix()
            if len(files) >= MAX_FILES:
                raise SkillPackageError(f"Skill 目录文件数不能超过 {MAX_FILES}")
            content = path.read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise SkillPackageError(f"Skill 文件超过 1 MB：{relative}")
            files[relative] = content
        return self.validate_files(files)

    @staticmethod
    def apply_report(skill: Skill, report: dict[str, Any]) -> None:
        skill.validation_status = str(report["status"])
        skill.risk_level = str(report["risk_level"])
        skill.validation_json = dumps(
            {key: value for key, value in report.items() if key != "instructions"}
        )
        skill.content_hash = str(report["content_hash"])
        skill.verified_at = (
            datetime.now(UTC) if report["status"] == "verified" else None
        )
        skill.enabled = report["status"] == "verified"

    def install_verified(self, files: dict[str, bytes], report: dict[str, Any]) -> Path:
        if report.get("status") != "verified":
            raise SkillPackageError("Skill 未通过安全校验", report)
        name = str(report["metadata"]["name"])
        settings.skills_root.mkdir(parents=True, exist_ok=True)
        destination = (settings.skills_root / name).resolve()
        root = settings.skills_root.resolve()
        if root not in destination.parents:
            raise SkillPackageError("Skill 目标路径越界", report)
        if destination.exists():
            raise FileExistsError(f"Skill“{name}”已存在，请先使用重新校验或更换名称")
        with tempfile.TemporaryDirectory(prefix=".skill-upload-", dir=root) as temp:
            staging = Path(temp) / name
            staging.mkdir()
            for relative, content in files.items():
                target = (staging / relative).resolve()
                if staging.resolve() not in target.parents:
                    raise SkillPackageError("Skill 文件路径越界", report)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            shutil.move(str(staging), str(destination))
        return destination / "SKILL.md"


skill_security_service = SkillSecurityService()
