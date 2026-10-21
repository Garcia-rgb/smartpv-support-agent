"""Preserve the UTC meaning of timestamps read from timezone-naive SQLite columns."""

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    # Application timestamps have always been written with models.utcnow().
    # SQLite drops tzinfo on reads; PostgreSQL may already return aware values.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
