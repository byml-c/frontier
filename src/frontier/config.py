from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="FRONTIER_",
        extra="ignore",
    )

    app_name: str = "Frontier Research"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    timezone: str = "Asia/Shanghai"
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str | None = None
    web_dist_dir: Path = PROJECT_ROOT / "web" / "dist"
    api_base_url: str = "http://127.0.0.1:8000"
    codex_command: str = "codex"
    codex_profile: str | None = None
    codex_sandbox: str = "workspace-write"
    codex_network_access: bool = True
    translation_env_file: Path = PROJECT_ROOT / "translation.env"
    enable_scheduler: bool = True
    enable_codex_runs: bool = True
    # Docling loads large layout models and is unsuitable for the default
    # low-memory deployment. arXiv HTML remains available independently.
    enable_docling: bool = False

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'frontier.db').as_posix()}"

    @property
    def local_api_base_url(self) -> str:
        """Return an endpoint that reaches this server from the same machine."""
        host = self.host.strip()
        if host in {"", "0.0.0.0"}:
            host = "127.0.0.1"
        elif host == "::":
            host = "::1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def task_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def document_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.pdf_dir,
            self.upload_dir,
            self.task_dir,
            self.text_dir,
            self.document_dir,
            self.export_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
