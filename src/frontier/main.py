from __future__ import annotations

import re
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    architectures,
    auth,
    briefs,
    conversations,
    daily,
    graph,
    interests,
    papers,
    podcasts,
    system,
)
from .auth import request_is_internal, request_is_trusted
from .config import settings
from .database import SessionLocal, create_tables
from .interactive_stream import (
    cancel_all_codex_runs,
    start_codex_app_server_pool,
    stop_codex_app_server_pool,
)
from .models import utcnow
from .scheduler import start_scheduler, stop_scheduler
from .services.agent_runtime import initialize_agent_resource_manager
from .services.architecture_scene import cleanup_stale_architecture_locks
from .services.author_metadata import queue_cached_arxiv_author_metadata_backfill
from .services.common import ensure_default_settings
from .services.completion_summary import migrate_legacy_completion_summaries
from .services.daily_selections import migrate_legacy_daily_selections
from .services.document_reader import shutdown_document_worker
from .services.task_dispatcher import start_task_dispatcher, stop_task_dispatcher
from .services.task_recovery import recover_interrupted_tasks
from .services.translation_provider import close_translation_provider, warm_translation_provider


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup_cutoff = utcnow()
    create_tables()
    with SessionLocal() as session:
        ensure_default_settings(session)
        cleanup_stale_architecture_locks(session)
        migrate_legacy_completion_summaries(session)
        migrate_legacy_daily_selections(session)
        recover_interrupted_tasks(session, started_before=startup_cutoff)
    initialize_agent_resource_manager()
    queue_cached_arxiv_author_metadata_backfill()
    warm_translation_provider()
    if settings.enable_codex_runs:
        start_codex_app_server_pool()
    start_task_dispatcher()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        stop_task_dispatcher(wait=False)
        cancel_all_codex_runs()
        stop_codex_app_server_pool()
        shutdown_document_worker()
        close_translation_provider()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)


@app.middleware("http")
async def protect_private_api(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    public_api = (
        request.method == "OPTIONS"
        or path == "/api/health"
        or path == "/api/auth/status"
        or path == "/api/auth/login"
        or (request.method == "GET" and path == "/api/daily")
        or (request.method == "POST" and path == "/api/daily/identity/rotate")
        or (
            request.method in {"GET", "POST"}
            and bool(re.fullmatch(r"/api/daily/papers/[0-9a-f-]+/reviews", path))
        )
        or (
            request.method == "PUT"
            and bool(re.fullmatch(r"/api/daily/papers/[0-9a-f-]+/reviews/mine", path))
        )
        or (
            request.method == "POST"
            and bool(re.fullmatch(r"/api/daily/reviews/[0-9a-f-]+/replies", path))
        )
    )
    protected_service_path = path == "/api" or path.startswith("/api/") or path in {
        "/docs", "/redoc", "/openapi.json"
    }
    if (
        protected_service_path
        and not public_api
        and not request_is_trusted(request)
        and not request_is_internal(request)
    ):
        return JSONResponse(status_code=401, content={"detail": "请先验证此设备"})
    return await call_next(request)


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    if request.url.path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(daily.router)
app.include_router(papers.router)
app.include_router(papers.tags_router)
app.include_router(architectures.router)
app.include_router(graph.router)
app.include_router(briefs.router)
app.include_router(podcasts.router)
app.include_router(interests.router)
app.include_router(conversations.router)


@app.get("/api")
def api_root() -> dict:
    return {"name": settings.app_name, "docs": "/docs", "health": "/api/health"}


if settings.web_dist_dir.exists():
    assets = settings.web_dist_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = settings.web_dist_dir / path
        if candidate.is_file() and settings.web_dist_dir in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(
            settings.web_dist_dir / "index.html",
            headers={"Cache-Control": "no-cache"},
        )


def run() -> None:
    uvicorn.run("frontier.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
