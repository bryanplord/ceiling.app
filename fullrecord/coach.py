"""Claude reads the whole record and writes the day's suggestion, then talks it
through. The instructions explain why it has the whole record and how to use it.

Claude runs on the athlete's own Claude subscription by default, through the
Claude Code command line that the claude-agent-sdk package installs. No API key
and no per-use billing are involved.

Configured by environment:
  CLAUDE_CODE_OAUTH_TOKEN  the subscription login for a server such as Render; make it once
                           on your own computer with `claude setup-token`. On a computer
                           where you are already logged in to Claude Code it is not needed.
  COACH_BACKEND            "subscription" (the default) or "api"; "api" uses ANTHROPIC_API_KEY
                           and bills per use instead
  COACH_MODEL              default claude-opus-5-5
  COACH_CLAUDE_CLI         path to the claude command, if not the bundled one or on PATH
  COACH_INSTRUCTIONS       path to the instructions; default fullrecord/instructions.md
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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
  "notebook": {"doubted": a list of {"reading", "why", "probably": your estimate of the true value, only when another reading from the same session supports it, otherwise empty}, "hypotheses": a list of {"statement", "test", "status": "open", "supported" or "dropped", "since": the day it was first made, "result": what the latest test showed, if anything}, "athlete_words": a sentence or two on what the athlete told you since your last suggestion and how much weight you gave it}
Every sentence is a full English sentence. Use US units throughout: miles, feet, minutes per mile,
miles per hour and feet climbed per hour, never kilometres or metres. Write times of day in
12-hour form, such as 10:30 PM."""


def backend() -> str:
    return "api" if os.getenv("COACH_BACKEND", "subscription").strip().lower() == "api" else "subscription"


def _cli() -> str | None:
    if os.getenv("COACH_CLAUDE_CLI"):
        return os.getenv("COACH_CLAUDE_CLI")
    try:
        import claude_agent_sdk
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        if bundled.exists():
            return str(bundled)
    except ImportError:
        pass
    return shutil.which("claude")


def configured() -> str | None:
    """None when the page can run, otherwise the reason it cannot."""
    if backend() == "api":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return "COACH_BACKEND is api, but no Anthropic API key is set (ANTHROPIC_API_KEY)."
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "The anthropic package is not installed (pip install '.[coach-api]')."
        return None
    if not _cli():
        return "Claude Code is not installed (pip install '.[coach]')."
    if not os.getenv("CLAUDE_CODE_OAUTH_TOKEN") and not (Path.home() / ".claude").exists():
        return ("No Claude subscription login is set. Run `claude setup-token` on your own computer "
                "and set the token it prints as CLAUDE_CODE_OAUTH_TOKEN.")
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


def _ledger(store, day) -> str:
    """The open hypotheses from the most recent earlier day, carried forward."""
    for d in db.recent_days(store, day, 14):
        hyp = ((d["suggestion"] or {}).get("notebook") or {}).get("hypotheses")
        if hyp:
            return f"## Hypotheses carried from {d['day']}\n\n" + json.dumps(hyp, indent=1)
    return ""


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
    ledger = _ledger(store, day)
    return (f"Today is {day}.\n\n## Earlier days\n\n{_earlier(store, day)}\n\n"
            + (ledger + "\n\n" if ledger else "")
            + f"Write today's suggestion.\n\n{FORMAT}")


def parse(text: str) -> dict:
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.index("{"): text.rindex("}") + 1]
    return json.loads(raw)


REPLY_NOTE = ("(Answer in plain prose, in full sentences. If you change the suggestion, say exactly what "
              "changed and which evidence from the record moved you.)")


# Subscription: one call to Claude Code, the instructions (and, for the morning
# suggestion, the whole record) as the system prompt, and the day's messages as
# the prompt. For a reply the record is not resent: it is a file in the working
# folder that Claude can search or read when a question needs it.

def _subscription(system, prompt, files=None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # it would take precedence
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        sys_file = Path(tmp) / "system.md"
        sys_file.write_text("\n\n".join(b["text"] for b in system))
        tools = ["--tools", ""]
        if files:
            for name, text in files.items():
                (Path(tmp) / name).write_text(text)
            tools = ["--tools", "Read,Grep", "--allowedTools", "Read", "Grep", "--restricted"]
        proc = subprocess.run(
            [_cli(), "-p", "--model", model(), *tools, "--system-prompt-file", str(sys_file),
             "--output-format", "json", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, cwd=tmp, env=env, timeout=900)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
    if out.get("is_error") or proc.returncode:
        raise RuntimeError(f"Claude Code: {str(out.get('result') or out)[:500]}")
    return out.get("result", "")


def _flatten(msgs) -> str:
    """The conversation as one prompt, for the subscription path."""
    parts = [msgs[0]["content"]]
    for m in msgs[1:]:
        parts.append(f"=== {'ATHLETE' if m['role'] == 'user' else 'YOU'} ===\n{m['content']}")
    return "\n\n".join(parts)


# API: the same messages through the Anthropic API, billed per use.

def _api(system, msgs, max_tokens) -> str:
    import anthropic
    resp = anthropic.Anthropic().messages.create(model=model(), max_tokens=max_tokens, system=system,
                                                 messages=msgs)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _ask(system, msgs, max_tokens) -> str:
    if backend() == "api":
        return _api(system, msgs, max_tokens)
    return _subscription(system, _flatten(msgs))


def suggest(store, day, record_text, goals) -> dict:
    msgs = [{"role": "user", "content": _opening(store, day)}]
    return parse(_ask(_system(record_text, goals), msgs, 16000))


RECORD_FILE = "record.md"

LOOKUP_NOTE = """## The record during the conversation

This morning you read the whole record and wrote today's suggestion. The record is not repeated
here. When a question needs a specific fact you do not have, search the file record.md for a date,
a word or a number, and read only the lines you need. Do not read the whole file. When a question
names a day, read every line for that date before answering. Say which lines you looked at when
they change your answer."""


def reply(store, day, record_text, goals, suggestion, conversation) -> str:
    msgs = [{"role": "user", "content": _opening(store, day)},
            {"role": "assistant", "content": "```json\n" + json.dumps(suggestion, indent=1) + "\n```"}]
    for m in conversation:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + REPLY_NOTE
    if backend() == "api":
        # The API path has no file tools here, so it still sends the whole record.
        return _api(_system(record_text, goals), msgs, 8000).strip()
    system = _system("", goals)[:1] + [{"type": "text", "text": LOOKUP_NOTE}]
    return _subscription(system, _flatten(msgs), files={RECORD_FILE: record_text}).strip()
