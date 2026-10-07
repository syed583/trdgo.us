"""
One-time passcodes for phone signup, kept in process memory.

Short-lived (5 min) and single-use, with rate limits so the WhatsApp sender
cannot be used to spam a number or brute-forced on verify. In-memory is fine
here: a code is only valid for minutes and a restart just makes the user ask
for a new one. Not for multi-worker horizontal scale without a shared store.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Optional

import whatsapp_service as wa

_TTL = 300.0            # a code is valid for 5 minutes
_RESEND_GAP = 45.0      # seconds between sends to one number
_MAX_PER_HOUR = 6       # sends per number per hour
_MAX_ATTEMPTS = 5       # verify tries before a code is burned

_lock = threading.Lock()
# phone -> {code, expires, attempts, sent_at, hour_window:[timestamps]}
_codes: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def request_code(phone: str) -> dict:
    """Generate + send a code. Returns {status: SENT|RATE_LIMITED|ERROR, ...}."""
    phone = wa.normalize_phone(phone)
    if len(phone) < 8:
        return {"status": "ERROR", "detail": "Enter a valid phone number with country code."}
    now = _now()
    with _lock:
        rec = _codes.get(phone) or {}
        window = [t for t in rec.get("window", []) if now - t < 3600.0]
        if rec.get("sent_at") and now - rec["sent_at"] < _RESEND_GAP:
            wait = int(_RESEND_GAP - (now - rec["sent_at"])) + 1
            return {"status": "RATE_LIMITED", "detail": f"Please wait {wait}s before requesting another code."}
        if len(window) >= _MAX_PER_HOUR:
            return {"status": "RATE_LIMITED", "detail": "Too many codes requested. Try again later."}
        code = f"{secrets.randbelow(1000000):06d}"
        window.append(now)
        _codes[phone] = {"code": code, "expires": now + _TTL, "attempts": 0,
                         "sent_at": now, "window": window}

    res = wa.send_otp(phone, code)
    if res.get("status") != "SENT":
        return {"status": "ERROR", "detail": res.get("detail") or "Could not send the code."}
    return {"status": "SENT"}


def verify_code(phone: str, code: str) -> bool:
    """True once for a correct, unexpired code; burns it so it can't be reused."""
    phone = wa.normalize_phone(phone)
    code = (code or "").strip()
    now = _now()
    with _lock:
        rec = _codes.get(phone)
        if not rec or rec["expires"] < now:
            return False
        if rec["attempts"] >= _MAX_ATTEMPTS:
            _codes.pop(phone, None)
            return False
        rec["attempts"] += 1
        if secrets.compare_digest(rec["code"], code):
            _codes.pop(phone, None)   # single-use
            return True
        return False
