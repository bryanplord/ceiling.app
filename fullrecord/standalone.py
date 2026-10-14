"""The Coach record without Ceiling: a small SQLite database of its own, filled
from a Garmin export and then straight from Garmin Connect, with the whole
record written out as text for a Claude conversation to read.

    python -m fullrecord.standalone init EXPORT     once: the history from a Garmin export
    python -m fullrecord.standalone pull            new days from Garmin Connect, then the record
    python -m fullrecord.standalone record          write the record again without pulling

The Garmin login comes first (python -m fullrecord.garmin_live login).

Where things are kept:
    FULLRECORD_HOME (default ~/.local/share/fullrecord/standalone)/coach.db
    FULLRECORD_OUT  (default FULLRECORD_HOME/out), which receives:
        record.md         the whole record, as the Coach page would give it to Claude
        instructions.md   the coaching instructions, copied beside it
        status.json       when the record was written, the last pull, and any error
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, garmin_live, ingest, record


class Store:
    """The few parts of Ceiling's store the Coach record uses, over a plain
    SQLite file. There are no Ceiling tables, so its counts are all zero."""

    owner = ""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def counts(self):
        return {"activities": 0, "streams": 0, "wellness": 0}

    def activities(self, since=None):
        return []

    def wellness(self, since=None):
        return []

    def sync(self):
        self.db.commit()


def home() -> Path:
    return Path(os.getenv("FULLRECORD_HOME") or "~/.local/share/fullrecord/standalone").expanduser()


def out_dir() -> Path:
    return Path(os.getenv("FULLRECORD_OUT") or str(home() / "out")).expanduser()


def open_store() -> Store:
    s = Store(home() / "coach.db")
    db.ensure(s)
    if db.get_meta(s, "source") is None:
        db.set_meta(s, "source", "garmin")   # new days here only ever come from Garmin Connect
    return s


def write_record(store, tz_name: str) -> Path:
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    text, meta = record.build(store, tz_name, today)
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "record.md.tmp"
    tmp.write_text(text)
    tmp.replace(out / "record.md")   # a reader never sees half a file
    shutil.copyfile(Path(__file__).parent / "instructions.md", out / "instructions.md")
    status = {
        "written": datetime.now(tz).isoformat(timespec="minutes"),
        "sessions": meta.get("sessions"),
        "nights": meta.get("nights"),
        "export_end": meta.get("export_end"),
        "source": db.get_meta(store, "source"),
        "garmin_last_pull": db.get_meta(store, "garmin_last_pull"),
        "garmin_last_error": db.get_meta(store, "garmin_last_error"),
        "characters": len(text),
    }
    (out / "status.json").write_text(json.dumps(status, indent=1))
    return out / "record.md"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["init", "pull", "record"])
    ap.add_argument("export", nargs="?", help="for init: the Garmin export zip or folder")
    ap.add_argument("--tz", default=os.getenv("RX_TZ", "America/Los_Angeles"))
    args = ap.parse_args(argv)
    store = open_store()
    if args.command == "init":
        if not args.export:
            ap.error("init needs the path of a Garmin export")
        ingest.into(store, args.export, ZoneInfo(args.tz))
    elif args.command == "pull":
        try:
            garmin_live.pull(store, "", args.tz)
        except Exception as e:
            db.set_meta(store, "garmin_last_error",
                        f"{datetime.now().isoformat(timespec='minutes')} {type(e).__name__}: {str(e)[:300]}")
            store.sync()
            print(f"Garmin pull failed: {type(e).__name__}: {e}", file=sys.stderr)
            write_record(store, args.tz)   # the record still says when the last good pull was
            return 1
    path = write_record(store, args.tz)
    print(f"record written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
