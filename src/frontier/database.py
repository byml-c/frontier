from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

settings.ensure_directories()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.resolved_database_url,
    connect_args={"check_same_thread": False}
    if settings.resolved_database_url.startswith("sqlite")
    else {},
)


if settings.resolved_database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_tables() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    run_schema_migrations()


def run_schema_migrations() -> None:
    """Apply small, idempotent schema changes after SQLAlchemy creates tables.

    Frontier intentionally keeps migrations in-process because the deployment is a
    single SQLite service. Each migration is recorded before the transaction closes,
    so restarting after an interrupted upgrade safely retries only unfinished work.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        if 1 not in applied:
            if connection.dialect.name == "sqlite":
                columns = {
                    str(row[1])
                    for row in connection.exec_driver_sql("PRAGMA table_info(papers)").fetchall()
                }
                additions = {
                    "topic_relevance_score": "FLOAT",
                    "author_influence_score": "FLOAT",
                    "screening_feedback": "JSON",
                }
                for name, column_type in additions.items():
                    if name not in columns:
                        connection.exec_driver_sql(
                            f"ALTER TABLE papers ADD COLUMN {name} {column_type}"
                        )
            else:
                connection.exec_driver_sql(
                    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS topic_relevance_score FLOAT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS author_influence_score FLOAT"
                )
                connection.exec_driver_sql(
                    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS screening_feedback JSON"
                )
            connection.exec_driver_sql(
                "INSERT INTO schema_migrations(version, applied_at) "
                "VALUES (1, CURRENT_TIMESTAMP)"
            )


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
