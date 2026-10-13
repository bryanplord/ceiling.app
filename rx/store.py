"""SQLite store. One file locally; a Turso replica when deployed."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id          TEXT PRIMARY KEY,      -- source-prefixed: icu:i123, garmin:456
    source      TEXT NOT NULL,
    date        TEXT NOT NULL,         -- local yyyy-mm-dd
    start_local TEXT,
    sport       TEXT NOT NULL,         -- normalized: bike, run, walk, ski, other
    raw_type    TEXT,
    name        TEXT,
    moving_s    INTEGER,
    distance_m  REAL,
    avg_hr      REAL,
    max_hr      REAL,
    device      TEXT,
    raw         TEXT
);
CREATE INDEX IF NOT EXISTS activities_date ON activities(date);

CREATE TABLE IF NOT EXISTS streams (
    activity_id TEXT PRIMARY KEY,
    time        TEXT NOT NULL,         -- JSON array, seconds from start
    hr          TEXT NOT NULL,         -- JSON array, bpm (null allowed)
    cadence     TEXT,                  -- JSON array or null
    speed       TEXT                   -- JSON array, m/s, or null
);

CREATE TABLE IF NOT EXISTS wellness (
    date        TEXT NOT NULL,
    source      TEXT NOT NULL,
    hrv         REAL,                  -- overnight rMSSD, ms
    rhr         REAL,                  -- resting HR, bpm
    sleep_score REAL,                  -- 0-100
    sleep_secs  REAL,
    readiness   REAL,                  -- device readiness, stored but not trusted
    raw         TEXT,
    PRIMARY KEY (date, source)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# When several sources report the same day, the first non-null value wins.
WELLNESS_PRIORITY = ("garmin", "intervals")


@dataclass
class Activity:
    id: str
    source: str
    date: str
    sport: str
    moving_s: int
    start_local: str | None = None
    raw_type: str | None = None
    name: str | None = None
    distance_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    device: str | None = None


@dataclass
class Wellness:
    date: str
    hrv: float | None = None
    rhr: float | None = None
    sleep_score: float | None = None
    sleep_secs: float | None = None
    readiness: float | None = None


class _Row(tuple):
    """A result row readable by position or by column name, like sqlite3.Row."""

    def __new__(cls, values, names):
        r = super().__new__(cls, values)
        r._names = names
        return r

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._names[key])
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


class _Locked:
    """Wraps a Turso (libsql) connection: one shared connection per process, every
    statement run under a lock and its rows read out immediately, so web requests
    and the background refresh can share it safely."""

    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.RLock()

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            if not cur.description:
                return []
            names = {d[0]: i for i, d in enumerate(cur.description)}
            return [_Row(row, names) for row in cur.fetchall()]

    def executescript(self, script):
        with self.lock:
            self.conn.executescript(script)

    def commit(self):
        with self.lock:
            self.conn.commit()

    def sync(self):
        with self.lock:
            if hasattr(self.conn, "sync"):
                self.conn.sync()

    def close(self):
        self.conn.close()


class Store:
    """Local SQLite by default. With TURSO_DATABASE_URL set, a local replica of the
    hosted Turso database: reads are local and fast, writes go to Turso, so the data
    survives hosts that wipe their disk (e.g. Render's free tier)."""

    def __init__(self, path: Path | str, turso_url: str = "", turso_token: str = "",
                 backend: str = "sqlite"):
        self.path = str(path)
        self.remote = bool(turso_url)
        if turso_url or backend == "libsql":
            import libsql
            conn = (libsql.connect(self.path, sync_url=turso_url, auth_token=turso_token)
                    if turso_url else libsql.connect(self.path))
            self.db = _Locked(conn)
            if turso_url:
                self.db.sync()
        else:
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(streams)")}
        if "speed" not in cols:  # migrate databases created before speed was stored
            self.db.execute("ALTER TABLE streams ADD COLUMN speed TEXT")
            self.db.commit()

    def _one(self, sql: str, args: tuple = ()):
        for row in self.db.execute(sql, args):
            return row
        return None

    def sync(self) -> None:
        """Pull the latest from Turso into the local replica (no-op for plain SQLite)."""
        if self.remote:
            self.db.sync()

    def close(self) -> None:
        self.db.close()

    # -- writes ---------------------------------------------------------
    def upsert_activity(self, a: Activity, raw: dict | None = None) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO activities
            (id, source, date, start_local, sport, raw_type, name, moving_s,
             distance_m, avg_hr, max_hr, device, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a.id, a.source, a.date, a.start_local, a.sport, a.raw_type, a.name,
             a.moving_s, a.distance_m, a.avg_hr, a.max_hr, a.device,
             json.dumps(raw) if raw is not None else None),
        )
        self.db.commit()

    def upsert_stream(self, activity_id: str, time: list, hr: list,
                      cadence: list | None = None, speed: list | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO streams (activity_id, time, hr, cadence, speed) VALUES (?,?,?,?,?)",
            (activity_id, json.dumps(time), json.dumps(hr),
             json.dumps(cadence) if cadence else None,
             json.dumps(speed) if speed else None),
        )
        self.db.commit()

    def upsert_wellness(self, source: str, w: Wellness, raw: dict | None = None) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO wellness
            (date, source, hrv, rhr, sleep_score, sleep_secs, readiness, raw)
            VALUES (?,?,?,?,?,?,?,?)""",
            (w.date, source, w.hrv, w.rhr, w.sleep_score, w.sleep_secs,
             w.readiness, json.dumps(raw) if raw is not None else None),
        )
        self.db.commit()

    # -- reads ----------------------------------------------------------
    def activities(self, since: str | None = None) -> list[Activity]:
        q = "SELECT * FROM activities"
        args: tuple = ()
        if since:
            q += " WHERE date >= ?"
            args = (since,)
        q += " ORDER BY start_local, date"
        cols = Activity.__dataclass_fields__.keys()
        return [Activity(**{k: r[k] for k in cols}) for r in self.db.execute(q, args)]

    def activity(self, activity_id: str) -> Activity | None:
        r = self._one("SELECT * FROM activities WHERE id=?", (activity_id,))
        if not r:
            return None
        return Activity(**{k: r[k] for k in Activity.__dataclass_fields__.keys()})

    def has_stream(self, activity_id: str) -> bool:
        return self._one("SELECT 1 FROM streams WHERE activity_id=?", (activity_id,)) is not None

    def stream(self, activity_id: str) -> tuple[list, list, list | None] | None:
        r = self._one("SELECT * FROM streams WHERE activity_id=?", (activity_id,))
        if not r:
            return None
        return (json.loads(r["time"]), json.loads(r["hr"]),
                json.loads(r["cadence"]) if r["cadence"] else None)

    def get_meta(self, key: str) -> str | None:
        r = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
        self.db.commit()

    def speed(self, activity_id: str) -> list | None:
        r = self._one("SELECT speed FROM streams WHERE activity_id=?", (activity_id,))
        return json.loads(r["speed"]) if r and r["speed"] else None

    def wellness(self, since: str | None = None) -> list[Wellness]:
        """One merged row per day, by source priority."""
        q = "SELECT * FROM wellness"
        args: tuple = ()
        if since:
            q += " WHERE date >= ?"
            args = (since,)
        by_day: dict[str, dict[str, sqlite3.Row]] = {}
        for r in self.db.execute(q, args):
            by_day.setdefault(r["date"], {})[r["source"]] = r
        out = []
        for day in sorted(by_day):
            rows = [by_day[day][s] for s in WELLNESS_PRIORITY if s in by_day[day]]
            rows += [r for s, r in by_day[day].items() if s not in WELLNESS_PRIORITY]
            merged = Wellness(date=day)
            for f in ("hrv", "rhr", "sleep_score", "sleep_secs", "readiness"):
                setattr(merged, f, _first(r[f] for r in rows))
            out.append(merged)
        return out


def _first(values: Iterable):
    for v in values:
        if v is not None:
            return v
    return None
