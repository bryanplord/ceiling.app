"""Magic-link sign-in for a short allowlist. No passwords, no user table.

How it works:
1. You enter your email on /login.
2. If it's on RX_ALLOWED_EMAILS, we email a link containing a signed token
   (email + expiry, HMAC-signed with RX_SECRET). Valid 15 minutes.
3. Clicking it sets a signed session cookie (30 days). Nothing is stored server-side.

Env:
  RX_ALLOWED_EMAILS  comma-separated, e.g. "a@x.com,b@y.com"
  RX_SECRET          long random string; changing it signs everyone out
  RX_BASE_URL        e.g. https://ceiling.app
  RESEND_API_KEY     https://resend.com — if unset, the link is printed to the server log
  RX_FROM_EMAIL      e.g. "Ceiling <signin@ceiling.app>" (domain verified in Resend)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import requests

LINK_TTL = 15 * 60
SESSION_TTL = 30 * 24 * 3600
COOKIE = "ceiling_session"


def _secret() -> bytes:
    s = os.getenv("RX_SECRET", "")
    if len(s) < 32:
        raise RuntimeError("RX_SECRET must be set to a random string of 32+ characters.")
    return s.encode()


def allowed() -> set[str]:
    return {e.strip().lower() for e in os.getenv("RX_ALLOWED_EMAILS", "").split(",") if e.strip()}


def enabled() -> bool:
    return bool(allowed())


def sign(kind: str, email: str, ttl: int) -> str:
    payload = f"{kind}|{email.lower()}|{int(time.time()) + ttl}"
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{mac}".encode()).decode().rstrip("=")


def verify(kind: str, token: str | None) -> str | None:
    """Return the email if the token is genuine, unexpired, the right kind, and still allowed."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        k, email, exp, mac = raw.split("|")
    except Exception:
        return None
    good = hmac.new(_secret(), f"{k}|{email}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, mac) or k != kind or int(exp) < time.time():
        return None
    return email if email in allowed() else None


def send_link(email: str) -> None:
    base = os.getenv("RX_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    link = f"{base}/auth?t={sign('link', email, LINK_TTL)}"
    key = os.getenv("RESEND_API_KEY", "")
    if not key:
        print(f"[sign-in link for {email}] {link}")   # local development
        return
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "from": os.getenv("RX_FROM_EMAIL", "Ceiling <signin@ceiling.app>"),
            "to": [email],
            "subject": "Your Ceiling sign-in link",
            "text": f"Sign in to Ceiling:\n\n{link}\n\nThis link works once in the next 15 minutes.",
        },
        timeout=20,
    )
    r.raise_for_status()
