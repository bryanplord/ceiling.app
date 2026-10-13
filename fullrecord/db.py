"""The Coach page's own tables, kept in the app's database beside Ceiling's.

Ceiling's tables are only ever read. These tables hold the history built once
from a Garmin export, and each day's suggestion and conversation.
"""
from __future__ import annotations

import json
import time

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
    store.db.executescript(SCHEMA)
    store.db.commit()


def get_meta(store, key, default=None):
    r = store._one("SELECT value FROM fr_meta WHERE key=?", (key,))
    return json.loads(r["value"]) if r and r["value"] is not None else default


def set_meta(store, key, value) -> None:
    store.db.execute("INSERT OR REPLACE INTO fr_meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
    store.db.commit()


def save_sessions(store, rows) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR REPLACE INTO fr_sessions (id, date, source, card, segments, blocks) VALUES (?,?,?,?,?,?)",
            (r["id"], r["date"], r["source"], json.dumps(r["card"]), json.dumps(r["segments"]),
             json.dumps(r["blocks"])))
    store.db.commit()


def sessions(store):
    out = []
    for r in store.db.execute("SELECT id, date, source, card, segments, blocks FROM fr_sessions ORDER BY id"):
        out.append({"id": r["id"], "date": r["date"], "source": r["source"], "card": json.loads(r["card"]),
                    "segments": json.loads(r["segments"]), "blocks": json.loads(r["blocks"])})
    return out


def save_nights(store, rows) -> None:
    for n in rows:
        store.db.execute("INSERT OR REPLACE INTO fr_nights (night, row) VALUES (?, ?)", (n["night"], json.dumps(n)))
    store.db.commit()


def nights(store):
    return [json.loads(r["row"]) for r in store.db.execute("SELECT row FROM fr_nights ORDER BY night")]


def counts(store):
    return {t: store._one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("fr_sessions", "fr_nights")}


def day(store, d):
    r = store._one("SELECT * FROM fr_days WHERE day=?", (d,))
    if not r:
        return None
    return {"day": r["day"], "suggestion": json.loads(r["suggestion"]) if r["suggestion"] else None,
            "state": r["state"], "model": r["model"], "created": r["created"]}


def set_day(store, d, state, suggestion=None, model=None) -> None:
    store.db.execute("INSERT OR REPLACE INTO fr_days (day, suggestion, state, model, created) VALUES (?,?,?,?,?)",
                     (d, json.dumps(suggestion) if suggestion is not None else None, state, model, time.time()))
    store.db.commit()


def recent_days(store, before, limit=14):
    rows = store.db.execute("SELECT * FROM fr_days WHERE day < ? AND state='ready' ORDER BY day DESC LIMIT ?",
                            (before, limit))
    return [{"day": r["day"], "suggestion": json.loads(r["suggestion"])} for r in rows]


def messages(store, d):
    return [{"role": r["role"], "content": r["content"]}
            for r in store.db.execute("SELECT role, content FROM fr_messages WHERE day=? ORDER BY id", (d,))]


def add_message(store, d, role, content) -> None:
    store.db.execute("INSERT INTO fr_messages (day, role, content, created) VALUES (?,?,?,?)",
                     (d, role, content, time.time()))
    store.db.commit()
