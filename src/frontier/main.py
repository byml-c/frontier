from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import briefs, conversations, graph, papers, system
from .config import settings
from .database import SessionLocal, create_tables
from .scheduler import start_scheduler, stop_scheduler
from .services.common import ensure_default_settings
from .services.translation_provider import close_translation_provider, warm_translation_provider


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_tables()
    with SessionLocal() as session:
        ensure_default_settings(session)
    warm_translation_provider()
    start_scheduler()
    yield
    stop_scheduler()
    close_translation_provider()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system.router)
app.include_router(papers.router)
app.include_router(papers.tags_router)
app.include_router(graph.router)
app.include_router(briefs.router)
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
        return FileResponse(settings.web_dist_dir / "index.html")


def run() -> None:
    uvicorn.run("frontier.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
