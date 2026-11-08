from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from frontier.database import Base
from frontier.models import AppSetting
from frontier.services.common import DEFAULT_SETTINGS, ensure_default_settings


def test_default_settings_are_idempotent_and_preserve_user_time():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AppSetting(key="daily_time", value_json="07:30"))
        session.commit()
        ensure_default_settings(session)
        ensure_default_settings(session)
        assert set(session.scalars(select(AppSetting.key))) == set(DEFAULT_SETTINGS)
        assert session.get(AppSetting, "daily_time").value_json == "07:30"


def test_retired_settings_are_not_writable(client):
    for key in ("daily_hour", "daily_minute", "abstract_limit", "featured_limit", "fulltext_limit"):
        assert client.put(f"/api/settings/{key}", json={"value": 1}).status_code == 422
    assert client.put("/api/settings/daily_time", json={"value": "07:30"}).status_code == 200
