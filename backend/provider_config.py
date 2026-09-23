"""
Configuration and shared HTTP plumbing for external data providers.

Design rules this file enforces:

* API keys come from the environment only. Nothing is hardcoded, and no
  endpoint ever returns a key - only whether one is present.
* A missing key is a distinct, reportable state (PROVIDER_NOT_CONFIGURED),
  not an error and not an empty result. The UI must be able to tell
  "you haven't set this up" apart from "the provider is down".
* Every provider response is cached in PostgreSQL by the calling service, so
  a page view never triggers a provider call.
"""

from __future__ import annotations

import json
import os
import threading
import time
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# status vocabulary
# ---------------------------------------------------------------------------

OK = "OK"
PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
PROVIDER_OFFLINE = "PROVIDER_OFFLINE"
ENTITLEMENT_REQUIRED = "ENTITLEMENT_REQUIRED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
PARTIAL_DATA = "PARTIAL_DATA"
RATE_LIMITED = "RATE_LIMITED"
TEST_DATA = "TEST_DATA"


class ProviderSpec:
    """One external provider: where its key lives and what it is for."""

    def __init__(self, key_env: str, name: str, purpose: str,
                 signup: str, base_url: str):
        self.key_env = key_env
        self.name = name
        self.purpose = purpose
        self.signup = signup
        self.base_url = base_url

    @property
    def api_key(self) -> Optional[str]:
        value = (os.getenv(self.key_env) or "").strip()
        return value or None

    @property
    def configured(self) -> bool:
        return self.api_key is not None

    def status(self) -> dict:
        """Never includes the key itself - only whether one is set."""
        if not self.configured:
            return {
                "provider": self.name,
                "status": PROVIDER_NOT_CONFIGURED,
                "configured": False,
                "detail": (
                    f"{self.name} is not configured. Set {self.key_env} in "
                    f"backend/.env to enable {self.purpose}."
                ),
                "env_var": self.key_env,
                "purpose": self.purpose,
                "signup": self.signup,
            }
        return {
            "provider": self.name,
            "status": OK,
            "configured": True,
            "detail": f"{self.name} key present ({self.purpose})",
            "env_var": self.key_env,
            "purpose": self.purpose,
        }


BENZINGA = ProviderSpec(
    key_env="BENZINGA_API_KEY",
    name="Benzinga",
    purpose="the earnings calendar, actual results and earnings history",
    signup="https://www.benzinga.com/apis/",
    base_url="https://api.benzinga.com/api",
)

ALPHA_VANTAGE = ProviderSpec(
    key_env="ALPHA_VANTAGE_API_KEY",
    name="Alpha Vantage",
    purpose="analyst estimates and estimate revisions",
    signup="https://www.alphavantage.co/support/#api-key",
    base_url="https://www.alphavantage.co/query",
)

TWELVE_DATA = ProviderSpec(
    key_env="TWELVE_DATA_API_KEY",
    name="Twelve Data",
    purpose="daily price bars and quotes when TWS is not running",
    signup="https://twelvedata.com/pricing",
    base_url="https://api.twelvedata.com",
)

MARKETAUX = ProviderSpec(
    key_env="MARKETAUX_API_TOKEN",
    name="Marketaux",
    purpose="company news headlines with per-symbol sentiment",
    signup="https://www.marketaux.com/",
    base_url="https://api.marketaux.com/v1",
)

ALL_PROVIDERS = [BENZINGA, ALPHA_VANTAGE, TWELVE_DATA, MARKETAUX]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

USER_AGENT = "US-Stock-Reader/1.0 (personal research)"
DEFAULT_TIMEOUT = 20.0


def redact(text: Any) -> str:
    """
    Strip any configured API key out of provider text before it is logged,
    stored or returned.

    Alpha Vantage echoes the key back inside its rate-limit message, so a
    verbatim log would write the credential into PostgreSQL and into an API
    response. Everything that surfaces provider text goes through here.
    """
    out = str(text or "")
    for spec in ALL_PROVIDERS:
        key = spec.api_key
        if key and key in out:
            out = out.replace(key, "***REDACTED***")
    return out


class ProviderError(RuntimeError):
    def __init__(self, status: str, detail: str):
        detail = redact(detail)
        super().__init__(detail)
        self.status = status
        self.detail = detail


def fetch_json(
    url: str,
    params: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Optional[dict] = None,
) -> Any:
    """
    GET a JSON document, translating transport failures into provider states.

    HTTP 401/403 means the key is wrong or unentitled rather than the service
    being down, and 429 means we are being paced - those are different
    problems with different fixes, so they are reported separately.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                 **(headers or {})},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderError(
                ENTITLEMENT_REQUIRED,
                f"HTTP {exc.code}: the API key was rejected or the plan does "
                f"not include this endpoint.",
            ) from exc
        if exc.code == 429:
            raise ProviderError(
                RATE_LIMITED, "HTTP 429: provider rate limit reached."
            ) from exc
        raise ProviderError(
            PROVIDER_OFFLINE, f"HTTP {exc.code} from provider."
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(
            PROVIDER_OFFLINE, f"Could not reach provider: {exc}"
        ) from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(
            DATA_UNAVAILABLE, "Provider returned a non-JSON response."
        ) from exc


# ---------------------------------------------------------------------------
# rate-limit cooldown
# ---------------------------------------------------------------------------

# A daily quota does not refill in the next few seconds, so once a provider
# says "rate limited" every further call is guaranteed to fail -- and each one
# still costs a full network round trip. Alpha Vantage alone was burning ~8s
# per scored symbol that way. Remember the refusal and skip until it lapses.
_COOLDOWN_UNTIL: dict[str, float] = {}
_COOLDOWN_LOCK = threading.Lock()

RATE_LIMIT_COOLDOWN = float(os.getenv("PROVIDER_RATE_LIMIT_COOLDOWN", "900"))


def start_cooldown(provider: str, seconds: float = RATE_LIMIT_COOLDOWN) -> None:
    """Stop calling `provider` until `seconds` have passed."""
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL[provider] = time.time() + seconds


def in_cooldown(provider: str) -> bool:
    """True while `provider` is being skipped after a rate-limit refusal."""
    with _COOLDOWN_LOCK:
        until = _COOLDOWN_UNTIL.get(provider)
    if not until:
        return False
    if time.time() >= until:
        with _COOLDOWN_LOCK:
            _COOLDOWN_UNTIL.pop(provider, None)
        return False
    return True


def clear_cooldown(provider: str) -> None:
    with _COOLDOWN_LOCK:
        _COOLDOWN_UNTIL.pop(provider, None)


def apply_last_fetch(base: dict, last_status: Optional[str]) -> dict:
    """
    Let a rejected key override a "configured" OK headline.

    ProviderSpec.status() only knows whether a key is present, so a revoked,
    mistyped or unentitled key still reported OK and painted the Settings chip
    green while every sync failed. A hard auth refusal is a persistent fact
    about the key, so it becomes the headline; transient states (rate limits)
    stay in last_status and do not.
    """
    if base.get("status") == OK and last_status == ENTITLEMENT_REQUIRED:
        base["status"] = ENTITLEMENT_REQUIRED
        base["detail"] = (
            f"{base.get('name') or 'Provider'} rejected the configured key "
            f"(HTTP 401). Check the key or the plan's endpoint access."
        )
    return base


def provider_matrix() -> dict:
    """Configuration state of every external provider, for Settings."""
    return {
        spec.key_env: spec.status() for spec in ALL_PROVIDERS
    }
