"""One-way migration to the current contract; no legacy readers/writers remain."""

from __future__ import annotations

import json
import math
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.schema import CreateIndex, CreateTable

VERSION = 6
SUMMARY_KEYS = {
    "summary",
    "problem",
    "innovation",
    "experiment",
    "limitations",
    "open_questions",
    "full_analysis",
    "completion_summary",
    "completion_summary_structured",
    "agent_comment",
    "catalog_summary",
    "pre_read_summary",
    "post_read_summary",
    "recommendation_reason",
}
RETIRED_KEYS = SUMMARY_KEYS | {"metadata_enrichment", "screening_feedback"}
LABELS = {
    "problem": "研究问题",
    "innovation": "方法与贡献",
    "experiment": "实验与证据",
    "limitations": "局限",
    "open_questions": "未解决问题",
    "claims": "论据",
    "method_components": "方法组件",
    "figure_table_index": "图表索引",
    "glossary": "术语",
    "unresolved_questions": "未解决问题",
    "summary": "概述",
    "agent_comment": "评述",
    "recommendation_reason": "推荐理由",
}


def obj(value, default):
    if isinstance(value, type(default)):
        return value
    try:
        decoded = json.loads(value) if value else default
        return decoded if isinstance(decoded, type(default)) else default
    except (TypeError, ValueError):
        return default


def markdown(value, depth=2):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n\n".join(markdown(item, depth) for item in value if item)
    if isinstance(value, dict):
        return "\n\n".join(
            f"{'#' * min(depth, 6)} {LABELS.get(k, k)}\n\n{markdown(v, depth + 1)}"
            for k, v in value.items()
            if v and k not in {"schema_version", "source_pdf_version", "user_attitude"}
        )
    return str(value) if value is not None else ""


def merge_markdown(*values):
    seen = set()
    parts = []
    for value in values:
        text = markdown(value)
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n\n".join(parts) or None


def score(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
        else None
    )


def split_legacy_private_sections(value: str | None) -> tuple[str | None, str | None]:
    public, private = [], []
    private_depth = None
    for line in (value or "").splitlines():
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            depth = len(heading[1])
            if any(
                label in heading[2]
                for label in ("用户态度", "用户评价", "用户判断", "个人态度", "user_attitude")
            ):
                private_depth = depth
                continue
            if private_depth is not None and depth <= private_depth:
                private_depth = None
        (private if private_depth is not None else public).append(line)
    return "\n".join(public).strip() or None, "\n".join(private).strip() or None


def clean_paper(row: dict) -> dict:
    result = dict(row)
    metadata = obj(row.get("metadata_json"), {})
    structured = obj(metadata.get("completion_summary_structured"), {})
    analysis = obj(row.get("full_analysis"), {})
    completion, private_attitude = split_legacy_private_sections(row.get("completion_summary"))
    detailed = (
        any(
            row.get(k)
            for k in ("problem", "innovation", "experiment", "limitations", "completion_summary")
        )
        or bool(analysis)
        or bool(structured)
    )
    sections = {
        k: row.get(k) or metadata.get(k)
        for k in ("problem", "innovation", "experiment", "limitations", "open_questions")
    }
    result["pre_read_summary"] = merge_markdown(
        row.get("pre_read_summary") or row.get("catalog_summary"),
        None if detailed else row.get("summary"),
        row.get("agent_comment"),
        row.get("recommendation_reason"),
    )
    result["post_read_summary"] = merge_markdown(
        row.get("post_read_summary"),
        completion if not structured else None,
        structured,
        row.get("summary") if detailed else None,
        sections,
        analysis,
    )
    # Multi-dimensional historical user judgments have no unique overall meaning.
    # Keep only an explicit overall score; never infer one from booleans/status.
    result["user_score"] = score(row.get("user_score"))
    result["agent_score"] = (
        score(row.get("agent_score"))
        if row.get("agent_score") is not None
        else score(row.get("relevance_score"))
    )
    result["model_score"] = (
        score(row.get("model_score"))
        if row.get("model_score") is not None
        else score(row.get("model_relevance_score"))
    )
    result["model_confidence"] = (
        score(row.get("model_confidence"))
        if row.get("model_confidence") is not None
        else score(row.get("model_relevance_confidence"))
    )
    screening = obj(row.get("screening_feedback"), {})
    result["user_note"] = merge_markdown(
        row.get("user_note"),
        screening.get("comment"),
        metadata.get("personal_comment"),
        structured.get("user_attitude"),
        private_attitude,
        analysis.get("user_attitude"),
    )
    metadata = {k: v for k, v in metadata.items() if k not in RETIRED_KEYS | {"personal_comment"}}
    for key in ("feedback_provenance", "ignore_provenance"):
        if isinstance(metadata.get(key), dict):
            metadata[key] = {
                k: v
                for k, v in metadata[key].items()
                if k
                not in {
                    "interest_level",
                    "quality_level",
                    "interested",
                    "quality",
                    "interest_score",
                    "quality_score",
                    "payload_hash",
                    "payload_hash_version",
                }
            }
    if isinstance(metadata.get("interest_model"), dict):
        metadata["interest_model"] = {
            k: v
            for k, v in metadata["interest_model"].items()
            if k not in {"version", "score", "confidence", "review_required"}
        }
    result["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    result["pending_feedback"] = json.dumps(
        {
            k: v
            for k, v in obj(row.get("pending_feedback"), {}).items()
            if k in {"suggested_score", "suggested_tags", "suggested_nodes"}
        }
    )
    return result


def _migrate_clean_database(engine: Engine) -> None:
    from ..models import DailyReview, Paper

    if engine.dialect.name != "sqlite":
        raise RuntimeError("Current cleanup migration requires SQLite")
    raw = engine.raw_connection()
    connection = raw.driver_connection
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        applied = {r[0] for r in connection.execute("SELECT version FROM schema_migrations")}
        if VERSION in applied:
            connection.rollback()
            return
        cursor = connection.execute("SELECT * FROM papers")
        keys = [d[0] for d in cursor.description]
        rows = [clean_paper(dict(zip(keys, row, strict=True))) for row in cursor]
        ddl = str(CreateTable(Paper.__table__).compile(engine)).replace(
            "CREATE TABLE papers", "CREATE TABLE papers_clean", 1
        )
        connection.execute(ddl)
        columns = [c.name for c in Paper.__table__.columns]
        now = datetime.now(UTC).isoformat()
        defaults = {
            "created_at": now,
            "updated_at": now,
            "discovered_at": now,
            "metadata_json": "{}",
            "pending_feedback": "{}",
            "authors": "[]",
            "search_aliases": "[]",
            "model_review_required": False,
            "status": "candidate",
            "source": "manual",
        }
        placeholders = ",".join("?" for _ in columns)
        sql = f"INSERT INTO papers_clean ({','.join(columns)}) VALUES ({placeholders})"
        for row in rows:
            connection.execute(
                sql, [row.get(c) if row.get(c) is not None else defaults.get(c) for c in columns]
            )
        connection.execute("DROP TABLE papers")
        connection.execute("ALTER TABLE papers_clean RENAME TO papers")
        for index in Paper.__table__.indexes:
            connection.execute(str(CreateIndex(index).compile(engine)))
        tables = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        # Preserve current selection membership, then retire the single-selection schema.
        if "daily_selections" in tables:
            for day, paper_id, created, updated in connection.execute(
                "SELECT selection_date,paper_id,created_at,updated_at FROM daily_selections"
            ).fetchall():
                rank = connection.execute(
                    "SELECT COALESCE(MAX(rank),0)+1 FROM daily_selection_items WHERE selection_date=?",
                    (day,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT OR IGNORE INTO daily_selection_items(id,selection_date,paper_id,rank,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), day, paper_id, rank, created, updated),
                )
            connection.execute("DROP TABLE daily_selections")
        review_fields = list(connection.execute("PRAGMA table_info(daily_reviews)"))
        if any(r[1] == "confidence" for r in review_fields):
            cur = connection.execute("SELECT * FROM daily_reviews")
            review_keys = [d[0] for d in cur.description]
            reviews = [dict(zip(review_keys, r, strict=True)) for r in cur.fetchall()]
            ddl = str(CreateTable(DailyReview.__table__).compile(engine)).replace(
                "CREATE TABLE daily_reviews", "CREATE TABLE daily_reviews_clean", 1
            )
            connection.execute(ddl)
            review_columns = [c.name for c in DailyReview.__table__.columns]
            for r in reviews:
                r["score"] = min(1, max(0, (r["score"] - 1) / 5))
                connection.execute(
                    f"INSERT INTO daily_reviews_clean ({','.join(review_columns)}) VALUES ({','.join('?' for _ in review_columns)})",
                    [r[c] for c in review_columns],
                )
            connection.execute("DROP TABLE daily_reviews")
            connection.execute("ALTER TABLE daily_reviews_clean RENAME TO daily_reviews")
            for index in DailyReview.__table__.indexes:
                connection.execute(str(CreateIndex(index).compile(engine)))
        connection.execute("DROP TABLE IF EXISTS daily_review_revisions")
        for table in ("paper_narrations", "podcast_episodes"):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        for table in ("briefs", "brief_items"):
            fields = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
            if table == "briefs" and "interest_context_digest" not in fields:
                connection.execute(
                    "ALTER TABLE briefs ADD COLUMN interest_context_digest VARCHAR(80)"
                )
            if "snapshot_json" in fields:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN snapshot_json")
        connection.execute(
            "DELETE FROM task_runs WHERE kind IN ('podcast_episode_render','paper_metadata_enrichment')"
        )
        # Preserve the configured time once, then remove obsolete split settings.
        settings_rows = dict(connection.execute("SELECT key,value_json FROM app_settings"))
        if (
            "daily_time" not in settings_rows
            and {"daily_hour", "daily_minute"} <= settings_rows.keys()
        ):
            hour = obj(settings_rows["daily_hour"], 0)
            minute = obj(settings_rows["daily_minute"], 0)
            if type(hour) is int and type(minute) is int and 0 <= hour <= 23 and 0 <= minute <= 59:
                connection.execute(
                    "INSERT INTO app_settings(key,value_json,created_at,updated_at) VALUES (?,?,?,?)",
                    ("daily_time", json.dumps(f"{hour:02d}:{minute:02d}"), now, now),
                )
        connection.execute(
            "DELETE FROM app_settings WHERE key IN ('daily_hour','daily_minute','fulltext_limit','featured_limit','abstract_limit','discovery_scan_limit','discovery_prefilter_limit')"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (VERSION, now))
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key check failed after cleanup: {violations[:5]}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        raw.close()


def migrate_clean_schema(engine: Engine, data_dir: Path) -> None:
    _migrate_clean_database(engine)
    # Retry filesystem cleanup even if the transactional schema step was already applied.
    # Report permission/disk errors instead of silently retaining retired audio.
    try:
        shutil.rmtree(data_dir / "audio")
    except FileNotFoundError:
        pass
