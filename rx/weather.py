"""The day's weather, read the way a coach would: is it a day to ride outside?

Open-Meteo (free, no key), hourly, for where you train (Truckee unless set). Read for the hours
you'd train, 8 AM to 6 PM. Cached an hour; if the forecast can't be fetched, nothing changes."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date

DEFAULT = (39.3280, -120.1833, "Truckee")          # Bryan's home; other athletes set their own in Profile
SNOW = {71, 73, 75, 77, 85, 86}
THUNDER = {95, 96, 99}


def where(store) -> tuple[float, float, str]:
    try:
        v = json.loads(store.get_meta("location") or "null")
        if v:
            return float(v[0]), float(v[1]), str(v[2])
    except (ValueError, TypeError, IndexError):
        pass
    return DEFAULT


def fetch(store) -> dict | None:
    lat, lon, _ = where(store)
    key = f"wx:{lat:.2f},{lon:.2f}"
    try:
        hit = json.loads(store.get_meta(key) or "null")
    except ValueError:
        hit = None
    if hit and time.time() - hit.get("t", 0) < (3600 if hit.get("data") else 900):
        return hit.get("data")
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&hourly=precipitation_probability,precipitation,temperature_2m,wind_speed_10m,weather_code"
           "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
           "&timezone=America%%2FLos_Angeles&forecast_days=3") % (lat, lon)
    data = None
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"weather fetch failed: {e!r}")
    try:
        store.set_meta(key, json.dumps({"t": time.time(), "data": data}))
    except Exception:
        pass
    return data


def read(data: dict | None, day: str, start: int = 8, end: int = 18) -> dict | None:
    """{rain, snow, thunder, cold, hot, windy, wet_from, prob, inches, lo, hi, wind, line}, or None."""
    if not data or "hourly" not in data:
        return None
    h = data["hourly"]
    rows = []
    for i, t in enumerate(h.get("time") or []):
        if t[:10] == day and start <= int(t[11:13]) <= end:
            g = lambda k: (h.get(k) or [None] * (i + 1))[i]
            rows.append({"hr": int(t[11:13]), "p": g("precipitation_probability") or 0, "mm": g("precipitation") or 0.0,
                         "t": g("temperature_2m"), "w": g("wind_speed_10m") or 0, "c": g("weather_code") or 0})
    if not rows:
        return None
    temps = [r["t"] for r in rows if r["t"] is not None]
    prob = max(r["p"] for r in rows)
    inches = round(sum(r["mm"] for r in rows), 2)
    wet_hours = [r["hr"] for r in rows if r["p"] >= 50 or r["mm"] >= 0.02]
    out = {"prob": prob, "inches": inches, "lo": round(min(temps)) if temps else None, "hi": round(max(temps)) if temps else None,
           "wind": round(max(r["w"] for r in rows)), "snow": any(r["c"] in SNOW for r in rows),
           "thunder": any(r["c"] in THUNDER for r in rows)}
    out["rain"] = bool(wet_hours) and prob >= 50 and inches >= 0.03 and not out["snow"]
    out["wet_from"] = _clock(wet_hours[0]) if wet_hours else None
    out["cold"] = out["hi"] is not None and out["hi"] < 38
    out["hot"] = out["hi"] is not None and out["hi"] >= 92
    out["windy"] = out["wind"] >= 25
    out["line"] = _line(out)
    return out


def _clock(h: int) -> str:
    return f"{(h - 1) % 12 + 1} {'AM' if h < 12 else 'PM'}"


def _line(w: dict) -> str:
    if w["thunder"]:
        return f"Thunderstorms likely ({w['prob']}%)"
    if w["snow"]:
        return f"Snow likely from {w['wet_from'] or 'midday'}"
    if w["rain"]:
        return f"Rain likely from {w['wet_from']} ({w['prob']}%, about {w['inches']:g} in)"
    if w["cold"]:
        return f"Cold: a high of {w['hi']}°F"
    if w["hot"]:
        return f"Hot: up to {w['hi']}°F"
    if w["windy"]:
        return f"Windy: gusts to {w['wind']} mph"
    return f"Dry, {w['lo']}–{w['hi']}°F" if w["lo"] is not None else "Dry"


def outdoor_bad(w: dict | None) -> bool:
    return bool(w and (w["rain"] or w["snow"] or w["thunder"] or w["cold"] or w["windy"]))


def adjust(rx, w: dict | None) -> None:
    """Idempotent: the prescription is cached and this runs per request.
    Rain, snow, storms, cold or wind on an outdoor ride day: the trainer becomes the pick, same heart
    rate; the outdoor ride stays as an option. A run in rain stands (it's just wet) unless there's thunder."""
    if getattr(rx, "weather_swapped", False):
        return
    rx.weather = w
    if not w:
        return
    what = rx.what or ""
    low = what.lower()
    outdoor_bike = any(k in low for k in ("mountain bike", "road ride", "gravel"))
    if outdoor_bike and outdoor_bad(w):
        kind = next(k for k in ("mountain bike ride", "road ride", "gravel ride", "mountain bike") if k in low)
        mins = rx.max_minutes or 60
        cap = min(mins, 90)
        rx.options = [(f"{kind[:1].upper()}{kind[1:]}, outside", f"{w['line']}. If it clears, same heart rate, same length.",
                       "bad", "outside", "ride")] + list(rx.options or [])
        rx.what = what.replace(kind, "trainer ride").replace(f"up to {mins} minutes", f"up to {cap} minutes")
        rx.activity_why = f"{w['line']}, so the trainer: same heart rate, and nothing to dodge." + (
            f" Indoors, {cap} minutes is plenty: there's no coasting." if cap < mins else "")
        rx.weather_swapped = True
    elif "run" in low and w["thunder"]:
        rx.activity_why = f"{w['line']}: run before it builds, or take it to a treadmill."
