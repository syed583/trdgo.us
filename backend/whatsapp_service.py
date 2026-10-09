"""
WhatsApp OTP sender (ibittechnologies WhatsApp Business panel).

Sends the one-time code via the account's approved template. All credentials
come from the environment -- nothing is hardcoded or committed:

    WHATSAPP_API_TOKEN     Bearer token (required)
    WHATSAPP_PHONE_ID      sender phone-number id (default from the panel setup)
    WHATSAPP_API_BASE      API base incl. version (default .../V23.0)
    WHATSAPP_TEMPLATE      approved template name (default "verify02")
    WHATSAPP_LANG          template language code (default "en_GB")

The "otpp" template has a body parameter (the code) and a URL button whose
dynamic parameter is also the code (the one-tap copy/verify button), so the
payload sends both. Set WHATSAPP_TEMPLATE to a body-only template name to fall
back to a plain body-only message (no button component).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

_DEFAULT_BASE = "https://panel.ibittechnologies.com/V23.0"
_DEFAULT_PHONE_ID = "1419616574558775"


def _token() -> Optional[str]:
    return (os.getenv("WHATSAPP_API_TOKEN") or "").strip() or None


def configured() -> bool:
    return _token() is not None


def normalize_phone(raw: str) -> str:
    """Digits only (country code + number), as the WhatsApp API expects."""
    return re.sub(r"\D", "", raw or "")


def send_otp(phone: str, code: str) -> dict:
    """
    Send the verification code to `phone` via the approved template. Returns
    {"status": "SENT"} or {"status": "ERROR", "detail": ...}. Never raises.
    """
    token = _token()
    if not token:
        return {"status": "ERROR", "detail": "WhatsApp is not configured."}
    to = normalize_phone(phone)
    if len(to) < 8:
        return {"status": "ERROR", "detail": "Enter a valid phone number with country code."}

    base = (os.getenv("WHATSAPP_API_BASE") or _DEFAULT_BASE).rstrip("/")
    phone_id = os.getenv("WHATSAPP_PHONE_ID") or _DEFAULT_PHONE_ID
    # Default to the Authentication-category template. OTP must NOT go through a
    # Marketing-category template -- Meta drops those with error 131049
    # ("healthy ecosystem engagement" frequency cap). verify_01 was Marketing.
    template = os.getenv("WHATSAPP_TEMPLATE") or "verify02"
    lang = os.getenv("WHATSAPP_LANG") or "en_GB"

    components = [
        {"type": "body", "parameters": [{"type": "text", "text": code}]},
    ]
    # Body-only templates (e.g. "verify_01") send just the code. Only the "otpp"
    # template has a dynamic URL button at index 0, whose parameter is the code.
    if template == "otpp":
        components.append({
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [{"type": "text", "text": code}],
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
            "components": components,
        },
        # Opaque tag the provider echoes back on delivery/status callbacks, so an
        # OTP send can be traced without exposing the code or recipient.
        "biz_opaque_callback_data": f"otp:{to[-4:]}",
    }
    try:
        req = urllib.request.Request(
            f"{base}/{phone_id}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "replace")
        # Surface the provider's own answer so a message that is accepted but
        # routed to the wrong number (or a template error returned with HTTP 200)
        # is visible instead of being reported as a blind "SENT".
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        msg = (body.get("messages") or [{}])[0]
        contact = (body.get("contacts") or [{}])[0]
        if body.get("error"):
            err = body["error"]
            detail = err.get("message") if isinstance(err, dict) else str(err)
            return {"status": "ERROR", "detail": f"Provider error: {detail}"}
        return {"status": "SENT", "message_id": msg.get("id"),
                "message_status": msg.get("message_status"),
                "routed_to": contact.get("wa_id")}
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            detail = str(e)
        return {"status": "ERROR", "detail": f"Could not send the code: {detail}"}
    except Exception as e:  # noqa: BLE001 - a provider failure is not a server fault
        return {"status": "ERROR", "detail": f"Could not send the code: {e}"}
