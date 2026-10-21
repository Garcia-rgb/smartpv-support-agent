from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from support_agent.models import Conversation, Message
from support_agent.schemas import UtcDatetime


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 10, 15, 6, 12, 8),
        datetime(2026, 10, 15, 6, 12, 8, tzinfo=UTC),
        datetime(2026, 10, 15, 14, 12, 8, tzinfo=timezone(timedelta(hours=8))),
        "2026-10-15T06:12:08",
        "2026-10-15T14:12:08+08:00",
    ],
)
def test_api_timestamp_has_explicit_utc_zone_without_shifting_instant(value):
    adapter = TypeAdapter(UtcDatetime)
    parsed = adapter.validate_python(value)
    assert parsed == datetime(2026, 10, 15, 6, 12, 8, tzinfo=UTC)
    assert adapter.dump_json(parsed) == b'"2026-10-15T06:12:08Z"'


async def test_legacy_sqlite_sessions_and_messages_return_utc(client, db_session):
    raw = datetime(2026, 10, 15, 6, 12, 8)
    conversation = Conversation(user_id="tz-user", created_at=raw)
    db_session.add(conversation)
    await db_session.flush()
    session_id = conversation.id
    db_session.add(
        Message(
            session_id=session_id,
            role="user",
            content="test timezone",
            citations=[],
            created_at=raw,
        )
    )
    await db_session.commit()
    db_session.expunge_all()  # Exercise SQLite's actual timezone-naive read behavior.
    headers = {"X-User-Id": "tz-user"}
    sessions = await client.get("/sessions", headers=headers)
    assert sessions.status_code == 200
    assert sessions.json()["items"][0]["created_at"] == "2026-10-15T06:12:08Z"
    detail = await client.get(f"/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["created_at"] == "2026-10-15T06:12:08Z"
    assert detail.json()["messages"][0]["created_at"] == "2026-10-15T06:12:08Z"
    # Fix serialization only; preserve the timestamps and order stored in the database.
    persisted = await db_session.scalar(
        select(Conversation.created_at).where(Conversation.id == session_id)
    )
    assert persisted == raw
    created = await client.post("/sessions", headers=headers)
    assert created.status_code == 201
    assert created.json()["created_at"].endswith("Z")
