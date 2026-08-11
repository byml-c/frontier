from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ZoteroExportItem:
    """Stable, one-way payload reserved for a future Frontier -> Zotero adapter."""

    paper_id: str
    title: str
    authors: tuple[str, ...]
    source_url: str | None = None
    doi: str | None = None
    pdf_path: Path | None = None
    tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    annotations: tuple[dict, ...] = field(default_factory=tuple)


class ZoteroExporter(Protocol):
    """Implementations may only push confirmed library items out of Frontier."""

    def export(self, item: ZoteroExportItem) -> str:
        """Return the remote Zotero item key after an idempotent export."""
        ...
