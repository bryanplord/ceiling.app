"""Build the history from a Garmin account export, once, into the app's database.

    python -m fullrecord.ingest path/to/export.zip      (or the unpacked folder)
    python -m fullrecord.ingest path/to/export.zip --email someone@example.com

Reads every activity file for its second-by-second records, and the HRV, sleep
and all-day monitoring files for the nights. Writes to the Coach page's own
tables (see db.py) in whatever database the app is using, local or Turso.
Ceiling's own tables are not touched. Safe to run again: rows are replaced.
At the end it prints a short summary, which is all anyone else needs to see.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .catalogue import _fit_file_type, assemble, session_rows
from .fit import read_activity
from .nights import collect, nights


def _unpack(path, work):
    """A zip (or a folder) in, a list of folders to search out. Garmin nests
    zips of the uploaded files inside the export; those are unpacked into the
    work folder too, so a folder given as input is never changed."""
    roots = []
    if os.path.isdir(path):
        roots.append(path)
    else:
        dest = os.path.join(work, "export")
        with zipfile.ZipFile(path) as z:
            z.extractall(dest)
        roots.append(dest)
    seen, n = set(), 0
    while True:
        inner = [p for r in roots for p in glob.glob(os.path.join(r, "**", "*.zip"), recursive=True)
                 if p not in seen]
        if not inner:
            break
        for p in inner:
            seen.add(p)
            n += 1
            dest = os.path.join(work, "inner", str(n))
            try:
                with zipfile.ZipFile(p) as z:
                    z.extractall(dest)
                roots.append(dest)
            except zipfile.BadZipFile:
                pass
    return roots


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", help="Garmin export zip or unpacked folder")
    ap.add_argument("--tz", default=os.getenv("RX_TZ", "America/Los_Angeles"))
    ap.add_argument("--email", default="", help="the Ceiling account the history is for; "
                    "leave out for the primary athlete")
    args = ap.parse_args(argv)

    from rx.service import open_store, owner_of
    store = open_store(owner_of(args.email) if args.email else "")
    into(store, args.path, ZoneInfo(args.tz))


def into(store, path, tz):
    """Read a Garmin export (zip or folder) into `store`'s Coach page tables."""
    db.ensure(store)

    work = tempfile.mkdtemp(prefix="fullrecord-")
    try:
        roots = _unpack(path, work)
        fits = sorted({f for r in roots for pat in ("*.fit", "*.FIT")
                       for f in glob.glob(os.path.join(r, "**", pat), recursive=True)})
        print(f"{len(fits)} FIT files found", flush=True)
        acts, starts, dupes, other = [], set(), 0, []
        for i, f in enumerate(fits, 1):
            if i % 500 == 0:
                print(f"  read {i} of {len(fits)}", flush=True)
            kind = _fit_file_type(f)
            if kind != "activity":
                other.append(f)
                continue
            try:
                a = read_activity(f)
            except Exception:
                continue
            if not a["rec"] or len(a["rec"]["t"]) < 5:
                continue
            a["first_ts"] = a["rec_first_ts"]
            key = int(a["first_ts"].timestamp())
            if key in starts:
                dupes += 1
                continue
            starts.add(key)
            acts.append(a)
        acts.sort(key=lambda a: a["first_ts"])
        print(f"{len(acts)} sessions with records ({dupes} duplicates dropped); building cards", flush=True)
        rows, caps = session_rows(acts, tz)
        db.save_sessions(store, rows)
        print("reading nights", flush=True)
        night_rows = nights(collect(other), tz)
        db.save_nights(store, night_rows)
        export_end = max((r["date"] for r in rows), default=None)
        db.set_meta(store, "caps", caps)
        db.set_meta(store, "export_end", export_end)
        db.set_meta(store, "ingested", datetime.now(tz).isoformat(timespec="minutes"))
        store.sync()

        text, meta = assemble(rows, caps, night_rows, datetime.now(tz).date(),
                              datetime.now(tz).isoformat(timespec="minutes"))
        removed = sum(1 for line in text.splitlines() if "far outside this person's own range" in line)
        print("\nSummary (safe to share; it holds no dates of sessions or places):")
        print(f"  sessions: {meta['sessions']}, from {meta['history'][0][:4]} to {meta['history'][1][:4]}")
        print(f"  nights with overnight data: {meta['nights']}")
        print(f"  record size: {len(text):,} characters, about {len(text) // 4:,} tokens")
        print(f"  sessions with a value removed as far outside your own range: {removed}")
        print(f"  altitude-jump limits from your own data, feet per hour: {dict((k, round(v / 0.3048) if v else None) for k, v in meta['limits']['altitude_jump_caps_m_per_h'].items())}")
    finally:
        shutil.rmtree(work, ignore_errors=True)



if __name__ == "__main__":
    sys.exit(main())
