from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.bparser import BibTexParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaperNode, ResearchEdge, ResearchNode
from ..schemas import PaperCreate
from .common import parse_arxiv_id
from .ingest import create_or_update_paper

SEED_NODES = [
    {
        "name": "VLA",
        "core_question": "如何让机器人把视觉与语言理解转化为可泛化的动作策略？",
        "domain": "VLA",
    },
    {
        "name": "世界-动作模型（WAM）",
        "core_question": "如何联合建模环境动态与动作后果，以支持机器人预测和决策？",
        "domain": "WAM",
    },
    {
        "name": "移动操作",
        "core_question": "如何让移动机器人在大范围环境中稳定完成导航与操作的联合任务？",
        "domain": "移动操作",
    },
    {
        "name": "机器人触觉",
        "core_question": "如何获取、表征并利用接触信息提升机器人操作能力？",
        "domain": "触觉",
    },
    {
        "name": "具身仿真",
        "core_question": "如何高效、可信地模拟机器人、传感器和接触交互？",
        "domain": "仿真",
    },
    {
        "name": "具身智能评测",
        "core_question": "如何可复现地衡量具身模型的能力、泛化与可靠性？",
        "domain": "评测",
    },
    {
        "name": "机器人遥操作",
        "core_question": "如何低成本、高保真地采集并传递人类操作行为？",
        "domain": "遥操",
    },
    {
        "name": "3D 视觉的具身应用",
        "core_question": "如何利用三维表示、重建与理解能力改善具身感知和行动？",
        "domain": "3DV",
    },
    {
        "name": "触觉增强 VLA 精细操作",
        "core_question": "如何将触觉引入 VLA，以提升接触丰富任务中的精细操作能力？",
        "domain": "VLA × 触觉",
    },
]

SEED_EDGES = [
    ("VLA", "触觉增强 VLA 精细操作"),
    ("机器人触觉", "触觉增强 VLA 精细操作"),
]

TOPIC_RULES: dict[str, tuple[str, ...]] = {
    "VLA": ("vision-language-action", "vision language action", "vla", "robot control"),
    "世界-动作模型（WAM）": ("world model", "world action model", "predictive model"),
    "移动操作": ("mobile manipulation", "navigation and manipulation", "whole-body"),
    "机器人触觉": (
        "tactile",
        "haptic",
        "visuotactile",
        "touch",
        "contact-rich",
        "force",
    ),
    "具身仿真": ("simulation", "sim2real", "sim-to-real", "digital twin", "simulator"),
    "具身智能评测": ("benchmark", "evaluation", "dataset", "generalization"),
    "机器人遥操作": ("teleoperation", "telepresence", "demonstration collection"),
    "3D 视觉的具身应用": (
        "3d gaussian",
        "gaussian splatting",
        "3d vision",
        "reconstruction",
        "point cloud",
        "nerf",
    ),
    "触觉增强 VLA 精细操作": ("visual-tactile", "vision-touch", "tactile language action"),
}


def ensure_seed_graph(session: Session) -> dict[str, ResearchNode]:
    nodes: dict[str, ResearchNode] = {}
    for index, payload in enumerate(SEED_NODES):
        node = session.scalar(select(ResearchNode).where(ResearchNode.name == payload["name"]))
        if node is None:
            node = ResearchNode(
                **payload,
                summary="等待 Agent 根据种子论文和新论文增量完善。",
                open_questions="等待初始化分析。",
                layout_x=float((index % 3) * 260),
                layout_y=float((index // 3) * 180),
            )
            session.add(node)
            session.flush()
        nodes[node.name] = node
    for source_name, target_name in SEED_EDGES:
        source = nodes[source_name]
        target = nodes[target_name]
        existing = session.scalar(
            select(ResearchEdge).where(
                ResearchEdge.source_id == source.id,
                ResearchEdge.target_id == target.id,
            )
        )
        if existing is None:
            session.add(ResearchEdge(source_id=source.id, target_id=target.id))
    session.commit()
    return nodes


def _clean_latex(value: str | None) -> str | None:
    if not value:
        return None
    result = value.replace("{{", "").replace("}}", "").replace("{", "").replace("}", "")
    return re.sub(r"\\[A-Za-z]+", "", result).strip()


def _entry_topics(entry: dict[str, Any]) -> list[str]:
    haystack = " ".join(
        str(entry.get(field, "")) for field in ("title", "abstract", "keywords", "note")
    ).lower()
    return [name for name, terms in TOPIC_RULES.items() if any(term in haystack for term in terms)]


def import_bibtex(session: Session, content: str) -> dict[str, Any]:
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    database = bibtexparser.loads(content, parser=parser)
    nodes = ensure_seed_graph(session)
    imported = 0
    skipped = 0
    linked = 0
    paper_ids: list[str] = []
    for entry in database.entries:
        topics = _entry_topics(entry)
        if not topics:
            skipped += 1
            continue
        title = _clean_latex(entry.get("title"))
        if not title:
            skipped += 1
            continue
        author_names = [
            " ".join(reversed([part.strip() for part in item.split(",", 1)]))
            if "," in item
            else item.strip()
            for item in entry.get("author", "").split(" and ")
            if item.strip()
        ]
        url = entry.get("url")
        arxiv_value = entry.get("eprint") or url or ""
        parsed = parse_arxiv_id(arxiv_value)
        year_match = re.search(r"\d{4}", entry.get("date", "") or entry.get("year", ""))
        published = None
        if year_match:
            from datetime import date

            published = date(int(year_match.group()), 1, 1)
        paper, was_created = create_or_update_paper(
            session,
            PaperCreate(
                title=title,
                abstract=_clean_latex(entry.get("abstract")),
                authors=[{"name": name} for name in author_names],
                first_author=author_names[0] if author_names else None,
                source="bibtex",
                source_url=url,
                pdf_url=f"https://arxiv.org/pdf/{parsed[0]}" if parsed else None,
                arxiv_id=parsed[0] if parsed else None,
                arxiv_version=parsed[1] or 1 if parsed else None,
                doi=entry.get("doi"),
                published_at=published,
                metadata_json={
                    "bibtex_key": entry.get("ID"),
                    "user_note": entry.get("note"),
                    "entry_type": entry.get("ENTRYTYPE"),
                },
            ),
            actor="bootstrap",
        )
        imported += int(was_created)
        paper_ids.append(paper.id)
        for topic in topics:
            node = nodes[topic]
            existing = session.scalar(
                select(PaperNode).where(
                    PaperNode.paper_id == paper.id, PaperNode.node_id == node.id
                )
            )
            if existing is None:
                session.add(
                    PaperNode(
                        paper_id=paper.id,
                        node_id=node.id,
                        reason="BibTeX 初始化关键词关联，等待 Agent 复核。",
                    )
                )
                linked += 1
        session.commit()
    return {
        "entries": len(database.entries),
        "imported": imported,
        "relevant": len(paper_ids),
        "skipped": skipped,
        "links": linked,
        "paper_ids": paper_ids,
        "node_ids": {name: node.id for name, node in nodes.items()},
    }


def import_bibtex_file(session: Session, path: Path) -> dict[str, Any]:
    return import_bibtex(session, path.read_text(encoding="utf-8"))
