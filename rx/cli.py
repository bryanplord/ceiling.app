from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from .config import settings
from .service import open_store, scored_day, today_rx
from . import profile as prof


def cmd_sync(a):
    s = settings()
    store = open_store()
    if a.source == "intervals":
        from .sources import intervals
        c = intervals.IntervalsClient(s.intervals_api_key, s.intervals_athlete_id)
        print(intervals.sync(c, store, days=a.days, with_streams=not a.no_streams))
    else:
        from .sources import garmin
        api = garmin.client(s.garmin_token_dir)
        print(garmin.sync(api, store, days=a.days, activities=a.activities))


def cmd_garmin_login(_):
    from .sources import garmin
    garmin.login_interactive(settings().garmin_token_dir)
    print("Logged in. Session saved locally; your password was not stored.")


def cmd_profile(_):
    p = prof.build(open_store())
    m = p.max_hr
    print(f"Max HR      {m.bpm or '—'}  {'(confirmed)' if m.confirmed else '(provisional)'}  {m.note}")
    for pk in m.top:
        print(f"              {pk.bpm:.0f}  {pk.date}  {pk.sport}")
    for pk in m.rejected[:3]:
        print(f"   rejected   {pk.bpm:.0f}  {pk.date}  {pk.reason}")
    print(f"Threshold   {p.threshold.bpm or '—'}  from {p.threshold.source}")
    print(f"Aerobic thr {p.aerobic.bpm or '—'}  {p.aerobic.evidence}")
    print(f"Resting HR  {p.rest_hr:.0f}")
    if p.zones:
        print(f"  {p.zones.basis}")
        for n, lo, hi in p.zones.bounds:
            print(f"  {n:14} {lo:>3}–{hi}")
    if p.yesterday:
        y = p.yesterday
        print(f"Load        fitness {y.ctl:.0f}  fatigue {y.atl:.0f}  form {y.tsb:+.0f}")
    r = p.response
    print(f"Response    {r.pairs} day-pairs; fitted={r.fitted}; "
          f"back to baseline after big days in {r.recovery_days} mornings ({r.big_days} big days)")
    print(f"\n{p.progress.headline}")
    for m in p.progress.measures:
        print(f"  {m.name:28} {m.trend:16} {m.sentence}")


def cmd_today(a):
    _, rx = today_rx(open_store(), a.date)
    print(f"\n{rx.headline}\n")
    print("Why:     " + " ".join(rx.why))
    print("Ideal:   " + rx.do)
    for s in rx.steps:
        print("           · " + s)
    print("Don't:   " + rx.dont)
    if rx.compress:
        print("Compress: " + rx.compress)
        print("Stretch:  " + rx.stretch)
    if rx.if_skip:
        print("If you skip it:   " + rx.if_skip)
    if rx.if_overdo:
        print("If you overdo it: " + rx.if_overdo)
    print("Tonight: " + rx.tonight)
    for s in rx.scenarios:
        print("  " + s)
    for n in rx.notes:
        print("  note: " + n)


def cmd_score(a):
    day = a.date or (date.today() - timedelta(days=1)).isoformat()
    rx, sessions = scored_day(open_store(), day)
    print(f"{day}: prescribed — {rx.do}")
    if not sessions:
        print("No sessions recorded.")
    for act, sc in sessions:
        if sc:
            print(f"  {act.name or act.sport}: {sc.score}%  {sc.summary} {sc.cost}")


def cmd_demo(a):
    from . import demo
    demo.seed(open_store(), days=a.days)
    print(f"Seeded {a.days} days of a synthetic athlete into {settings().db_path}")


def cmd_serve(a):
    import uvicorn
    uvicorn.run("rx.web.app:app", host=a.host, port=a.port, reload=False)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rx")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--source", choices=["intervals", "garmin"], default="intervals")
    s.add_argument("--days", type=int, default=365)
    s.add_argument("--no-streams", action="store_true")
    s.add_argument("--activities", action="store_true", help="garmin: also pull activities")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("garmin-login").set_defaults(fn=cmd_garmin_login)
    sub.add_parser("profile").set_defaults(fn=cmd_profile)
    t = sub.add_parser("today")
    t.add_argument("--date")
    t.set_defaults(fn=cmd_today)
    sc = sub.add_parser("score")
    sc.add_argument("--date", help="yyyy-mm-dd, default yesterday")
    sc.set_defaults(fn=cmd_score)
    dm = sub.add_parser("demo", help="seed a synthetic athlete (use a separate RX_DATA_DIR)")
    dm.add_argument("--days", type=int, default=120)
    dm.set_defaults(fn=cmd_demo)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
