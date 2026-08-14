from datetime import datetime
from zoneinfo import ZoneInfo

from frontier.services.research_day import research_date_at


def test_research_day_rolls_over_at_daily_update_time() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    assert research_date_at(datetime(2026, 8, 14, 4, 59, tzinfo=zone)).isoformat() == (
        "2026-08-13"
    )
    assert research_date_at(datetime(2026, 8, 14, 5, 0, tzinfo=zone)).isoformat() == (
        "2026-08-14"
    )


def test_research_day_respects_a_configured_rollover() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    assert research_date_at(
        datetime(2026, 8, 14, 6, 29, tzinfo=zone),
        rollover_hour=6,
        rollover_minute=30,
    ).isoformat() == "2026-08-13"
    assert research_date_at(
        datetime(2026, 8, 14, 6, 30, tzinfo=zone),
        rollover_hour=6,
        rollover_minute=30,
    ).isoformat() == "2026-08-14"
