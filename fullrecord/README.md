# Coach page

A separate page in Ceiling, beside Today. Each morning Claude reads the
athlete's whole recorded history and writes the day's suggestion in the same
layout as Today, and the athlete can then talk it through underneath. Today
and every other page are unchanged; this folder only reads Ceiling's data and
writes its own tables (named `fr_*`).

## The record

Code turns every second the watch has recorded into a written record:
one line per session with the segments where the effort changed, weekly
totals, the speed and climbing each heart rate bought month by month, heart
rate drift in long steady efforts, the highest and lowest heart rates actually
held, and every night the watch was worn. Nothing in it is estimated by
formula. Values far outside the athlete's own range are removed and named,
judged only against their own data. The one label used is the sport chosen on
the watch, to tell rides and swims from movement on foot.

History comes once from a Garmin account export. Every day after the export
comes from Ceiling's own intervals.icu pull, which has no altitude, so those
days show no climbing. Garmin's own nightly figures for those days are left out
unless `FULLRECORD_GARMIN_NIGHTLY=1`.

## Setup

    pip install -e ".[coach]"
    python -m fullrecord.ingest path/to/garmin-export.zip

Then set `ANTHROPIC_API_KEY` (and on Render, change the build command to
`pip install ".[coach]"`). Without the key the page says it is off.
`COACH_MODEL` picks the model (default `claude-opus-5-5`).

## Files

- `ingest.py`: export to the `fr_sessions` and `fr_nights` tables.
- `fit.py`, `raw.py`, `catalogue.py`, `nights.py`, `render.py`: the record.
- `store_source.py`: days after the export, from Ceiling's store.
- `record.py`: the whole record as text, rebuilt when the data changes.
- `coach.py`: the prompt, the suggestion and the conversation.
- `instructions.md`: what Claude is told about the record and how to use it.
- `web.py`, `templates/coach.html`: the page.
