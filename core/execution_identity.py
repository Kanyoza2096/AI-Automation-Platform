"""Canonical identities for externally visible publishing operations."""
from __future__ import annotations

import hashlib
from typing import Optional


def make_publish_idempotency_key(
    workspace_id: str, brand_id: str, platform: str, destination_account_id: str,
    topic: str, caption: str, image_url: str = "", image_bytes: Optional[bytes] = None,
) -> str:
    image_digest = hashlib.sha256(image_bytes).hexdigest() if image_bytes else ""
    material = "|".join([
        workspace_id or "", brand_id or "", platform or "",
        destination_account_id or "", topic or "", caption or "",
        image_url or "", image_digest,
    ])
    return "exec_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
