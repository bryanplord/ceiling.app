"""Claude reads the whole record and writes the day's suggestion, then talks it
through. The instructions explain why it has the whole record and how to use it.

Configured by environment:
  ANTHROPIC_API_KEY     required; without it the Coach page says it is off
  COACH_MODEL           default claude-opus-5-5
  COACH_INSTRUCTIONS    path to the instructions; default fullrecord/instructions.md
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import db

DEFAULT_MODEL = "claude-opus-5-5"

FORMAT = """Reply with one JSON object and nothing else, in a ```json block, with these keys:
  "headline": the verdict shown very large at the top of the page, two to five words ending with a full stop, such as "Easy hour." or "Go long."
  "diagnosis": one or two sentences on where the athlete stands today
  "what": the session in one sentence
  "how_short": the essential numbers in one sentence
  "how": a list of sentences with every detail needed to do it
  "why": a few sentences on why this session, today, for this person
  "minimum": the least that still counts, in one or two sentences
  "tomorrow_short": one sentence
  "tomorrow": a list of {"label": the choice, "tone": "good" or "bad" or "muted", "text": what tomorrow looks like after it}
  "tonight": one or two sentences
  "markers": a list of {"name", "now": its current value, "expect": the direction you expect, "why"}
  "tests": a list of {"assumption", "test": the session that tests it, "when"}
  "sources": a list of {"claim", "basis": "record" or "general", "evidence": dates and numbers from the record, or the general principle}
Every sentence is a full English sentence. Use US units throughout: miles, feet, minutes per mile,
miles per hour and feet climbed per hour, never kilometres or metres."""


def configured() -> str | None:
    """None when the page can run, otherwise the reason it cannot."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "No Anthropic API key is set (ANTHROPIC_API_KEY)."
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "The anthropic package is not installed (pip install '.[coach]')."
    return None


def model() -> str:
    return os.getenv("COACH_MODEL", DEFAULT_MODEL)


def instructions() -> str:
    p = os.getenv("COACH_INSTRUCTIONS") or str(Path(__file__).parent / "instructions.md")
    return Path(p).read_text()


def _system(record_text, goals):
    goal_text = goals.strip() if goals and goals.strip() else "The athlete has not written their goals yet."
    return [
        {"type": "text", "text": instructions() + "\n\n## The athlete's goals, in their words\n\n" + goal_text},
        {"type": "text", "text": "<record>\n" + record_text + "\n</record>", "cache_control": {"type": "ephemeral"}},
    ]


def _earlier(store, day) -> str:
    lines = []
    for d in reversed(db.recent_days(store, day, 14)):
        s = d["suggestion"] or {}
        lines.append(f"### {d['day']}\nYou suggested: {s.get('headline', '')} {s.get('what', '')} {s.get('how_short', '')}")
        for m in db.messages(store, d["day"]):
            who = "Athlete" if m["role"] == "user" else "You"
            lines.append(f"{who}: {m['content']}")
    return "\n".join(lines) if lines else "There are no earlier days yet."


def _opening(store, day) -> str:
    return (f"Today is {day}.\n\n## Earlier days\n\n{_earlier(store, day)}\n\n"
            f"Write today's suggestion.\n\n{FORMAT}")


def _client():
    import anthropic
    return anthropic.Anthropic()


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def parse(text: str) -> dict:
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.index("{"): text.rindex("}") + 1]
    return json.loads(raw)


def suggest(store, day, record_text, goals) -> dict:
    resp = _client().messages.create(
        model=model(), max_tokens=16000, system=_system(record_text, goals),
        messages=[{"role": "user", "content": _opening(store, day)}])
    return parse(_text(resp))


def reply(store, day, record_text, goals, suggestion, conversation) -> str:
    msgs = [{"role": "user", "content": _opening(store, day)},
            {"role": "assistant", "content": "```json\n" + json.dumps(suggestion, indent=1) + "\n```"}]
    for m in conversation:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs[-1]["content"] = msgs[-1]["content"] + (
        "\n\n(Answer in plain prose, in full sentences. If you change the suggestion, say exactly what "
        "changed and which evidence from the record moved you.)")
    resp = _client().messages.create(model=model(), max_tokens=8000, system=_system(record_text, goals),
                                     messages=msgs)
    return _text(resp).strip()
