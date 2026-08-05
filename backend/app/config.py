from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import shutil
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "EvoAgent"
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT / "data"
)


def prepare_persistent_ca_bundle(data_root: Path = DATA_ROOT) -> Path | None:
    """Keep the TLS CA bundle outside PyInstaller's disposable extraction directory.

    A one-file PyInstaller backend loads certifi from ``%TEMP%\\_MEI*``. Windows
    cleanup utilities may remove files from that directory while the long-running
    backend process is still alive. HTTPX then fails before making a request with
    ``FileNotFoundError: [Errno 2]``. Copying the bundle to the application data
    directory makes HTTPS model and knowledge-provider calls independent of that
    temporary directory.
    """

    configured = os.environ.get("SSL_CERT_FILE", "").strip()
    if configured and Path(configured).is_file():
        return Path(configured)

    target = data_root / "tls" / "cacert.pem"
    if not target.is_file():
        try:
            import certifi

            source = Path(certifi.where())
            if not source.is_file():
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(target)
        except (ImportError, OSError):
            return None

    os.environ["SSL_CERT_FILE"] = str(target)
    return target


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="EVO_",
        extra="ignore",
    )

    app_name: str = "EvoAgent"
    version: str = "1.0.0"
    debug: bool = True
    database_url: str = f"sqlite+aiosqlite:///{(DATA_ROOT / 'evoagent.db').as_posix()}"
    workspace_root: Path = DATA_ROOT / "workspace"
    skills_root: Path = DATA_ROOT / "skills"
    plugins_root: Path = DATA_ROOT / "plugins"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "tauri://localhost",
            # Tauri 2 uses this origin for the Windows WebView2 frontend.
            "http://tauri.localhost",
            "https://tauri.localhost",
        ]
    )
    llm_provider: str = "demo"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    require_online_agents: bool = True
    siliconflow_api_key: str = ""
    max_agent_depth: int = 5
    max_tool_iterations: int = 8
    command_timeout_seconds: int = 30
    max_file_bytes: int = 2_000_000

    def prepare_directories(self) -> None:
        for path in (self.workspace_root, self.skills_root, self.plugins_root):
            path.mkdir(parents=True, exist_ok=True)
        prepare_persistent_ca_bundle(DATA_ROOT)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
