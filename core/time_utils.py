from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_now() -> datetime:
    return datetime.now(IST)


def ist_today() -> date:
    return ist_now().date()


def parse_user_datetime_ist(value: str) -> datetime:
    # Accept ISO input with/without timezone; interpret naive values as IST.
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def to_ist_iso(value: datetime) -> str:
    return value.astimezone(IST).isoformat(timespec="minutes")
