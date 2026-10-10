from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import AppSetting, OperationLog
from frontier.services.common import (
    BRIEF_LIMIT_DEFAULT_MIGRATION_ACTION,
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
