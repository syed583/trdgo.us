"""Phase 6.1 development-only configuration guard (no import side effects).

Kept separate from ``dev_backend`` so it can be unit-tested as a pure function
without constructing the app. It resolves and validates the TRDGO-US-DEV
connection and REFUSES production: wrong env, the production project ref, an
unknown ref, missing/malformed config, or a missing SSL requirement all raise.
It reads the dev URL ONLY from ``INSTITUTIONAL_DATABASE_URL`` / ``.env.dev`` and
never from the production ``.env``'s ``DATABASE_URL``.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

PROD_REF = "ddetojbocaxhcfxtcopc"
DEV_REF = "qzmsozpyypjdxzfprhoz"


def require_dev_config() -> str:
    """Return the validated dev DB URL, or raise RuntimeError. Production-proof."""
    if (os.getenv("TRDGO_ENV") or "").strip().lower() != "development":
        raise RuntimeError("DEV GUARD: TRDGO_ENV must be 'development'")

    from dotenv import dotenv_values
    here = os.path.dirname(os.path.abspath(__file__))
    envd = dotenv_values(os.path.join(here, ".env.dev"))

    url = (os.getenv("INSTITUTIONAL_DATABASE_URL") or envd.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DEV GUARD: no development DATABASE_URL (set .env.dev)")
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError("DEV GUARD: malformed database URL")

    norm = url if url.startswith("postgresql+psycopg") else url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "sslmode=" not in norm:
        norm += ("&" if "?" in norm else "?") + "sslmode=require"

    u = urlparse(norm.replace("postgresql+psycopg", "postgresql"))
    user = u.username or ""
    ref = user.split(".", 1)[1] if "." in user else None
    if ref == PROD_REF:
        raise RuntimeError("DEV GUARD: refusing PRODUCTION project ref")
    if ref != DEV_REF:
        raise RuntimeError("DEV GUARD: project ref is not the approved development project")
    if "sslmode=require" not in norm:
        raise RuntimeError("DEV GUARD: SSL (sslmode=require) is mandatory")

    # Publish the validated dev URL; deliberately never set DATABASE_URL.
    os.environ["INSTITUTIONAL_DATABASE_URL"] = norm
    # Require a DISTINCT session secret for the dev backend so it can never fall
    # back to the on-disk persisted secret shared with production -- that was the
    # Phase 6.1 cross-environment-cookie gap. Fail closed if one is not provided.
    secret = os.environ.get("SESSION_SECRET") or envd.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "DEV GUARD: SESSION_SECRET is required (set it in .env.dev) so the dev "
            "backend uses its own signing key and never shares production cookies")
    os.environ["SESSION_SECRET"] = secret
    if envd.get("ACCESS_PASSWORD"):
        os.environ.setdefault("ACCESS_PASSWORD", envd["ACCESS_PASSWORD"])
    return norm
