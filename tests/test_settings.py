from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import AppSetting, OperationLog
from frontier.services.common import (
    BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION,
    DAILY_TIME_MIGRATION_ACTION,
    ensure_default_settings,
)


def test_old_brief_default_migrates_once_without_overwriting_later_choice() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(AppSetting(key="brief_limit", value_json=5))
        session.commit()

        ensure_default_settings(session)
        assert session.get(AppSetting, "brief_limit").value_json == 50
        assert session.scalar(
            select(OperationLog.id).where(
                OperationLog.action == BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION
            )
        )

        session.get(AppSetting, "brief_limit").value_json = 5
        session.commit()
        ensure_default_settings(session)
        assert session.get(AppSetting, "brief_limit").value_json == 5


def test_legacy_daily_time_migrates_to_0905_and_preserves_custom_time() -> None:
    for hour, minute, expected in [(5, 0, "09:05"), (7, 30, "07:30")]:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add_all(
                [
                    AppSetting(key="daily_hour", value_json=hour),
                    AppSetting(key="daily_minute", value_json=minute),
                ]
            )
            session.commit()
            ensure_default_settings(session)
            assert session.get(AppSetting, "daily_time").value_json == expected
            assert session.scalar(
                select(OperationLog.id).where(OperationLog.action == DAILY_TIME_MIGRATION_ACTION)
            )
        engine.dispose()
