"""The record in its other two forms, both written from the same data as the full record.

- condensed(full_text): the full record with every value kept, the session and
  segment lines written compactly with each label stated once in a legend. A
  field is shortened only when every number in it survives; otherwise it is
  left exactly as in the full record.
- lookup(...): the full record organised for finding things: one file per
  month with a heading for each day (every session with all its segments, the
  night, and the athlete's notes), an index of what exists on each date, and a
  list of every reading the checks doubted, grouped by kind. Each session keeps
  the same ID (its local start, yyyy-mm-dd-hh-mm-ss) in every file.

The full record (render.py) is what the morning run reads; the condensed one is
for the conversation afterwards, with the lookup files for anything it needs in
full.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from .render import card_line, night_line, segment_line

NUM = re.compile(r"-?\d+(?:\.\d+)?")
SESSION = re.compile(r"^\d{4}-\d{2}-\d{2} \w{3} \d{2}:\d{2} \| ")
MODES = {"foot": "f", "bike": "b", "swim": "w", "fast": "v", "still": "s", "paused": "p"}

LEGEND = """## How to read the condensed record

This is the same record as the full one, with every value kept; only the labels are shortened.
Each session is one line, its fields in this order, separated by spaces:
date, weekday and start time; the sport recorded on the watch; span; paused time as p (if any);
minutes by movement (f foot, b bike, w swim, v fast or vehicle, s still); miles (mi); feet up and
down (ft); fp average pace on foot (min/mi); ff pace on flat ground on foot @ its HR; fc steep
climbing on foot (ft/h) @ its HR; bs bike speed (mph); bf bike speed on the flat @ its HR; bc bike
climbing (ft/h) @ its HR; sw swim pace per 100 yd; hm heart rate while moving; pk and lo the highest
and lowest one-minute heart rate; b minutes in each 10-bpm band, the band written in tens (14:20
means 20 minutes at 140-149 bpm; bands with no minutes are left out); spm steps per minute; W watts;
1mi, 3mi and 10mi the fastest mile and three miles on foot and ten miles on the bike; alt the
lowest/highest altitude (ft); t the watch's temperature (C); +Nd the days since the previous
session; then any NOTE, word for word. A field that could not be shortened without losing a number
is written in full.
The indented line under a session lists its segments, joined by >: duration, movement letter, then
in brackets the heart rate (first>last when it changed by 6 or more), the pace (min/mi on foot), mph
on a bike or in a vehicle, the grade (%) and the rate of climb (fh, feet per hour).
Each session's full entry, with every segment, is in the lookup files under its date."""

FIELD_RULES = [
    (re.compile(r"^recorded as (.+)$"), r"\1"),
    (re.compile(r"^span (\S+)$"), r"\1"),
    (re.compile(r"^paused (\S+)$"), r"p\1"),
    (re.compile(r"^([\d.]+) mi$"), r"\1mi"),
    (re.compile(r"^\+(\d+)/-(\d+) ft$"), r"+\1-\2ft"),
    (re.compile(r"^foot (\d+:\d\d)/mi$"), r"fp\1"),
    (re.compile(r"^flat (\d+:\d\d)/mi at HR (\d+)$"), r"ff\1@\2"),
    (re.compile(r"^steep climbing (-?\d+) ft/h at HR (\d+)$"), r"fc\1@\2"),
    (re.compile(r"^bike ([\d.]+) mph$"), r"bs\1"),
    (re.compile(r"^bike flat ([\d.]+) mph at HR (\d+)$"), r"bf\1@\2"),
    (re.compile(r"^bike climbing (-?\d+) ft/h at HR (\d+)$"), r"bc\1@\2"),
    (re.compile(r"^swim (\d+:\d\d)/100yd$"), r"sw\1"),
    (re.compile(r"^HR moving (\d+)$"), r"hm\1"),
    (re.compile(r"^HR 1-min peak (\d+), low (\d+)$"), r"pk\1 lo\2"),
    (re.compile(r"^(\d+) spm$"), r"\1spm"),
    (re.compile(r"^(\d+) W$"), r"\1W"),
    (re.compile(r"^fastest foot mile (\S+)$"), r"1mi\1"),
    (re.compile(r"^fastest foot 3 miles (\S+)$"), r"3mi\1"),
    (re.compile(r"^fastest bike 10 miles (\S+)$"), r"10mi\1"),
    (re.compile(r"^alt (-?\d+)-(-?\d+) ft$"), r"alt\1/\2"),
    (re.compile(r"^watch (-?[\d.]+) C$"), r"t\1"),
    (re.compile(r"^(\d+)d after previous$"), r"+\1d"),
]
MOVES = re.compile(r"^(?:(?:foot|bike|swim|fast|still) \d+(?::\d\d){1,2}(?:, )?)+$")


def _nums(s):
    return NUM.findall(s.replace("-", " -").replace("+", " "))


def _same(a, b):
    """True when the two texts carry the same numbers in the same order."""
    norm = lambda xs: [str(abs(float(x))) for x in xs]
    return norm(_nums(a)) == norm(_nums(b))


def _short_field(f):
    if f.startswith("NOTE: "):
        return "| " + f
    if f.startswith("HR min by band "):
        pairs = [x.split(":") for x in f[len("HR min by band "):].split()]
        if all(len(x) == 2 and x[0].isdigit() and x[1].isdigit() for x in pairs):
            kept = [f"{int(b) // 10}:{m}" for b, m in pairs if m != "0"]
            return "b " + " ".join(kept) if kept else ""
        return f
    if MOVES.match(f):
        out = " ".join(MODES[w.split()[0]] + w.split()[1] for w in f.split(", "))
        return out if _same(f, out) else f
    for rx, rep in FIELD_RULES:
        if rx.match(f):
            # Each rule matches the whole field and keeps every captured value, so
            # nothing variable can be lost; only the fixed label words change.
            return rx.sub(rep, f)
    return f


def _short_session(line):
    fields = line.split(" | ")
    out = [fields[0]] + [x for x in (_short_field(f) for f in fields[1:]) if x]
    return " ".join(out)


def _short_structure(line):
    s = line.strip()
    t = s.replace(" > ", ">").replace("HR ", "").replace("/mi", "").replace(" mph", "").replace(" ft/h", "fh")
    t = re.sub(r"paused (\d)", r"p\1", t)
    t = re.sub(r"(\d) (foot|bike|swim|fast|still)\b", lambda m: m.group(1) + MODES[m.group(2)], t)
    t = re.sub(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)", lambda m: "(" + m.group(1).replace(", ", " ") + ")", t)
    return "    " + (t if _same(s, t) else s)


def condensed(full_text: str) -> str:
    out, prev_session = [], False
    for line in full_text.splitlines():
        if line.startswith("## Every session"):
            out.append(LEGEND)
            out.append("")
            out.append("## Every session, oldest first")
            prev_session = False
            continue
        if SESSION.match(line):
            out.append(_short_session(line))
            prev_session = True
            continue
        if prev_session and line.startswith("    ") and line.strip():
            out.append(_short_structure(line))
            prev_session = False
            continue
        prev_session = False
        out.append(line)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ lookup files

FLAG_KINDS = [
    ("Heart-rate spikes left out", ("jumped more than",)),
    ("Heart rate frozen while moving", ("stopped changing", "unchanged", "frozen")),
    ("Heart rate following the step rate (cadence lock)", ("step", "stride", "cadence")),
    ("One-minute peaks with no rise into them", ("no rise",)),
    ("Altitude and climbing", ("altitude", "climb")),
    ("Vehicle stretches", ("vehicle",)),
    ("Missing heart rate", ("without heart rate", "no heart rate", "without HR")),
]


def _flag_kind(note):
    low = note.lower()
    for name, keys in FLAG_KINDS:
        if any(k.lower() in low for k in keys):
            return name
    return "Other notes"


def lookup(cards, seg_rows, night_rows, notes=()):
    """{relative path: text} for the lookup files."""
    segs = {s["id"]: s["segments"] for s in seg_rows}
    by_day = defaultdict(lambda: {"sessions": [], "night": None, "notes": []})
    for c in cards:
        by_day[c["date"]]["sessions"].append(c)
    for n in night_rows:
        by_day[n["night"]]["night"] = n
    for d, t, text in notes:
        by_day[d]["notes"].append((t, text))

    months = defaultdict(list)
    index = ["# Index of the record, one line per date",
             "Each line says what exists for that date and which month file holds it in full. "
             "Session IDs are the local start time and are the same in every file.", ""]
    flags = defaultdict(list)
    for d in sorted(by_day):
        day = by_day[d]
        wd = date.fromisoformat(d).strftime("%a")
        mfile = f"months/{d[:7]}.md"
        L = months[d[:7]]
        L.append(f"## {d} {wd}")
        bits = []
        if day["sessions"]:
            bits.append("sessions " + ", ".join(
                f"{c['id']} {c.get('recorded_as', '?')}" for c in day["sessions"]))
        for c in day["sessions"]:
            L.append(f"### {c['id']}")
            L.append(card_line(c))
            if segs.get(c["id"]):
                L.append("Every segment:")
                L.extend(segment_line(g) for g in segs[c["id"]])
            for note in c.get("notes", []):
                flags[_flag_kind(note)].append(f"- {c['id']}: {note}")
        if day["night"]:
            L.append("### Night ending this morning")
            L.append(night_line(day["night"]))
            bits.append("night")
        if day["notes"]:
            L.append("### Notes the athlete logged")
            L.extend(f"- {t} | {text}" for t, text in day["notes"])
            bits.append(f"{len(day['notes'])} note{'s' if len(day['notes']) > 1 else ''}")
        n_flags = sum(len(c.get("notes", [])) for c in day["sessions"])
        if n_flags:
            bits.append(f"{n_flags} flagged reading{'s' if n_flags > 1 else ''}")
        L.append("")
        index.append(f"- {d} {wd} | " + " | ".join(bits) + f" | {mfile}")

    files = {"index.md": "\n".join(index) + "\n"}
    for m, L in months.items():
        files[f"months/{m}.md"] = f"# {m}\n\n" + "\n".join(L) + "\n"
    F = ["# Every reading the checks doubted, by kind",
         "Each line is a session ID and the note written on that session. The full session is in "
         "the month file for its date.", ""]
    for name, _ in FLAG_KINDS + [("Other notes", ())]:
        if flags.get(name):
            F.append(f"## {name} ({len(flags[name])})")
            F.extend(flags[name])
            F.append("")
    files["flags.md"] = "\n".join(F) + "\n"
    return files
