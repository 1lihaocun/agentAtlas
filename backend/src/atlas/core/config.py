from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", extra="forbid")

    workspace: Path = Field(default_factory=Path.cwd)
    home: Path = Field(default_factory=Path.home)
    scan_roots: list[str] | None = None
    max_depth: int = Field(default=9, ge=1, le=30)
    host: str = "127.0.0.1"
    port: int = Field(default=7788, ge=1024, le=65535)
    frontend_dir: Path | None = None
    guide_path: Path | None = None
    codex_home: Path | None = None
    allowed_origins: list[str] = []
    refresh_on_start: bool = True

    @field_validator("workspace", "home", "frontend_dir", "guide_path", "codex_home")
    @classmethod
    def absolute_path(cls, value):
        return value.expanduser().resolve() if value is not None else None

    @field_validator("host")
    @classmethod
    def local_host(cls, value):
        if value not in {"127.0.0.1", "::1"}:
            raise ValueError("仅支持本机监听地址")
        return value

    @property
    def state_dir(self):
        return self.workspace / ".agentatlas"

    @property
    def instruction_path(self):
        return self.workspace / "atlas.json"

    @property
    def static_dir(self):
        return self.frontend_dir or self.workspace / "frontend" / "dist"
