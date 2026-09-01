from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
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
    outbound_proxy: str | None = None
    codex_sandbox: str = "workspace-write"
    codex_network_access: bool = True
    # App-server workers are long-lived for the Frontier service lifetime. Each
    # worker serializes turns while keeping multiple Codex threads loaded.
    codex_app_server_max_workers: int = Field(default=1, ge=1)
    # The Codex limit counts spawned threads, not the root Agent. A value of 1
    # therefore permits at most one subagent at a time on a 2-vCPU host.
    codex_max_concurrent_subagents: int = Field(default=1, ge=0)
    codex_subagent_max_depth: int = Field(default=1, ge=0)
    codex_subagent_job_timeout_seconds: int = Field(default=1800, ge=1)
    # Serialize root Agent work across background exec runs and interactive
    # app-server turns. Child-process cgroups provide the hard resource limit.
    agent_max_concurrent_runs: int = Field(default=1, ge=1)
    agent_task_timeout_seconds: int = Field(default=10_800, ge=1)
    agent_interactive_timeout_seconds: int = Field(default=3600, ge=1)
    agent_app_server_start_timeout_seconds: int = Field(default=30, ge=1)
    agent_timeout_grace_seconds: int = Field(default=10, ge=1)
    # `required` is the production-safe mode: a task fails instead of running
    # without isolation. Local development defaults to `auto` for portability.
    agent_cgroup_mode: Literal["disabled", "auto", "required"] = "auto"
    agent_cgroup_root: Path | None = None
    agent_server_memory_high_bytes: int = Field(default=256 * 1024**2, ge=1)
    agent_server_memory_max_bytes: int = Field(default=384 * 1024**2, ge=1)
    agent_server_memory_swap_max_bytes: int = Field(default=64 * 1024**2, ge=0)
    agent_server_cpu_quota_percent: int = Field(default=50, ge=1)
    agent_server_tasks_max: int = Field(default=96, ge=1)
    agent_pool_memory_high_bytes: int = Field(default=640 * 1024**2, ge=1)
    agent_pool_memory_max_bytes: int = Field(default=768 * 1024**2, ge=1)
    agent_pool_memory_swap_max_bytes: int = Field(default=0, ge=0)
    agent_pool_cpu_quota_percent: int = Field(default=100, ge=1)
    agent_pool_tasks_max: int = Field(default=128, ge=1)
    agent_memory_high_bytes: int = Field(default=512 * 1024**2, ge=1)
    agent_memory_max_bytes: int = Field(default=640 * 1024**2, ge=1)
    agent_memory_swap_max_bytes: int = Field(default=0, ge=0)
    agent_cpu_quota_percent: int = Field(default=100, ge=1)
    agent_io_weight: int = Field(default=25, ge=1, le=10_000)
    agent_io_read_bps: int = Field(default=20 * 1024**2, ge=1)
    agent_io_write_bps: int = Field(default=10 * 1024**2, ge=1)
    agent_tasks_max: int = Field(default=96, ge=1)
    paper_processing_max_concurrency: int = Field(default=1, ge=1)
    paper_download_max_bytes: int = Field(default=64 * 1024**2, ge=1)
    paper_text_max_chars: int = Field(default=20_000_000, ge=1)
    translation_env_file: Path = PROJECT_ROOT / "translation.env"
    translation_timeout_seconds: int = Field(default=120, ge=1)
    aliyun_tts_api_key: str | None = None
    aliyun_tts_endpoint: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "multimodal-generation/generation"
    )
    aliyun_tts_model: str = "qwen3-tts-instruct-flash"
    aliyun_tts_voice: str = "Cherry"
    aliyun_tts_instructions: str = "请用自然、清晰、克制的中文播客旁白语气朗读。"
    aliyun_tts_timeout_seconds: float = Field(default=120, gt=0)
    aliyun_tts_max_retries: int = Field(default=2, ge=0, le=8)
    # Qwen3-TTS accepts at most 600 characters per non-streaming request. Keep
    # one character of headroom so later payload decoration cannot cross it.
    aliyun_tts_chunk_max_chars: int = Field(default=500, ge=50, le=599)
    aliyun_tts_audio_max_bytes: int = Field(default=32 * 1024**2, ge=1)
    enable_scheduler: bool = True
    enable_codex_runs: bool = True
    access_password: str = "byml-frontier"
    internal_api_token: str | None = None
    trusted_cookie_secure: bool = False
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
    def architecture_dir(self) -> Path:
        return self.data_dir / "architectures"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.pdf_dir,
            self.upload_dir,
            self.task_dir,
            self.text_dir,
            self.document_dir,
            self.architecture_dir,
            self.export_dir,
            self.audio_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
