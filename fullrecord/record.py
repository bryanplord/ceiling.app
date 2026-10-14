"""The whole record as text: the history built from the export plus every
session since. Rebuilt only when the data changes.

The sessions since the export come from one of two places. By default they are
Ceiling's own intervals.icu pull. An athlete whose source is set to Garmin
(garmin_live.py does that on its first pull) gets them straight from Garmin
Connect instead, already in the Coach page's own tables, and Ceiling's
intervals.icu sessions and nightly figures are left out for them."""
from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .catalogue import assemble
from .store_source import garmin_nightly_lines, store_sessions

_cache: dict = {}
_lock = threading.Lock()


def version(store) -> tuple:
    c = store.counts()
    fc = db.counts(store)
    return (getattr(store, "owner", ""), c["activities"], c["streams"], c["wellness"], fc["fr_sessions"],
            fc["fr_nights"], db.get_meta(store, "ingested"), db.get_meta(store, "source"))


def build(store, tz_name, today):
    """(text, meta) for the record as of `today`."""
    key = (version(store), today.isoformat())
    with _lock:
        if key in _cache:
            return _cache[key]
    tz = ZoneInfo(tz_name)
    caps = db.get_meta(store, "caps", {}) or {}
    export_end = db.get_meta(store, "export_end")
    from_garmin = db.get_meta(store, "source") == "garmin"
    rows = db.sessions(store)
    if not from_garmin:
        have = {r["id"] for r in rows}
        rows += [r for r in store_sessions(store, export_end, tz, caps) if r["id"] not in have]
    night_rows = db.nights(store)
    text, meta = assemble(rows, caps, night_rows, today, datetime.now(tz).isoformat(timespec="minutes"),
                          extra_lines=[] if from_garmin else garmin_nightly_lines(store, export_end))
    meta["export_end"] = export_end
    with _lock:
        _cache.clear()
        _cache[key] = (text, meta)
    return text, meta
