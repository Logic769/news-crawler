"""Timestamp helpers: DB stores local time without an offset."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


_FALLBACK_OFFSET = dt.timezone(dt.timedelta(hours=8))


def resolve_timezone(name: str | None) -> dt.tzinfo:
    if not name:
        return _FALLBACK_OFFSET
    try:
        return ZoneInfo(name)
    except Exception:
        return _FALLBACK_OFFSET


def system_timezone() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def parse_db_time(value: str | None, tz_name: str) -> dt.datetime | None:
    """Parse a DB timestamp; naive values use the given IANA timezone."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    tz = resolve_timezone(tz_name)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def iso_or_none(value: str | None, tz_name: str) -> str | None:
    parsed = parse_db_time(value, tz_name)
    return parsed.isoformat() if parsed else None


def utc_iso_or_none(value: str | None, tz_name: str) -> str | None:
    parsed = parse_db_time(value, tz_name)
    if not parsed:
        return None
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def local_iso(value: str | None) -> str | None:
    """Parse first_seen_at/updated_at, which are written in server-local time."""
    if not value:
        return None
    raw = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=system_timezone())
    return parsed.isoformat()


def normalize_db_bound(value: str, tz_name: str, end_of_day: bool) -> str:
    """Convert an ISO date/datetime query bound to the DB's naive local format."""
    raw = value.strip()
    if not raw:
        raise ValueError("空时间")
    if len(raw) == 10:
        day = dt.date.fromisoformat(raw)
        return f"{day.isoformat()} {'23:59:59' if end_of_day else '00:00:00'}"
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(resolve_timezone(tz_name))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")
