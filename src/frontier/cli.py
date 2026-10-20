from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from .auth import INTERNAL_TOKEN_HEADER, internal_api_token
from .config import settings
from .services.generated_content import (
    PAPER_ANALYSIS_PROSE_FIELDS,
    non_chinese_prose_fields,
    non_chinese_structured_summary_fields,
)

app = typer.Typer(no_args_is_help=True, help="Frontier Agent toolkit CLI")
paper_app = typer.Typer(no_args_is_help=True)
graph_app = typer.Typer(no_args_is_help=True)
brief_app = typer.Typer(no_args_is_help=True)
memory_app = typer.Typer(no_args_is_help=True)
interest_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
setting_app = typer.Typer(no_args_is_help=True)
source_app = typer.Typer(no_args_is_help=True)
bootstrap_app = typer.Typer(no_args_is_help=True)
architecture_app = typer.Typer(no_args_is_help=True)
narration_app = typer.Typer(no_args_is_help=True)
podcast_app = typer.Typer(no_args_is_help=True)
research_group_app = typer.Typer(no_args_is_help=True)

app.add_typer(paper_app, name="paper")
app.add_typer(graph_app, name="graph")
app.add_typer(brief_app, name="brief")
app.add_typer(memory_app, name="memory")
app.add_typer(interest_app, name="interest")
app.add_typer(task_app, name="task")
app.add_typer(setting_app, name="setting")
app.add_typer(source_app, name="source")
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(architecture_app, name="architecture")
app.add_typer(narration_app, name="narration")
app.add_typer(podcast_app, name="podcast")
app.add_typer(research_group_app, name="research-group")


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def parse_json(value: str) -> Any:
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


class ApiClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get("FRONTIER_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with httpx.Client(trust_env=False) as http_client:
                response = http_client.request(
                    method,
                    f"{self.base_url}{path}",
                    timeout=kwargs.pop("timeout", 120),
                    headers={INTERNAL_TOKEN_HEADER: internal_api_token(), **kwargs.pop("headers", {})},
                    **kwargs,
                )
            response.raise_for_status()
            if response.status_code == 204:
                return {"ok": True}
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json()
            except ValueError:
                detail = {"detail": exc.response.text}
            emit({"ok": False, "status": exc.response.status_code, "error": detail})
            raise typer.Exit(1) from exc
        except httpx.HTTPError as exc:
            emit({"ok": False, "error": str(exc), "base_url": self.base_url})
            raise typer.Exit(1) from exc


client = ApiClient()


def _require_chinese_agent_prose(
    payload: Any,
    *,
    fields: set[str] | frozenset[str] = PAPER_ANALYSIS_PROSE_FIELDS,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        emit({"ok": False, "error": "Paper update data must be a JSON object."})
        raise typer.Exit(1)
    invalid = non_chinese_prose_fields(payload, fields=fields)
    if invalid:
        emit(
            {
                "ok": False,
                "error": (
                    "Agent-generated paper analysis must be written in Simplified Chinese. "
                    "Rewrite the listed fields and retry the same command."
                ),
                "fields": invalid,
            }
        )
        raise typer.Exit(1)
    return payload


@app.command("health")
def health() -> None:
    emit(client.request("GET", "/api/health"))


@paper_app.command("list")
def paper_list(
    query: str | None = typer.Option(None, "--query", "-q"),
    status: str | None = None,
    node_id: str | None = None,
    tag_id: str | None = None,
    library: bool = False,
    limit: int = 50,
) -> None:
    emit(
        client.request(
            "GET",
            "/api/papers",
            params={
                "q": query,
                "status": status,
                "node_id": node_id,
                "tag_id": tag_id,
                "library": str(library).lower(),
                "limit": limit,
            },
        )
    )


@paper_app.command("get")
def paper_get(paper_id: str) -> None:
    emit(client.request("GET", f"/api/papers/{paper_id}"))


@paper_app.command("create")
def paper_create(data: str = typer.Option(..., help="JSON string or @file")) -> None:
    emit(client.request("POST", "/api/papers", json=parse_json(data)))


@paper_app.command("add-arxiv")
def paper_add_arxiv(value: str) -> None:
    emit(client.request("POST", "/api/papers/ingest/arxiv", data={"value": value}))


@paper_app.command("add-url")
def paper_add_url(url: str) -> None:
    emit(client.request("POST", "/api/papers/ingest/url", data={"url": url}))


@paper_app.command("upload")
def paper_upload(path: Path) -> None:
    if not path.is_file():
        emit({"ok": False, "error": f"File not found: {path}"})
        raise typer.Exit(1)
    with path.open("rb") as file:
        emit(
            client.request(
                "POST",
                "/api/papers/ingest/pdf",
                files={"file": (path.name, file, "application/pdf")},
                timeout=180,
            )
        )


@paper_app.command("update")
def paper_update(paper_id: str, data: str = typer.Option(..., help="JSON string or @file")) -> None:
    payload = _require_chinese_agent_prose(parse_json(data))
    emit(client.request("PATCH", f"/api/papers/{paper_id}", json=payload))


@paper_app.command("assign-group")
def paper_assign_group(paper_id: str, group_id: str) -> None:
    """Bind a paper to the canonical research group selected by the metadata Agent."""
    emit(
        client.request(
            "PATCH",
            f"/api/papers/{paper_id}",
            json={"research_group_id": group_id},
        )
    )


@paper_app.command("metadata-context")
def paper_metadata_context(paper_id: str) -> None:
    emit(client.request("GET", f"/api/papers/{paper_id}/metadata-context"))


@paper_app.command("first-page")
def paper_first_page(paper_id: str, max_chars: int = 2500) -> None:
    """Return a short, non-persisted affiliation/author snippet."""
    emit(
        client.request(
            "GET",
            f"/api/papers/{paper_id}/first-page",
            params={"max_chars": max_chars},
            timeout=120,
        )
    )


@paper_app.command("metadata-complete")
def paper_metadata_complete(
    paper_id: str,
    status: str = typer.Option("completed", help="completed or failed"),
    note: str | None = typer.Option(None, help="short evidence or unresolved reason"),
) -> None:
    emit(
        client.request(
            "POST",
            f"/api/papers/{paper_id}/metadata-enrichment/complete",
            json={"status": status, "note": note},
        )
    )


@paper_app.command("metadata-retry")
def paper_metadata_retry(paper_id: str) -> None:
    emit(client.request("POST", f"/api/papers/{paper_id}/metadata-enrichment/retry"))


@paper_app.command("metadata-retry-failed")
def paper_metadata_retry_failed() -> None:
    emit(client.request("POST", "/api/papers/metadata-enrichment/retry-failed"))


@paper_app.command("ignore")
def paper_ignore(paper_id: str, reason: str = typer.Option(...)) -> None:
    emit(client.request("POST", f"/api/papers/{paper_id}/ignore", json={"reason": reason}))


@paper_app.command("start-reading")
def paper_start_reading(paper_id: str) -> None:
    emit(client.request("POST", f"/api/papers/{paper_id}/start-reading"))


@paper_app.command("download")
def paper_download(paper_id: str) -> None:
    emit(client.request("POST", f"/api/papers/{paper_id}/download-pdf", timeout=180))


@paper_app.command("text")
def paper_text(paper_id: str, page: int | None = None, force: bool = False) -> None:
    result = client.request(
        "GET", f"/api/papers/{paper_id}/text", params={"force": str(force).lower()}, timeout=180
    )
    if page is not None:
        pages = [item for item in result["pages"] if item["page_number"] == page]
        result = {"paper_id": paper_id, "page_count": result["page_count"], "pages": pages}
    emit(result)


@paper_app.command("render-page")
def paper_render_page(paper_id: str, page: int, dpi: int = 144) -> None:
    """Render a PDF page and return its local image path for visual inspection."""
    emit(
        client.request(
            "POST",
            f"/api/papers/{paper_id}/render-page",
            params={"page_number": page, "dpi": dpi},
            timeout=180,
        )
    )


@paper_app.command("context")
def paper_context(paper_id: str, include_text: bool = True) -> None:
    result = {
        "paper": client.request("GET", f"/api/papers/{paper_id}"),
        "annotations": client.request("GET", f"/api/papers/{paper_id}/annotations"),
        "progress": client.request("GET", f"/api/papers/{paper_id}/progress"),
        "conversations": client.request(
            "GET", "/api/conversations", params={"kind": "paper", "reference_id": paper_id}
        ),
    }
    if include_text:
        result["pdf"] = client.request("GET", f"/api/papers/{paper_id}/text", timeout=180)
    emit(result)


@paper_app.command("agent-context")
def paper_agent_context(
    paper_id: str,
    query: str | None = typer.Option(None, "--query", "-q"),
    conversation_id: str | None = typer.Option(None, "--conversation-id"),
) -> None:
    """Read compact paper context, incrementally when bound to a Conversation."""
    if isinstance(conversation_id, str) and conversation_id:
        emit(
            client.request(
                "POST",
                f"/api/papers/{paper_id}/agent-context/sync",
                json={"conversation_id": conversation_id, "query": query},
            )
        )
        return
    params = {"query": query} if query is not None else None
    emit(client.request("GET", f"/api/papers/{paper_id}/agent-context", params=params))


@paper_app.command("resources")
def paper_resources(
    paper_id: str,
    kind: str | None = typer.Option(
        None, help="Filter: formula, figure, table, or reference"
    ),
    block_id: str | None = typer.Option(None, help="Return resources from one document block"),
) -> None:
    """Read cached TeX, figures, tables, and internal references."""
    emit(
        client.request(
            "GET",
            f"/api/papers/{paper_id}/document/resources",
            params={"kind": kind, "block_id": block_id},
        )
    )


@paper_app.command("finish")
def paper_finish(paper_id: str, data: str = typer.Option(..., help="JSON string or @file")) -> None:
    payload = _require_chinese_agent_prose(parse_json(data), fields={"summary"})
    invalid = non_chinese_structured_summary_fields(payload)
    if invalid:
        emit(
            {
                "ok": False,
                "error": (
                    "Agent-generated paper analysis must be written in Simplified Chinese. "
                    "Rewrite the listed fields and retry the same command."
                ),
                "fields": invalid,
            }
        )
        raise typer.Exit(1)
    emit(client.request("POST", f"/api/papers/{paper_id}/finish", json=payload))


def _task_output_path(path: Path) -> Path:
    resolved = path.resolve()
    task_root = settings.task_dir.resolve()
    if resolved != task_root and task_root not in resolved.parents:
        emit(
            {
                "ok": False,
                "error": "Architecture working files must be under data/tasks/.",
                "path": str(resolved),
            }
        )
        raise typer.Exit(1)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


@architecture_app.command("status")
def architecture_status(paper_id: str) -> None:
    emit(client.request("GET", f"/api/papers/{paper_id}/architecture"))


@architecture_app.command("checkout")
def architecture_checkout(
    paper_id: str,
    output: Annotated[Path, typer.Option("--output", "-o")],
    task_id: Annotated[str | None, typer.Option("--task-id")] = None,
    lock: Annotated[bool, typer.Option("--lock")] = False,
) -> None:
    if lock and not task_id:
        emit({"ok": False, "error": "--lock requires --task-id"})
        raise typer.Exit(1)
    if lock:
        client.request(
            "POST",
            f"/api/papers/{paper_id}/architecture/agent-lock",
            json={"ownerId": task_id},
        )
    document = client.request("GET", f"/api/papers/{paper_id}/architecture")
    scene = document.get("scene")
    if not isinstance(scene, dict):
        scene = {
            "schemaVersion": 1,
            "layoutEpoch": 1,
            "nodes": [],
            "tensors": [],
            "edges": [],
            "annotations": [],
            "groups": [],
            "sources": [],
            "assets": [],
        }
    target = _task_output_path(output)
    target.write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(
        {
            "paper_id": paper_id,
            "revision": document["revision"],
            "status": document["status"],
            "path": str(target),
            "locked": lock,
        }
    )


@architecture_app.command("validate")
def architecture_validate(data: str = typer.Option(..., help="JSON string or @file")) -> None:
    emit(client.request("POST", "/api/papers/_/architecture/validate", json=parse_json(data)))


@architecture_app.command("commit")
def architecture_commit(
    paper_id: str,
    file: Annotated[Path, typer.Option("--file", exists=True, dir_okay=False)],
    revision: Annotated[int, typer.Option("--revision", min=0)],
    task_id: Annotated[str, typer.Option("--task-id")],
) -> None:
    scene = json.loads(file.read_text(encoding="utf-8"))
    emit(
        client.request(
            "POST",
            f"/api/papers/{paper_id}/architecture/agent-commit",
            json={"expectedRevision": revision, "taskId": task_id, "scene": scene},
        )
    )


@architecture_app.command("unlock")
def architecture_unlock(paper_id: str, task_id: str = typer.Option(..., "--task-id")) -> None:
    emit(
        client.request(
            "POST",
            f"/api/papers/{paper_id}/architecture/agent-lock/release",
            json={"ownerId": task_id},
        )
    )


@architecture_app.command("command")
def architecture_command(paper_id: str, data: str = typer.Option(...)) -> None:
    payload = parse_json(data)
    if isinstance(payload, list):
        payload = {"commands": payload}
    emit(
        client.request(
            "POST", f"/api/papers/{paper_id}/architecture/commands", json=payload
        )
    )


@architecture_app.command("upload-asset")
def architecture_upload_asset(paper_id: str, path: Path) -> None:
    if not path.is_file():
        emit({"ok": False, "error": f"File not found: {path}"})
        raise typer.Exit(1)
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    if not media_type:
        emit({"ok": False, "error": "Only PNG, JPEG, and WebP images are supported."})
        raise typer.Exit(1)
    with path.open("rb") as file_handle:
        emit(
            client.request(
                "POST",
                f"/api/papers/{paper_id}/architecture/assets",
                files={"file": (path.name, file_handle, media_type)},
                timeout=180,
            )
        )


@graph_app.command("list")
def graph_list(query: str | None = typer.Option(None, "--query", "-q"), limit: int = 100) -> None:
    emit(client.request("GET", "/api/graph/nodes", params={"q": query, "limit": limit}))


@graph_app.command("get")
def graph_get(node_id: str) -> None:
    emit(client.request("GET", f"/api/graph/nodes/{node_id}"))


@graph_app.command("subgraph")
def graph_subgraph(focus_id: str | None = None, depth: int = 1, limit: int = 80) -> None:
    emit(
        client.request(
            "GET",
            "/api/graph/subgraph",
            params={"focus_id": focus_id, "depth": depth, "limit": limit},
        )
    )


@graph_app.command("create-node")
def graph_create_node(data: str = typer.Option(..., help="JSON string or @file")) -> None:
    emit(client.request("POST", "/api/graph/nodes", json=parse_json(data)))


@graph_app.command("update-node")
def graph_update_node(node_id: str, data: str = typer.Option(...)) -> None:
    emit(client.request("PATCH", f"/api/graph/nodes/{node_id}", json=parse_json(data)))


@graph_app.command("link")
def graph_link(source_id: str, target_id: str) -> None:
    emit(
        client.request(
            "POST", "/api/graph/edges", json={"source_id": source_id, "target_id": target_id}
        )
    )


@graph_app.command("attach-paper")
def graph_attach_paper(
    paper_id: str,
    node_id: str,
    reason: str | None = None,
    key: bool = False,
) -> None:
    emit(
        client.request(
            "POST",
            "/api/graph/paper-links",
            json={"paper_id": paper_id, "node_id": node_id, "reason": reason, "is_key": key},
        )
    )


@graph_app.command("add-progress")
def graph_add_progress(node_id: str, data: str = typer.Option(...)) -> None:
    emit(client.request("POST", f"/api/graph/nodes/{node_id}/progress", json=parse_json(data)))


@graph_app.command("add-idea")
def graph_add_idea(node_id: str, data: str = typer.Option(...)) -> None:
    emit(client.request("POST", f"/api/graph/nodes/{node_id}/ideas", json=parse_json(data)))


@graph_app.command("update-idea")
def graph_update_idea(idea_id: str, data: str = typer.Option(...)) -> None:
    emit(client.request("PATCH", f"/api/graph/ideas/{idea_id}", json=parse_json(data)))


@brief_app.command("list")
def brief_list(limit: int = 30) -> None:
    emit(client.request("GET", "/api/briefs", params={"limit": limit}))


@brief_app.command("get")
def brief_get(brief_id: str) -> None:
    emit(client.request("GET", f"/api/briefs/{brief_id}"))


@brief_app.command("create")
def brief_create(data: str = typer.Option(...)) -> None:
    emit(client.request("POST", "/api/briefs", json=parse_json(data)))


@brief_app.command("add-paper")
def brief_add_paper(brief_id: str, paper_id: str, rank: int, tier: str = "related") -> None:
    emit(
        client.request(
            "POST",
            f"/api/briefs/{brief_id}/items",
            json={"paper_id": paper_id, "rank": rank, "tier": tier},
        )
    )


@brief_app.command("publish")
def brief_publish(brief_id: str) -> None:
    emit(client.request("POST", f"/api/briefs/{brief_id}/publish"))


@brief_app.command("recover-failed")
def brief_recover_failed(brief_id: str) -> None:
    """Recover an interrupted daily publication; analyzed briefs are protected."""
    emit(client.request("POST", f"/api/briefs/{brief_id}/recover-failed"))


@brief_app.command("void-and-regenerate")
def brief_void_and_regenerate(brief_id: str) -> None:
    """Void a published daily brief and queue a replacement daily run."""
    emit(client.request("POST", f"/api/briefs/{brief_id}/void-and-regenerate"))


@brief_app.command("translate")
def brief_translate(brief_id: str, force: bool = False) -> None:
    emit(
        client.request(
            "POST", f"/api/briefs/{brief_id}/abstract-translations", params={"force": force}
        )
    )


@research_group_app.command("search")
def research_group_search(
    query: str,
    limit: int = typer.Option(20, min=1, max=200),
) -> None:
    """Search groups by English/Chinese name, institution, alias, or focus."""
    emit(
        client.request(
            "GET",
            "/api/research-groups",
            params={"q": query, "limit": limit},
        )
    )


@research_group_app.command("get")
def research_group_get(group_id: str) -> None:
    emit(client.request("GET", f"/api/research-groups/{group_id}"))


@research_group_app.command("create")
def research_group_create(data: str = typer.Option(..., help="JSON string or @file")) -> None:
    emit(client.request("POST", "/api/research-groups", json=parse_json(data)))


@research_group_app.command("update")
def research_group_update(
    group_id: str, data: str = typer.Option(..., help="JSON string or @file")
) -> None:
    emit(client.request("PATCH", f"/api/research-groups/{group_id}", json=parse_json(data)))


@narration_app.command("get")
def narration_get(paper_id: str, language: str = "zh-CN") -> None:
    emit(
        client.request(
            "GET",
            f"/api/papers/{paper_id}/narration",
            params={"language": language},
        )
    )


@narration_app.command("commit")
def narration_commit(
    paper_id: str,
    data: str = typer.Option(..., help="JSON string or @file"),
) -> None:
    emit(
        client.request(
            "POST",
            f"/api/papers/{paper_id}/narration/commit",
            json=parse_json(data),
        )
    )


@podcast_app.command("get")
def podcast_get(brief_id: str, language: str = "zh-CN") -> None:
    emit(
        client.request(
            "GET",
            f"/api/briefs/{brief_id}/podcast",
            params={"language": language},
        )
    )


@podcast_app.command("generate")
def podcast_generate(brief_id: str) -> None:
    emit(client.request("POST", f"/api/briefs/{brief_id}/podcast/generate"))


@podcast_app.command("commit")
def podcast_commit(
    brief_id: str,
    data: str = typer.Option(..., help="JSON string or @file"),
) -> None:
    emit(
        client.request(
            "POST",
            f"/api/briefs/{brief_id}/podcast/commit",
            json=parse_json(data),
        )
    )


@podcast_app.command("status")
def podcast_status(
    brief_id: str,
    data: str = typer.Option(..., help="JSON string or @file"),
    language: str = "zh-CN",
) -> None:
    emit(
        client.request(
            "PATCH",
            f"/api/briefs/{brief_id}/podcast/status",
            params={"language": language},
            json=parse_json(data),
        )
    )


@memory_app.command("list")
def memory_list(kind: str | None = None, active: bool | None = None) -> None:
    emit(client.request("GET", "/api/memories", params={"kind": kind, "active": active}))


@memory_app.command("create")
def memory_create(data: str = typer.Option(...)) -> None:
    emit(client.request("POST", "/api/memories", json=parse_json(data)))


@memory_app.command("update")
def memory_update(memory_id: str, data: str = typer.Option(...)) -> None:
    emit(client.request("PATCH", f"/api/memories/{memory_id}", json=parse_json(data)))


@interest_app.command("context")
def interest_context(recent_limit: int = typer.Option(20, min=1, max=100)) -> None:
    """Read the compact confirmed-feedback profile and recommendation calibration."""
    emit(
        client.request(
            "GET",
            "/api/interests/context",
            params={"recent_limit": recent_limit},
        )
    )


@interest_app.command("profile-update")
def interest_profile_update(data: str = typer.Option(..., help="JSON string or @file")) -> None:
    """CAS-update the single evidence-backed semantic interest profile."""
    emit(client.request("PUT", "/api/interests/profile", json=parse_json(data)))


@task_app.command("list")
def task_list(status: str | None = None, limit: int = 50) -> None:
    emit(client.request("GET", "/api/tasks", params={"status": status, "limit": limit}))


@task_app.command("get")
def task_get(task_id: str) -> None:
    emit(client.request("GET", f"/api/tasks/{task_id}"))


@task_app.command("run-daily")
def task_run_daily(target_date: str | None = None) -> None:
    params = {"target_date": target_date} if target_date else None
    emit(client.request("POST", "/api/tasks/run-daily", params=params))


@task_app.command("retry")
def task_retry(task_id: str) -> None:
    emit(client.request("POST", f"/api/tasks/{task_id}/retry"))


@setting_app.command("list")
def setting_list() -> None:
    emit(client.request("GET", "/api/settings"))


@setting_app.command("set")
def setting_set(key: str, value: str = typer.Option(..., help="JSON value")) -> None:
    emit(client.request("PUT", f"/api/settings/{key}", json={"value": parse_json(value)}))


@source_app.command("arxiv-search")
def source_arxiv_search(query: str, max_results: int = 50, ingest: bool = True) -> None:
    emit(
        client.request(
            "POST",
            "/api/sources/arxiv/search",
            params={"query": query, "max_results": max_results, "ingest": str(ingest).lower()},
            timeout=660,
        )
    )


@source_app.command("arxiv-discover")
def source_arxiv_discover(
    date: str | None = typer.Option(None, help="Research date (YYYY-MM-DD)"),
    offset: int = 0,
    page_size: int = 100,
) -> None:
    """Scan recent metadata and return at most 120 new prefiltered papers by default."""
    params: dict[str, Any] = {"offset": offset, "page_size": page_size}
    if date:
        params["through_date"] = date
    emit(client.request("GET", "/api/sources/arxiv/discover", params=params, timeout=660))


@task_app.command("candidate-pool")
def task_candidate_pool(task_id: str, data: str = typer.Option(..., help="JSON or @file")) -> None:
    """Save the roughly 100 papers retained after domain and interest screening."""
    emit(client.request("PUT", f"/api/tasks/{task_id}/candidate-pool", json=parse_json(data)))


@app.command("backup")
def backup() -> None:
    emit(client.request("POST", "/api/backup", timeout=180))


@bootstrap_app.command("bibtex")
def bootstrap_bibtex(path: Path) -> None:
    if not path.is_file():
        emit({"ok": False, "error": f"File not found: {path}"})
        raise typer.Exit(1)
    with path.open("rb") as file:
        emit(
            client.request(
                "POST",
                "/api/bootstrap/bibtex",
                files={"file": (path.name, file, "application/x-bibtex")},
                timeout=180,
            )
        )


if __name__ == "__main__":
    app()
