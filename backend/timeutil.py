"""Date helpers shared by adapters.

Anime dates follow the Japanese broadcast convention used by しょぼいカレンダー:
JST, with the day boundary at 05:00. A show airing at 01:30 JST on Sunday
belongs to Saturday and is written "25:30".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
BROADCAST_DAY_START_HOUR = 5

DatePrecision = Literal["day", "month", "year"]


def broadcast_day(epoch_seconds: int) -> tuple[str, str]:
    """Returns (YYYY-MM-DD, "HH:MM") in broadcast-day notation for a UNIX time."""
    dt = datetime.fromtimestamp(epoch_seconds, tz=JST)
    if dt.hour < BROADCAST_DAY_START_HOUR:
        day = dt.date() - timedelta(days=1)
        hour = dt.hour + 24
    else:
        day = dt.date()
        hour = dt.hour
    return day.isoformat(), f"{hour:02d}:{dt.minute:02d}"


def jst_date(dt: datetime) -> str:
    """Calendar date in JST (no broadcast-day shift) for an aware datetime."""
    return dt.astimezone(JST).date().isoformat()


def jst_date_from_iso(value: str) -> Optional[str]:
    """JST calendar date for an ISO 8601 timestamp such as '2026-10-12T16:30:00Z'."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return jst_date(dt)


def partial_date(
    year: Optional[int], month: Optional[int] = None, day: Optional[int] = None
) -> Optional[tuple[str, DatePrecision]]:
    """Builds a sortable YYYY-MM-DD plus its real precision from partial parts."""
    if not year:
        return None
    if month and day:
        try:
            return date(year, month, day).isoformat(), "day"
        except ValueError:
            return None
    if month:
        if not 1 <= month <= 12:
            return None
        return f"{year:04d}-{month:02d}-01", "month"
    return f"{year:04d}-01-01", "year"


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()
