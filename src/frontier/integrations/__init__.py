"""Reserved integration boundaries; no external sync is enabled in the MVP."""

from .zotero import ZoteroExporter, ZoteroExportItem

__all__ = ["ZoteroExportItem", "ZoteroExporter"]
