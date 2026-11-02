"""Shared Unusual Whales key: what can and cannot be verified from this machine, and a gate that keeps a live run BLOCKED until the owner has acknowledged the rest.

Facts this module encodes (sources: unusualwhales_service.py header comment citing Unusual Whales support 2026-10-23, uw_slots.py, uw_budget.py, project notes):
  * the plan has no daily or per-minute cap; the ONLY provider limit is THREE simultaneous in-flight REST requests per token, counted across every machine and process that uses it (a 4th gets a 429
    `concurrent_limit_exceeded`);
  * this application bounds its own process (priority gate) and this machine (uw_slots, OS file locks) - a non-production machine defaults to ONE slot;
  * another application (us-signals-platform, recorder with 4 workers) and the production server use the same token. Their requests are NOT visible from here; only the provider's response headers would
    show total use, and reading them costs a request;
  * the collector's `hourly_allowance` (100 in market hours) is our own self-imposed rail, not a provider limit, and its `existing_usage_fn` returns 0, so it does not see the other consumers.
So concurrent use by another consumer can neither be seen nor prevented from here. The gate therefore reports NOT_VERIFIED unless the owner attests, and it refuses outright if a known local consumer is running.
Nothing here sends a request, stops another application, or changes a limit.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

UTC, ET = timezone.utc, ZoneInfo("America/New_York")
PROVIDER_LIMIT = "3 simultaneous in-flight requests per token, across all machines and processes (no daily or per-minute cap)"
KNOWN_CONSUMER_PATTERNS = ("record.py", "measure_feed", "prod_supervisor", "us-signals-platform")
KNOWN_TASKS = ("Prod-Signals-1-backend", "Prod-Signals-3-recorder", "Prod-Signals-4-engine")
ATTESTATION_MAX_AGE_H = 24.0
PROVIDER_SLOTS = 3
ENVIRONMENTS = ("localhost", "production")


def this_environment(env=None) -> str:
    """production when APP_ENV=production (the server), otherwise localhost."""
    import os
    return "production" if ((env if env is not None else os.environ).get("APP_ENV") or "").strip().lower() == "production" else "localhost"


def concurrency_policy(local_inflight: int, production_inflight: int) -> dict:
    """Static partition of the token's three slots. Two environments cannot see each other's requests, so the ONLY guarantee available without a shared coordinator is that the sum of the two
    hard caps does not exceed three AND each environment really enforces its own cap (process gate + machine slots). 1 + 3 (today's production default) = 4 is NOT safe; 1 + 2 = 3 is."""
    total = local_inflight + production_inflight
    return {"local_cap": local_inflight, "production_cap": production_inflight, "sum": total, "safe_without_coordinator": total <= PROVIDER_SLOTS,
            "note": "holds only if each environment enforces its cap; it does not make provider collection on both safe at 1 + 3"}


def list_processes() -> Optional[list]:
    """Command lines of running processes (Windows, read only). None when it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"], capture_output=True, text=True, timeout=30)
        return [ln for ln in out.stdout.splitlines() if ln.strip()] if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def enabled_tasks(names=KNOWN_TASKS) -> Optional[list]:
    """Scheduled tasks of the other application that are NOT disabled (read only). None when unreadable."""
    if sys.platform != "win32":
        return None
    try:
        cmd = "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -in @(%s) -and $_.State -ne 'Disabled' } | ForEach-Object { $_.TaskName }" % ",".join("'%s'" % n for n in names)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30)
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()] if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def in_us_session(now: datetime) -> bool:
    n = now.astimezone(ET)
    return n.weekday() < 5 and (n.hour, n.minute) >= (9, 30) and n.hour < 16


def read_attestation(path, now: datetime) -> dict:
    """The owner's acknowledgement (a small JSON file). It never claims the shared use was measured; it records who accepted the residual risk and when."""
    if not path:
        return {"valid": False, "why": "no attestation file"}
    p = Path(path)
    if not p.exists():
        return {"valid": False, "why": "attestation file not found"}
    try:
        a = json.loads(p.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(a["attested_at"])
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
    except (ValueError, KeyError, OSError):
        return {"valid": False, "why": "attestation unreadable (needs attested_by, attested_at)"}
    if not a.get("attested_by") or a.get("acknowledges_shared_token_three_slot_limit") is not True or a.get("acknowledges_production_and_other_app_use_not_visible") is not True:
        return {"valid": False, "why": "attestation lacks the required acknowledgements"}
    if now - at > timedelta(hours=ATTESTATION_MAX_AGE_H) or at > now + timedelta(minutes=5):
        return {"valid": False, "why": "attestation is older than %.0f h or dated in the future" % ATTESTATION_MAX_AGE_H}
    return {"valid": True, "attested_by": a["attested_by"], "attested_at": at.isoformat(), "market_hours_ok": a.get("market_hours_ok") is True, "designated_collector": a.get("designated_collector"),
            "production_provider_traffic_paused": a.get("production_provider_traffic_paused") is True, "production_uw_max_inflight": a.get("production_uw_max_inflight")}


def designation_problem(att: dict, here: str) -> Optional[str]:
    """Only ONE environment may make live provider requests unless the caps are partitioned. The attestation must name the designated collector and it must be THIS environment; a localhost collector also
    needs production's provider traffic paused, or production capped at 2 in flight (1 + 2 = 3)."""
    d = att.get("designated_collector")
    if d not in ENVIRONMENTS:
        return "attestation must name designated_collector: localhost or production"
    if d != here:
        return "this environment (%s) is not the designated collector (%s)" % (here, d)
    if here == "localhost":
        cap = att.get("production_uw_max_inflight")
        if not att.get("production_provider_traffic_paused") and not (isinstance(cap, int) and 1 <= cap and cap + 1 <= PROVIDER_SLOTS):
            return "localhost collection needs production provider traffic paused, or production UW_MAX_INFLIGHT <= 2 (1 + 2 = 3)"
    return None


def evaluate(now: datetime, attestation_path=None, *, processes: Callable[[], Optional[list]] = list_processes, tasks: Callable[[], Optional[list]] = enabled_tasks) -> dict:
    checks = []
    procs = processes()
    hit = [p[:160] for p in (procs or []) if any(k in p for k in KNOWN_CONSUMER_PATTERNS)]
    checks.append({"check": "other application processes on this machine", "result": "UNREADABLE" if procs is None else "RUNNING" if hit else "NONE_RUNNING", "detail": hit[:3]})
    en = tasks()
    checks.append({"check": "other application scheduled tasks enabled", "result": "UNREADABLE" if en is None else "ENABLED" if en else "ALL_DISABLED", "detail": en or []})
    checks.append({"check": "production server and other machines using the token", "result": "NOT_VISIBLE", "detail": "no shared counter exists; only provider headers show total use and reading them needs a request"})
    checks.append({"check": "this process", "result": "ONE_IN_FLIGHT", "detail": "pilot uses UW_MAX_INFLIGHT=1, sequential, 2 s spacing; 429 or auth error stops the run, no retry"})
    session = in_us_session(now)
    checks.append({"check": "US market session", "result": "IN_SESSION" if session else "OUTSIDE_SESSION", "detail": "production traffic is heaviest in session"})
    att = read_attestation(attestation_path, now)
    conflict = bool(hit) or bool(en) or procs is None or en is None
    if conflict:
        status, blocks = "NOT_VERIFIED", True
        why = "another consumer is running/enabled on this machine, or this machine could not be inspected"
    elif att["valid"] and designation_problem(att, this_environment()):
        status, blocks = "NOT_VERIFIED", True
        why = designation_problem(att, this_environment())
    elif att["valid"] and (not session or att.get("market_hours_ok")):
        status, blocks, why = "OWNER_ATTESTED", False, "no local conflict found; cross-machine use is not visible and is covered only by the owner's attestation"
    else:
        status, blocks = "NOT_VERIFIED", True
        why = "cross-machine use cannot be verified here and no valid owner attestation" + (" (in session: attestation must set market_hours_ok)" if session and att["valid"] else "") + (": " + att["why"] if not att["valid"] else "")
    return {"status": status if status != "NOT_VERIFIED" else "SHARED KEY SAFETY NOT VERIFIED", "blocks_live": blocks, "reason": why, "checks": checks, "provider_limit": PROVIDER_LIMIT, "attestation": att,
            "note": "A passing gate is not proof: the other consumers' in-flight requests cannot be seen from here."}
