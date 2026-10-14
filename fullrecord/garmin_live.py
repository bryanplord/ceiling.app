"""New days straight from Garmin Connect, for an athlete whose record is fed by
Garmin rather than by Ceiling's intervals.icu pull.

    python -m fullrecord.garmin_live login [--email ACCOUNT]   once: Garmin email, password, two-step code
    python -m fullrecord.garmin_live pull  [--email ACCOUNT]   every few minutes, from a scheduler

This uses python-garminconnect, which logs in to Garmin Connect as the person
rather than through Garmin's developer program. Garmin can break that login
when it changes its own; when that happens the pull fails and says so, nothing
already saved is lost, and the gap can be filled from a Garmin export later.

Each pull downloads, for every activity since the last pull, the original file
the watch uploaded (the same FIT file a Garmin export holds), and for each
recent day that day's wellness files (all-day monitoring, overnight HRV, sleep
times). The files are kept, and read by the same code as the export, using
the limits already computed from the person's own history. The first pull
marks the athlete's source as Garmin, so the record stops adding Ceiling's
intervals.icu sessions and nightly figures for them.

Where things are kept (FULLRECORD_GARMIN_DIR, default ~/.local/share/fullrecord/garmin):
    <account>/tokens/       the Garmin login, refreshed as it ages; treat it like a password
    <account>/activities/   one FIT file per activity
    <account>/wellness/     one folder per day of wellness files
"""
from __future__ import annotations

import argparse
import getpass
import glob
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db

PULL_EVERY_S = 15 * 60          # the page asks for a pull when the last one is older than this
REFRESH_DAYS = 2                # wellness for today and yesterday is fetched again each pull
FIRST_PULL_DAYS = 30            # with no export and no earlier pull, how far back to start


def base_dir(account: str) -> Path:
    root = Path(os.getenv("FULLRECORD_GARMIN_DIR") or "~/.local/share/fullrecord/garmin").expanduser()
    return root / (account or "primary")


def has_login(account: str) -> bool:
    return (base_dir(account) / "tokens").exists()


def _lib():
    try:
        import garminconnect
    except ImportError as e:
        raise SystemExit("Install the extra: pip install '.[garmin]'") from e
    return garminconnect


def login(account: str, email: str, password: str, mfa) -> None:
    """One-time login with the person's Garmin email and password. `mfa` is
    called for the two-step code if Garmin asks for one."""
    gc = _lib()
    tokens = base_dir(account) / "tokens"
    tokens.mkdir(parents=True, exist_ok=True)
    os.chmod(tokens.parent, 0o700)
    os.chmod(tokens, 0o700)
    api = gc.Garmin(email, password, prompt_mfa=mfa)
    api.login()
    api.client.dump(str(tokens))


def client(account: str):
    gc = _lib()
    tokens = base_dir(account) / "tokens"
    if not tokens.exists():
        raise SystemExit("No Garmin login saved yet. Run: python -m fullrecord.garmin_live login")
    api = gc.Garmin()
    api.login(str(tokens))
    return api


def _save_zip_or_fit(data: bytes, dest: Path, stem: str) -> list[Path]:
    """Garmin hands back a zip of the uploaded files, or now and then the bare
    file. Returns the FIT files written."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i, name in enumerate(n for n in z.namelist() if n.lower().endswith(".fit")):
                p = dest / f"{stem}_{i}.fit"
                p.write_bytes(z.read(name))
                out.append(p)
    elif len(data) > 12 and data[8:12] == b".FIT":
        p = dest / f"{stem}.fit"
        p.write_bytes(data)
        out.append(p)
    return out


def download(api, account: str, start: date, today: date, log=print) -> dict:
    """Fetch every activity and wellness day from `start` to `today` that is
    not already kept. Returns the activity files that are new and the days
    whose wellness files were fetched."""
    gc = _lib()
    home = base_dir(account)
    new_acts, days = [], []
    for a in api.get_activities_by_date(start.isoformat(), today.isoformat()):
        aid = str(a["activityId"])
        if glob.glob(str(home / "activities" / f"{aid}*.fit")):
            continue
        data = api.download_activity(aid, dl_fmt=gc.Garmin.ActivityDownloadFormat.ORIGINAL)
        new_acts += _save_zip_or_fit(data, home / "activities", aid)
    d = start - timedelta(days=1)   # the evening before the first night
    while d <= today:
        folder = home / "wellness" / d.isoformat()
        if not folder.exists() or (today - d).days < REFRESH_DAYS:
            try:
                data = api.download(f"/download-service/files/wellness/{d.isoformat()}")
            except Exception as e:   # a day with nothing uploaded comes back as an error
                log(f"  wellness {d}: none ({type(e).__name__})")
                data = b""
            if data:
                for old in folder.glob("*.fit"):
                    old.unlink()
                _save_zip_or_fit(data, folder, d.isoformat())
                days.append(d)
            # Garmin's daily download leaves out the overnight HRV and sleep-times
            # files the export has; the same watch readings come as JSON instead.
            folder.mkdir(parents=True, exist_ok=True)
            for name, fn in (("hrv", api.get_hrv_data), ("sleep", api.get_sleep_data)):
                try:
                    (folder / f"{name}.json").write_text(json.dumps(fn(d.isoformat()) or {}))
                except Exception as e:
                    log(f"  {name} {d}: none ({type(e).__name__})")
            if d not in days:
                days.append(d)
        d += timedelta(days=1)
    return {"activities": new_acts, "days": days}


def _gmt(text):
    """'2026-06-11T06:01:10.0', Garmin's GMT reading time, to Unix seconds."""
    return int(datetime.fromisoformat(text.split(".")[0]).replace(tzinfo=timezone.utc).timestamp())


def _add_json_nights(data: dict, paths) -> None:
    """Add the overnight HRV readings and the watch's sleep start and end from
    Garmin's JSON to what the FIT files gave. These are the same watch readings
    the export's HRV and sleep files hold (checked against the export for
    2026-06-11: the same 70 readings and the same sleep window)."""
    from .nights import dedupe
    for p in paths:
        try:
            j = json.loads(p.read_text())
        except Exception:
            continue
        if p.name == "hrv.json":
            for r in j.get("hrvReadings") or []:
                if r.get("hrvValue") and r.get("readingTimeGMT"):
                    data["hrv"].append((_gmt(r["readingTimeGMT"]), r["hrvValue"]))
        elif p.name == "sleep.json":
            dto = j.get("dailySleepDTO") or {}
            a, b = dto.get("sleepStartTimestampGMT"), dto.get("sleepEndTimestampGMT")
            if a and b:
                data["sleeps"].append((int(a) // 1000, int(b) // 1000))
    data["hrv"] = dedupe(sorted(data["hrv"]))
    data["sleeps"] = sorted(set(data["sleeps"]))


def ingest(store, account: str, got: dict, start: date, tz) -> dict:
    """Read the new files into the athlete's fr_ tables, exactly as the export
    ingest does, with the limits already computed from their history."""
    from .catalogue import _fit_file_type, session_rows
    from .fit import read_activity
    from .nights import collect, nights

    caps = db.get_meta(store, "caps", None)
    acts = []
    for f in got["activities"]:
        if _fit_file_type(str(f)) != "activity":
            continue
        try:
            a = read_activity(str(f))
        except Exception:
            continue
        if not a["rec"] or len(a["rec"]["t"]) < 5:
            continue
        a["first_ts"] = a["rec_first_ts"]
        acts.append(a)
    acts.sort(key=lambda a: a["first_ts"])
    rows = []
    if acts:
        rows, new_caps = session_rows(acts, tz, caps=caps, source="garmin")
        if caps is None:   # no export: the limits come from what Garmin has given so far
            db.set_meta(store, "caps", new_caps)
        db.save_sessions(store, rows)

    night_rows = []
    if got["days"]:
        # A night runs from the evening before into the morning, so read every
        # kept day from the one before `start` and keep the nights from `start` on.
        home = base_dir(account) / "wellness"
        files = [str(p) for p in home.glob("*/*.fit")
                 if p.parent.name >= (start - timedelta(days=1)).isoformat()]
        data = collect(files)
        _add_json_nights(data, [p for p in home.glob("*/*.json")
                                if p.parent.name >= (start - timedelta(days=1)).isoformat()])
        night_rows = [n for n in nights(data, tz) if n["night"] >= start.isoformat()]
        db.save_nights(store, night_rows)
    return {"sessions": len(rows), "nights": len(night_rows)}


def pull(store, account: str, tz_name: str, log=print) -> dict:
    """One pull: everything since the last one, read into the tables. Safe to
    run as often as wanted; files already kept are not fetched again."""
    tz = ZoneInfo(tz_name)
    db.ensure(store)
    today = datetime.now(tz).date()
    last = db.get_meta(store, "garmin_last_pull")
    if last:
        start = date.fromisoformat(last[:10]) - timedelta(days=1)
    elif db.get_meta(store, "export_end"):
        start = date.fromisoformat(db.get_meta(store, "export_end")) - timedelta(days=1)
    else:
        start = today - timedelta(days=FIRST_PULL_DAYS)
    api = client(account)
    try:
        got = download(api, account, start, today, log=log)
    finally:
        api.client.dump(str(base_dir(account) / "tokens"))   # keep any refreshed login
    counts = ingest(store, account, got, start, tz)
    db.set_meta(store, "source", "garmin")
    db.set_meta(store, "garmin_last_pull", datetime.now(tz).isoformat(timespec="minutes"))
    db.set_meta(store, "garmin_last_error", None)
    if counts["sessions"] or counts["nights"]:
        db.set_meta(store, "ingested", datetime.now(tz).isoformat(timespec="minutes"))
    store.sync()
    log(f"  garmin: {len(got['activities'])} activity files, {len(got['days'])} wellness days; "
        f"{counts['sessions']} sessions and {counts['nights']} nights saved")
    return counts


def due(store) -> bool:
    last = db.get_meta(store, "garmin_last_pull")
    if not last:
        return True
    return time.time() - datetime.fromisoformat(last).timestamp() > PULL_EVERY_S


def _open(email):
    from rx.service import open_store, owner_of
    owner = owner_of(email) if email else ""
    return open_store(owner), owner


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["login", "pull"])
    ap.add_argument("--email", default="", help="the Ceiling account; leave out for the primary athlete")
    ap.add_argument("--tz", default=os.getenv("RX_TZ", "America/Los_Angeles"))
    ap.add_argument("--if-due", action="store_true", help="pull only if the last pull is older than 15 minutes")
    ap.add_argument("--account", default=None, help="for login without Ceiling installed: the account "
                    "folder name (leave out for the primary athlete)")
    args = ap.parse_args(argv)
    if args.command == "login":
        owner = args.account if args.account is not None else (_open(args.email)[1] if args.email else "")
        email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
        password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password (not stored): ")
        login(owner, email, password, lambda: input("Two-step code: ").strip())
        print(f"Logged in. The login is saved in {base_dir(owner) / 'tokens'}; the password is not.")
        return 0
    store, owner = _open(args.email)
    db.ensure(store)
    if args.if_due and not due(store):
        return 0
    try:
        pull(store, owner, args.tz)
    except Exception as e:
        db.set_meta(store, "garmin_last_error",
                    f"{datetime.now().isoformat(timespec='minutes')} {type(e).__name__}: {str(e)[:300]}")
        print(f"Garmin pull failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
