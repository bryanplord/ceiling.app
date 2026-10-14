"""The Coach record without Ceiling: a small SQLite database of its own, filled
from a Garmin export and then straight from Garmin Connect, with the whole
record written out as text for a Claude conversation to read.

    python -m fullrecord.standalone init EXPORT     once: the history from a Garmin export
    python -m fullrecord.standalone pull            new days from Garmin Connect, then the record
    python -m fullrecord.standalone record          write the record again without pulling

The Garmin login comes first (python -m fullrecord.garmin_live login).

Where things are kept:
    FULLRECORD_HOME (default ~/.local/share/fullrecord/standalone)/coach.db
    FULLRECORD_NOTES (default FULLRECORD_HOME/notes.md), the athlete's own notes, one line each:
                    "- YYYY-MM-DD HH:MM | the note"; they go into the record beside the night that followed
    FULLRECORD_OUT  (default FULLRECORD_HOME/out), which receives:
        record.md         the whole record, as the Coach page would give it to Claude (read every morning)
        record-condensed.md  the same record with every value kept and the labels stated once (for the conversation)
        lookup/           the full record by month, an index of each date, and every flagged reading
        instructions.md   the coaching instructions, copied beside it
        status.json       when the record was written, the last pull, and any error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db, garmin_live, ingest, layers, record


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


def notes_path() -> Path:
    return Path(os.getenv("FULLRECORD_NOTES") or str(home() / "notes.md")).expanduser()


def _notes():
    """(date, time, text) for each note line, in the order written."""
    out = []
    p = notes_path()
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line.startswith("- ") or "|" not in line:
            continue
        when, text = (x.strip() for x in line[2:].split("|", 1))
        parts = when.split()
        if parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
            out.append((parts[0], parts[1] if len(parts) > 1 else "", text))
    return out


def _clock(hhmm):
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")
    except ValueError:
        return hhmm


def notes_section(store) -> list[str]:
    """The athlete's notes, each beside the night that followed it (the night
    dated by the morning after). Their words are kept exactly."""
    notes = _notes()
    if not notes:
        return []
    nights = {n["night"]: n for n in db.nights(store)}
    L = ["## Notes the athlete logged",
         "In the athlete's own words, with the date and time they gave. Beside each is the night "
         "that followed, from the raw overnight measurements, when the watch recorded it."]
    for d, t, text in notes:
        line = f"- {d} {_clock(t)} | {text}".rstrip()
        after = (datetime.fromisoformat(d) + timedelta(days=1)).date().isoformat()
        n = nights.get(after)
        if n:
            bits = [f"night ending {after}: HR mean {n.get('hr_mean')}, low 30-min {n.get('hr_low_30min')}"]
            if n.get("hrv_mean") is not None:
                bits.append(f"HRV mean {n.get('hrv_mean')}, median {n.get('hrv_median')}")
            if n.get("breaths_mean") is not None:
                bits.append(f"breaths {n.get('breaths_mean')}")
            line += " | " + ", ".join(bits)
        else:
            line += f" | no night recorded ending {after}"
        L.append(line)
    return L


def _rmssd(rr):
    """RMSSD in ms from beat-to-beat intervals in seconds, after leaving out
    intervals outside 0.3 to 2.0 s and any that differ from the one before by
    more than 20% (a missed or extra beat)."""
    clean = [x for x in rr if 0.3 <= x <= 2.0]
    good = [clean[0]] if clean else []
    for a, b in zip(clean, clean[1:]):
        if abs(b - a) <= 0.2 * a:
            good.append(b)
    if len(good) < 20:
        return None, len(good)
    d = [(b - a) * 1000 for a, b in zip(good, good[1:])]
    return round((sum(x * x for x in d) / len(d)) ** 0.5), len(good)


def snapshots_section(tz_name: str) -> list[str]:
    """Health Snapshots, the athlete's two-minute seated recordings, with HRV
    worked out here from the beat-to-beat intervals the watch saved."""
    from garmin_fit_sdk import Decoder, Stream
    tz = ZoneInfo(tz_name)
    rows = []
    # Garmin has no separate download for snapshots; a recording with beat-to-beat
    # intervals arrives among the day's wellness files, so look for those intervals.
    for f in sorted((garmin_live.base_dir("") / "wellness").glob("*/*.fit")):
        try:
            m, _ = Decoder(Stream.from_file(str(f))).read()
        except Exception:
            continue
        rr = [x for msg in m.get("hrv_mesgs", []) for x in (msg.get("time") or []) if x is not None]
        if not rr:
            continue
        ts = None
        for key in ("session_mesgs", "file_id_mesgs"):
            for msg in m.get(key, []):
                ts = msg.get("start_time") or msg.get("time_created") or ts
            if ts:
                break
        hrs = [msg.get("heart_rate") for msg in m.get("record_mesgs", []) if msg.get("heart_rate")]
        rmssd, n = _rmssd(rr) if rr else (None, 0)
        when = ts.astimezone(tz).strftime("%Y-%m-%d %-I:%M %p") if ts else f.parent.name
        bits = [when]
        if rmssd is not None:
            bits.append(f"HRV (RMSSD) {rmssd} ms from {n} beats")
        else:
            bits.append(f"too few clean beats for HRV ({n})")
        if hrs:
            bits.append(f"HR mean {round(sum(hrs) / len(hrs))}")
        rows.append("- " + " | ".join(bits))
    if not rows:
        return []
    return ["## Daytime readings (Health Snapshots)",
            "Two-minute seated recordings the athlete started on the watch. HRV here is worked out "
            "from the beat-to-beat intervals the watch saved, not taken from Garmin's own figure."] + rows


def write_record(store, tz_name: str) -> Path:
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    text, meta = record.build(store, tz_name, today)
    extra = notes_section(store)
    snaps = snapshots_section(tz_name)
    if snaps:
        extra = extra + ([""] if extra else []) + snaps
    if extra:
        text = text.rstrip("\n") + "\n\n" + "\n".join(extra) + "\n"
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "record.md.tmp"
    tmp.write_text(text)
    tmp.replace(out / "record.md")   # a reader never sees half a file
    shutil.copyfile(Path(__file__).parent / "instructions.md", out / "instructions.md")
    # The condensed record (every value, labels once) and the lookup files.
    (out / "record-condensed.md.tmp").write_text(layers.condensed(text))
    (out / "record-condensed.md.tmp").replace(out / "record-condensed.md")
    from .catalogue import prepare
    nights = db.nights(store)
    _, _, _, _, cards, seg_rows = prepare(db.sessions(store), db.get_meta(store, "caps", {}) or {}, nights, "")
    look = out / "lookup"
    if look.exists():
        shutil.rmtree(look)
    for rel, body in layers.lookup(cards, seg_rows, nights, _notes()).items():
        (look / rel).parent.mkdir(parents=True, exist_ok=True)
        (look / rel).write_text(body)
    status = {
        "written": datetime.now(tz).isoformat(timespec="minutes"),
        "sessions": meta.get("sessions"),
        "nights": meta.get("nights"),
        "export_end": meta.get("export_end"),
        "source": db.get_meta(store, "source"),
        "garmin_last_pull": db.get_meta(store, "garmin_last_pull"),
        "garmin_last_error": db.get_meta(store, "garmin_last_error"),
        "notes": len(_notes()),
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
