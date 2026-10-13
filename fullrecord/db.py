"""The Coach page's own tables, kept in the app's database beside Ceiling's.

Ceiling's tables are only ever read. These tables hold the history built once
from a Garmin export, and each day's suggestion and conversation.
"""
from __future__ import annotations

import json
import re
import time

# Each athlete in Ceiling has tables of their own (rx/store.py, Store.view). The
# Coach page's tables follow the same rule: for any athlete but the primary one,
# every fr_ table and index gets that athlete's prefix.
_FR = re.compile(r"\bfr_\w+")


class _Tables:
    def __init__(self, store):
        self._db = store.db
        owner = getattr(store, "owner", "") or ""
        self._p = f"u_{owner}__" if owner else ""

    def _q(self, sql):
        return _FR.sub(lambda m: self._p + m.group(0), sql) if self._p else sql

    def execute(self, sql, args=()):
        return self._db.execute(self._q(sql), args)

    def executescript(self, script):
        return self._db.executescript(self._q(script))

    def commit(self):
        return self._db.commit()


def _db(store):
    return _Tables(store)


def _one(store, sql, args=()):
    for row in _db(store).execute(sql, args):
        return row
    return None

SCHEMA = """
CREATE TABLE IF NOT EXISTS fr_sessions (
    id        TEXT PRIMARY KEY,   -- local start, yyyy-mm-dd-hh-mm-ss
    date      TEXT NOT NULL,
    source    TEXT NOT NULL,      -- export
    card      TEXT NOT NULL,      -- JSON
    segments  TEXT NOT NULL,      -- JSON
    blocks    TEXT NOT NULL       -- JSON, slimmed 30-second blocks
);
CREATE INDEX IF NOT EXISTS fr_sessions_date ON fr_sessions(date);

CREATE TABLE IF NOT EXISTS fr_nights (
    night     TEXT PRIMARY KEY,
    row       TEXT NOT NULL       -- JSON
);

CREATE TABLE IF NOT EXISTS fr_meta (
    key       TEXT PRIMARY KEY,
    value     TEXT
);

CREATE TABLE IF NOT EXISTS fr_days (
    day        TEXT PRIMARY KEY,  -- the day the suggestion is for
    suggestion TEXT,              -- JSON from Claude
    state      TEXT,              -- pending, ready, failed: <reason>
    model      TEXT,
    created    REAL
);

CREATE TABLE IF NOT EXISTS fr_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    day       TEXT NOT NULL,
    role      TEXT NOT NULL,      -- user or assistant
    content   TEXT NOT NULL,
    created   REAL
);
CREATE INDEX IF NOT EXISTS fr_messages_day ON fr_messages(day);
"""


def ensure(store) -> None:
    _db(store).executescript(SCHEMA)
    _db(store).commit()


def get_meta(store, key, default=None):
    r = _one(store, "SELECT value FROM fr_meta WHERE key=?", (key,))
    return json.loads(r["value"]) if r and r["value"] is not None else default


def set_meta(store, key, value) -> None:
    _db(store).execute("INSERT OR REPLACE INTO fr_meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    _db(store).commit()


def save_sessions(store, rows) -> None:
    for r in rows:
        _db(store).execute(
            "INSERT OR REPLACE INTO fr_sessions (id, date, source, card, segments, blocks) VALUES (?,?,?,?,?,?)",
            (r["id"], r["date"], r["source"], json.dumps(r["card"]), json.dumps(r["segments"]),
             json.dumps(r["blocks"])))
    _db(store).commit()


def sessions(store):
    out = []
    for r in _db(store).execute("SELECT id, date, source, card, segments, blocks FROM fr_sessions ORDER BY id"):
        out.append({"id": r["id"], "date": r["date"], "source": r["source"], "card": json.loads(r["card"]),
                    "segments": json.loads(r["segments"]), "blocks": json.loads(r["blocks"])})
    return out


def save_nights(store, rows) -> None:
    for n in rows:
        _db(store).execute("INSERT OR REPLACE INTO fr_nights (night, row) VALUES (?, ?)", (n["night"], json.dumps(n)))
    _db(store).commit()


def nights(store):
    return [json.loads(r["row"]) for r in _db(store).execute("SELECT row FROM fr_nights ORDER BY night")]


def counts(store):
    return {t: _one(store, f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("fr_sessions", "fr_nights")}


def day(store, d):
    r = _one(store, "SELECT * FROM fr_days WHERE day=?", (d,))
    if not r:
        return None
    return {"day": r["day"], "suggestion": json.loads(r["suggestion"]) if r["suggestion"] else None,
            "state": r["state"], "model": r["model"], "created": r["created"]}


def set_day(store, d, state, suggestion=None, model=None) -> None:
    _db(store).execute("INSERT OR REPLACE INTO fr_days (day, suggestion, state, model, created) VALUES (?,?,?,?,?)",
                     (d, json.dumps(suggestion) if suggestion is not None else None, state, model, time.time()))
    _db(store).commit()


def recent_days(store, before, limit=14):
    rows = _db(store).execute("SELECT * FROM fr_days WHERE day < ? AND state='ready' ORDER BY day DESC LIMIT ?",
                            (before, limit))
    return [{"day": r["day"], "suggestion": json.loads(r["suggestion"])} for r in rows]


def messages(store, d):
    return [{"role": r["role"], "content": r["content"]}
            for r in _db(store).execute("SELECT role, content FROM fr_messages WHERE day=? ORDER BY id", (d,))]


def add_message(store, d, role, content) -> None:
    _db(store).execute("INSERT INTO fr_messages (day, role, content, created) VALUES (?,?,?,?)",
                     (d, role, content, time.time()))
    _db(store).commit()
