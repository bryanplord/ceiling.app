"""Simple email + password sign-in for a short list of accounts. No user table.

Accounts live in one setting, RX_USERS, as  email=salt$hash  pairs, comma-separated.
Passwords are never stored: only a salted PBKDF2-SHA256 hash. Create an entry with:

    python -m rx.web.auth someone@example.com

A successful sign-in sets a signed session cookie (30 days). Nothing is stored server-side.
RX_SECRET (32+ random characters) signs the cookie; changing it signs everyone out.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import sys
import time

SESSION_TTL = 30 * 24 * 3600
COOKIE = "ceiling_session"
ITERATIONS = 600_000


def _secret() -> bytes:
    s = os.getenv("RX_SECRET", "")
    if len(s) < 32:
        raise RuntimeError("RX_SECRET must be set to a random string of 32+ characters.")
    return s.encode()


def users() -> dict[str, str]:
    out = {}
    for pair in os.getenv("RX_USERS", "").split(","):
        email, _, h = pair.strip().partition("=")
        if email and h:
            out[email.strip().lower()] = h.strip()
    return out


def enabled() -> bool:
    return bool(users())


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"{salt}${h}"


def check_password(email: str, password: str) -> bool:
    stored = users().get(email.strip().lower())
    if not stored or "$" not in stored:
        hash_password(password)          # same work either way: no timing hint
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def sign(kind: str, email: str, ttl: int) -> str:
    payload = f"{kind}|{email.lower()}|{int(time.time()) + ttl}"
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{mac}".encode()).decode().rstrip("=")


def verify(kind: str, token: str | None) -> str | None:
    """Return the email if the token is genuine, unexpired, the right kind, and the account still exists."""
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
    return email if email in users() else None


if __name__ == "__main__":  # python -m rx.web.auth someone@example.com
    email = sys.argv[1].strip().lower()
    pw = getpass.getpass(f"Password for {email}: ")
    print(f"{email}={hash_password(pw)}")


def seal(text: str) -> str:
    """Encrypt a small secret (an API key) with RX_SECRET before it's stored:
    SHA-256 in counter mode for the keystream, HMAC-SHA256 to detect tampering."""
    if not text:
        return ""
    nonce = secrets.token_bytes(16)
    data = text.encode()
    stream = b"".join(hashlib.sha256(_secret() + nonce + i.to_bytes(4, "big")).digest()
                      for i in range(len(data) // 32 + 1))
    ct = bytes(a ^ b for a, b in zip(data, stream))
    mac = hmac.new(_secret(), nonce + ct, hashlib.sha256).digest()
    return "s1:" + base64.urlsafe_b64encode(nonce + mac + ct).decode()


def unseal(token: str) -> str:
    if not token or not token.startswith("s1:"):
        return ""
    try:
        raw = base64.urlsafe_b64decode(token[3:])
        nonce, mac, ct = raw[:16], raw[16:48], raw[48:]
        if not hmac.compare_digest(mac, hmac.new(_secret(), nonce + ct, hashlib.sha256).digest()):
            return ""
        stream = b"".join(hashlib.sha256(_secret() + nonce + i.to_bytes(4, "big")).digest()
                          for i in range(len(ct) // 32 + 1))
        return bytes(a ^ b for a, b in zip(ct, stream)).decode()
    except Exception:
        return ""
