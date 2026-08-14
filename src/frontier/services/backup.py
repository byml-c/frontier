from __future__ import annotations

import json
import subprocess
from datetime import datetime

from sqlalchemy import select

from ..config import settings
from ..database import SessionLocal
from ..models import (
    AppSetting,
    ArchitectureAsset,
    ArchitectureDocument,
    Brief,
    BriefItem,
    Memory,
    NodeProgress,
    OperationLog,
    Paper,
    PaperNode,
    PaperTag,
    ResearchEdge,
    ResearchIdea,
    ResearchNode,
    Tag,
)
from .common import model_dict

EXPORT_MODELS = {
    "architecture_documents": ArchitectureDocument,
    "architecture_assets": ArchitectureAsset,
    "papers": Paper,
    "research_nodes": ResearchNode,
    "research_edges": ResearchEdge,
    "paper_nodes": PaperNode,
    "node_progress": NodeProgress,
    "research_ideas": ResearchIdea,
    "briefs": Brief,
    "brief_items": BriefItem,
    "tags": Tag,
    "paper_tags": PaperTag,
    "memories": Memory,
    "settings": AppSetting,
    "operation_logs": OperationLog,
}


def create_local_git_backup() -> dict[str, str | int]:
    backup_root = settings.data_dir / "backups" / "git"
    backup_root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as session:
        for filename, model in EXPORT_MODELS.items():
            items = session.scalars(select(model)).all()
            path = backup_root / f"{filename}.json"
            path.write_text(
                json.dumps([model_dict(item) for item in items], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    if not (backup_root / ".git").exists():
        subprocess.run(["git", "init"], cwd=backup_root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Frontier Backup"],
            cwd=backup_root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "frontier@localhost"],
            cwd=backup_root,
            check=True,
        )
    subprocess.run(["git", "add", "--all"], cwd=backup_root, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=backup_root, check=False)
    committed = 0
    if diff.returncode != 0:
        message = f"Daily backup {datetime.now().astimezone().isoformat(timespec='seconds')}"
        subprocess.run(["git", "commit", "-m", message], cwd=backup_root, check=True)
        committed = 1
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=backup_root,
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"path": str(backup_root), "revision": revision, "committed": committed}
