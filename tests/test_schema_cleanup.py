from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, inspect

from frontier.database import Base
from frontier.models import Paper
from frontier.services.schema_cleanup import clean_paper, migrate_clean_schema


def legacy_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for version in range(1, 6):
            c.exec_driver_sql("INSERT INTO schema_migrations VALUES (?, 'old')", (version,))
        paper_ddl = c.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='papers'"
        ).scalar_one()
        c.exec_driver_sql("DROP TABLE papers")
        c.exec_driver_sql(paper_ddl.replace("search_aliases JSON NOT NULL", "search_aliases JSON"))
        for col, kind in [
            ("summary", "TEXT"),
            ("problem", "TEXT"),
            ("full_analysis", "JSON"),
            ("catalog_summary", "TEXT"),
            ("completion_summary", "TEXT"),
            ("relevance_score", "FLOAT"),
            ("research_score", "FLOAT"),
            ("model_relevance_score", "FLOAT"),
            ("is_interested", "BOOLEAN"),
            ("is_quality", "BOOLEAN"),
        ]:
            c.exec_driver_sql(f"ALTER TABLE papers ADD COLUMN {col} {kind}")
        c.exec_driver_sql("CREATE INDEX ix_papers_is_quality ON papers(is_quality)")
        c.exec_driver_sql(
            "INSERT INTO papers(id,title,normalized_title,source,status,authors,discovered_at,search_aliases,model_review_required,pending_feedback,summary,problem,full_analysis,catalog_summary,relevance_score,research_score,model_relevance_score,is_interested,is_quality,metadata_json,created_at,updated_at) VALUES ('p','Paper','paper','manual','completed','[]','2026-01-01',NULL,0,'{}','原有分析','研究问题','{\"claims\":[{\"claim\":\"证据 [[page:2]]\"}]}','旧读前简介',.623,.91,.743,1,0,'{\"completion_summary_structured\":{\"innovation\":\"创新\",\"user_attitude\":\"私有态度\"},\"bibliographic_provenance\":{\"source\":\"pdf\"},\"metadata_enrichment\":{\"status\":\"pending\"}}','2026-01-01','2026-01-01')"
        )
        c.exec_driver_sql(
            "INSERT INTO annotations(id,paper_id,kind,page_number,content,position_json,created_at,updated_at) VALUES ('n','p','note',2,'保留笔记','{}','2026-01-01','2026-01-01')"
        )
        c.exec_driver_sql(
            "CREATE TABLE daily_selections(selection_date TEXT,paper_id TEXT,created_at TEXT,updated_at TEXT)"
        )
        c.exec_driver_sql(
            "INSERT INTO daily_selections VALUES('2026-01-01','p','2026-01-01','2026-01-01')"
        )
        c.exec_driver_sql("ALTER TABLE briefs ADD COLUMN snapshot_json JSON")
        c.exec_driver_sql("ALTER TABLE brief_items ADD COLUMN snapshot_json JSON")
        c.exec_driver_sql("CREATE TABLE paper_narrations(id TEXT)")
        c.exec_driver_sql("CREATE TABLE podcast_episodes(id TEXT)")
    audio = tmp_path / "audio" / "podcasts"
    audio.mkdir(parents=True)
    (audio / "old.wav").write_bytes(b"audio")
    return engine


def test_cleanup_physically_removes_legacy_columns_tables_and_audio(tmp_path):
    engine = legacy_database(tmp_path)
    migrate_clean_schema(engine, tmp_path)
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_columns("papers")} == set(
        Paper.__table__.columns.keys()
    )
    assert not {
        "podcast_episodes",
        "paper_narrations",
        "daily_selections",
        "daily_review_revisions",
    } & set(inspector.get_table_names())
    assert "snapshot_json" not in {c["name"] for c in inspector.get_columns("briefs")}
    assert not (tmp_path / "audio").exists()
    with engine.connect() as c:
        p = c.exec_driver_sql("SELECT * FROM papers WHERE id='p'").mappings().one()
        assert "旧读前简介" in p["pre_read_summary"]
        assert all(
            text in p["post_read_summary"]
            for text in ("原有分析", "研究问题", "创新", "证据 [[page:2]]")
        )
        assert "私有态度" not in p["post_read_summary"]
        assert p["user_note"] == "私有态度"
        assert p["user_score"] is None
        assert json.loads(p["search_aliases"]) == []
        assert p["agent_score"] == 0.623 and p["model_score"] == 0.743
        assert json.loads(p["metadata_json"]) == {"bibliographic_provenance": {"source": "pdf"}}
        assert c.exec_driver_sql("SELECT COUNT(*) FROM annotations").scalar_one() == 1
        assert c.exec_driver_sql("SELECT COUNT(*) FROM daily_selection_items").scalar_one() == 1
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    migrate_clean_schema(engine, tmp_path)
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT COUNT(*) FROM papers").scalar_one() == 1
        assert c.exec_driver_sql("SELECT COUNT(*) FROM daily_selection_items").scalar_one() == 1


def test_failed_migration_rolls_back_schema_and_records(tmp_path):
    engine = legacy_database(tmp_path)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO annotations(id,paper_id,kind,page_number,position_json,created_at,updated_at) VALUES ('orphan','missing','note',1,'{}','2026-01-01','2026-01-01')"
        )
    with pytest.raises(RuntimeError, match="Foreign key check failed"):
        migrate_clean_schema(engine, tmp_path)
    assert "summary" in {c["name"] for c in inspect(engine).get_columns("papers")}
    assert "paper_narrations" in inspect(engine).get_table_names()
    assert (tmp_path / "audio").exists()
    with engine.connect() as c:
        assert (
            c.exec_driver_sql("SELECT COUNT(*) FROM schema_migrations WHERE version=6").scalar_one()
            == 0
        )


def test_migration_does_not_turn_boolean_or_unknown_into_a_score():
    row = clean_paper(
        {"is_interested": True, "is_quality": False, "status": "completed", "metadata_json": "{}"}
    )
    assert row["user_score"] is None
    assert row["agent_score"] is None
    assert row["model_score"] is None


def test_cleanup_preserves_schedule_and_converts_public_reviews(tmp_path):
    engine = legacy_database(tmp_path)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO app_settings(key,value_json,created_at,updated_at) VALUES ('daily_hour','7','2026-01-01','2026-01-01'), ('daily_minute','30','2026-01-01','2026-01-01')"
        )
        c.exec_driver_sql("ALTER TABLE daily_reviews ADD COLUMN confidence INTEGER")
        c.exec_driver_sql(
            "INSERT INTO daily_reviews(id,paper_id,device_id,score,content,confidence,created_at,updated_at) VALUES ('r','p','d',4,'原评论',5,'2026-01-01','2026-01-01')"
        )
        c.exec_driver_sql(
            "INSERT INTO daily_replies(id,review_id,device_id,content,created_at,updated_at) VALUES ('reply','r','d','原回复','2026-01-01','2026-01-01')"
        )
    migrate_clean_schema(engine, tmp_path)
    with engine.connect() as c:
        assert (
            json.loads(
                c.exec_driver_sql(
                    "SELECT value_json FROM app_settings WHERE key='daily_time'"
                ).scalar_one()
            )
            == "07:30"
        )
        assert (
            c.exec_driver_sql(
                "SELECT COUNT(*) FROM app_settings WHERE key IN ('daily_hour','daily_minute')"
            ).scalar_one()
            == 0
        )
        assert c.exec_driver_sql("SELECT score,content FROM daily_reviews").one() == (0.6, "原评论")
        assert c.exec_driver_sql("SELECT content FROM daily_replies").scalar_one() == "原回复"
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    assert "confidence" not in {c["name"] for c in inspect(engine).get_columns("daily_reviews")}
    # Filesystem cleanup is retried even when the schema marker is already committed.
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "leftover.wav").write_bytes(b"audio")
    migrate_clean_schema(engine, tmp_path)
    assert not audio.exists()


def test_flat_legacy_private_attitude_moves_to_user_note():
    result = clean_paper(
        {
            "completion_summary": "### 创新内容\n\n公开结论。\n\n### 用户态度\n\n我的私人想法。\n\n### 未解决问题\n\n公开问题。",
            "metadata_json": "{}",
        }
    )
    assert "公开结论" in result["post_read_summary"]
    assert "公开问题" in result["post_read_summary"]
    assert "我的私人想法" not in result["post_read_summary"]
    assert result["user_note"] == "我的私人想法。"
