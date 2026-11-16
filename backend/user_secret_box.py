"""C.1: encrypting a user's broker password at rest. Nothing else in the application stores a secret a user
typed, so this is deliberately small, deliberately boring, and it FAILS CLOSED.

Three rules:
  1. No key, no storage. If MT5_USER_KEY is unset or malformed, `available()` is False and `seal()` raises.
     The feature then reports itself as not configured. It never falls back to storing anything in the clear.
  2. The plaintext never leaves this module except through `open_()`, which only the connect path calls.
     It is never returned by a route, never logged, never put in an error message.
  3. The key lives in the environment on the server, never in git and never in the database beside the data
     it protects - a key stored next to the ciphertext protects nothing.

MT5_USER_KEY is a Fernet key: 32 random bytes, url-safe base64. Generate one with
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Rotating it makes every stored password undecryptable, which shows up as every user needing to reconnect -
not as silent breakage: `open_()` answers None and the caller reports RECONNECT_NEEDED.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

_log = logging.getLogger("user_mt5")

ENV_KEY = "MT5_USER_KEY"


def _fernet():
    """The cipher, or None when the library or the key is missing. Never raises on import."""
    key = (os.getenv(ENV_KEY) or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        # The library is not pinned in requirements.txt, so a server may not have it. Saying so once is
        # better than a stack trace on every request.
        _log.error("the cryptography package is not installed: a user's MT5 password cannot be stored")
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except Exception:  # noqa: BLE001 - a malformed key must disable the feature, never crash a request
        _log.error("%s is set but is not a valid Fernet key: the MT5 connect feature stays disabled", ENV_KEY)
        return None


def available() -> bool:
    """True when a password could actually be stored. The UI asks this before offering to connect."""
    return _fernet() is not None


def why_unavailable() -> Optional[str]:
    """A reason an operator can act on. Deliberately says nothing about the key's value."""
    if available():
        return None
    if not (os.getenv(ENV_KEY) or "").strip():
        return "MT5_USER_KEY is not set on the server"
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return "the cryptography package is not installed on the server"
    return "MT5_USER_KEY is set but is not a valid key"


def seal(plaintext: str) -> str:
    """Encrypt. Raises when storage is not configured - better a refusal than a password in the clear."""
    f = _fernet()
    if f is None:
        raise RuntimeError("cannot store an MT5 password: %s" % (why_unavailable() or "not configured"))
    if not isinstance(plaintext, str) or not plaintext:
        raise ValueError("refusing to store an empty password")
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def open_(ciphertext: Optional[str]) -> Optional[str]:
    """Decrypt, or None when it cannot be read (no key, rotated key, corrupt row). Never raises."""
    if not ciphertext:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - an unreadable secret means "ask the user to reconnect"
        return None


def mask(login: Optional[str | int]) -> str:
    """What a reader may see of an account number: the last two digits only."""
    s = str(login or "").strip()
    if len(s) <= 2:
        return "••"
    return "•" * (len(s) - 2) + s[-2:]
