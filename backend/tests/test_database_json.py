from datetime import UTC, date, datetime

from sqlalchemy import JSON, bindparam, select

from app.database import engine


def test_application_engine_serializes_datetime_and_date_json_values():
    payload = {
        "timestamp": datetime(2026, 10, 9, 2, 30, tzinfo=UTC),
        "nested": [{"trade_date": date(2026, 10, 9)}],
        "values": [None, True, 42, "unchanged"],
    }

    with engine.connect() as connection:
        stored = connection.execute(select(bindparam("payload", type_=JSON)), {"payload": payload}).scalar_one()

    assert stored == {
        "timestamp": "2026-10-09 02:30:00+00:00",
        "nested": [{"trade_date": "2026-10-09"}],
        "values": [None, True, 42, "unchanged"],
    }
