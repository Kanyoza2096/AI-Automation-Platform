from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def sign_webhook(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = sign_webhook(body, secret)
    supplied = str(signature or "").strip().removeprefix("sha256=")
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def safe_event_id(payload: dict[str, Any], provider: str) -> str:
    explicit = str(payload.get("event_id") or payload.get("transaction_id") or payload.get("external_order_id") or "").strip()
    if explicit:
        return f"{provider}:{explicit}"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{provider}:sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
