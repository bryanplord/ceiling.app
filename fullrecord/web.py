"""The Coach page: Claude's suggestion for today in the same layout as Today,
with a conversation underneath. Mounted into Ceiling's app as its own router;
everything here reads Ceiling's data and writes only the Coach page's tables."""
from __future__ import annotations

import os
import re
import threading
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from markupsafe import Markup, escape

from . import coach, db, record

router = APIRouter()

_here = Path(__file__).parent
_rx_templates = Path(__file__).resolve().parent.parent / "rx" / "web" / "templates"
try:
    import rx.web as _rxweb
    _rx_templates = Path(_rxweb.__file__).parent / "templates"
except Exception:
    pass
templates = Jinja2Templates(directory=str(_here / "templates"))
templates.env.loader = ChoiceLoader([FileSystemLoader(str(_here / "templates")), FileSystemLoader(str(_rx_templates))])

def _prose(text: str) -> Markup:
    """The coach's replies: paragraphs, bold and bulleted lines, everything else escaped."""
    out, items = [], []

    def inline(t):
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(escape(t)))

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if all(l.lstrip().startswith(("- ", "* ")) for l in lines if l.strip()):
            items.extend(l.lstrip()[2:] for l in lines if l.strip())
            continue
        flush()
        out.append("<p>" + "<br>".join(inline(l) for l in lines) + "</p>")
    flush()
    return Markup("".join(out))


templates.env.filters["prose"] = _prose

_busy: dict[str, threading.Lock] = {}
_busy_guard = threading.Lock()


def _lock(name):
    with _busy_guard:
        return _busy.setdefault(name, threading.Lock())


def _tz():
    return os.getenv("RX_TZ", "America/Los_Angeles")


def _today():
    from rx.clock import local_today
    return local_today()


def _store():
    from rx.service import open_store
    s = open_store()
    db.ensure(s)
    return s


def _generate(day: str, force=False):
    lk = _lock("day:" + day)
    if not lk.acquire(blocking=False):
        return

    def run():
        try:
            store = _store()
            cur = db.day(store, day)
            if cur and cur["state"] == "ready" and not force:
                return
            db.set_day(store, day, "pending", model=coach.model())
            text, _ = record.build(store, _tz(), date.fromisoformat(day))
            s = coach.suggest(store, day, text, db.get_meta(store, "goals", ""))
            db.set_day(store, day, "ready", s, coach.model())
            store.sync()
        except Exception as e:
            try:
                db.set_day(_store(), day, f"failed: {type(e).__name__}: {str(e)[:300]}", model=coach.model())
            except Exception:
                pass
        finally:
            lk.release()

    threading.Thread(target=run, daemon=True).start()


def _answer(day: str):
    lk = _lock("chat:" + day)
    if not lk.acquire(blocking=False):
        return

    def run():
        try:
            store = _store()
            d = db.day(store, day)
            text, _ = record.build(store, _tz(), date.fromisoformat(day))
            out = coach.reply(store, day, text, db.get_meta(store, "goals", ""), d["suggestion"],
                              db.messages(store, day))
            db.add_message(store, day, "assistant", out)
            store.sync()
        except Exception as e:
            try:
                db.add_message(_store(), day, "assistant",
                               f"(The reply failed: {type(e).__name__}: {str(e)[:300]})")
            except Exception:
                pass
        finally:
            lk.release()

    threading.Thread(target=run, daemon=True).start()


@router.get("/coach", response_class=HTMLResponse)
def coach_page(request: Request):
    why_off = coach.configured()
    store = _store()
    counts = db.counts(store)
    day = _today().isoformat()
    d = db.day(store, day)
    if not why_off and counts["fr_sessions"] == 0 and store.counts()["streams"] == 0:
        why_off = "There is no history yet. Run python -m fullrecord.ingest on your Garmin export."
    if not why_off and (d is None or (d["state"] != "ready" and not d["state"].startswith("failed")
                                      and not _lock("day:" + day).locked())):
        _generate(day)
        d = db.day(store, day)
    msgs = db.messages(store, day)
    replying = _lock("chat:" + day).locked()
    pending = (d is not None and d["state"] == "pending") or _lock("day:" + day).locked()
    ctx = {"active": "coach", "why_off": why_off, "d": d, "s": (d or {}).get("suggestion") or {},
           "pending": pending, "msgs": msgs, "replying": replying,
           "goals": db.get_meta(store, "goals", ""), "day_label": _today().strftime("%A, %B %-d"),
           "model": coach.model(), "counts": counts, "export_end": db.get_meta(store, "export_end")}
    return templates.TemplateResponse(request, "coach.html", ctx)


@router.post("/coach/new")
def coach_new():
    _generate(_today().isoformat(), force=True)
    return RedirectResponse("/coach", status_code=303)


@router.post("/coach/say")
async def coach_say(request: Request):
    text = parse_qs((await request.body()).decode()).get("text", [""])[0].strip()
    day = _today().isoformat()
    store = _store()
    d = db.day(store, day)
    if text and d and d["state"] == "ready" and not _lock("chat:" + day).locked():
        db.add_message(store, day, "user", text)
        _answer(day)
    return RedirectResponse("/coach#talk", status_code=303)


@router.post("/coach/goals")
async def coach_goals(request: Request):
    text = parse_qs((await request.body()).decode()).get("goals", [""])[0].strip()
    db.set_meta(_store(), "goals", text)
    return RedirectResponse("/coach", status_code=303)
