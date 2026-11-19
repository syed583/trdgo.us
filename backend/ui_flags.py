"""Who sees the new interface.

The redesign ships behind a per-account flag so the owner can look at it on the real server, with real
data, before anyone else does. The alternative - a date, or a global switch - gives everyone the half-built
version at once, which is how a redesign gets judged on its worst day.

FAIL CLOSED. An unreadable or absent setting means NOBODY sees the new shell: the old one is the known-good
interface, so an error must land there, not on the new code. There is no "default on" here and there should
never be one.

The list is an environment variable rather than a database row on purpose. It is operator configuration,
not user data: it needs no migration, it cannot be changed by anything that reaches the API, and turning
the redesign off for everyone is a restart with the variable cleared - which is the fastest rollback there
is, and it works even if the database is the thing that is broken.

    NEW_UI_USERS=owner                 one account
    NEW_UI_USERS=owner,second_account  several, comma separated
    NEW_UI_USERS=                      nobody (the default)
    NEW_UI_USERS=*                     everyone - only after the redesign is approved
"""
from __future__ import annotations

import os
from typing import Optional

ENV_VAR = "NEW_UI_USERS"


def _raw() -> str:
    try:
        return os.environ.get(ENV_VAR) or ""
    except Exception:  # noqa: BLE001 - an environment read must never break sign-in
        return ""


def allowed_accounts() -> set[str]:
    """The accounts named in the setting, lowercased. Empty means nobody."""
    return {p.strip().lower() for p in _raw().split(",") if p.strip()}


def new_ui_for(username: Optional[str]) -> bool:
    """True when this account should be served the redesigned shell.

    A missing username is never allowed through: an anonymous caller has no account to match, and
    guessing on their behalf is exactly the fail-open behaviour this module exists to prevent.
    """
    if not username:
        return False
    try:
        names = allowed_accounts()
    except Exception:  # noqa: BLE001
        return False
    if "*" in names:
        return True
    return str(username).strip().lower() in names
