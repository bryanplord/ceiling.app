"""'Today' means the athlete's today, not the server's. Hosts run on UTC,
which flips to tomorrow at 5:00 PM Pacific."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import settings


def local_today() -> date:
    return datetime.now(ZoneInfo(settings().tz)).date()
