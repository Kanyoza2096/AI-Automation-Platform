from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from plugins.base import Plugin

if TYPE_CHECKING:
    from services.facebook import FacebookService

logger = logging.getLogger(__name__)


class FacebookPlugin(Plugin):
    """Wraps FacebookService behind the Plugin interface so the platform can
    publish to Facebook without knowing its implementation details."""

    def __init__(self, facebook_service: "FacebookService") -> None:
        self._svc = facebook_service
        self._active = False

    @property
    def name(self) -> str:
        return "facebook"

    def start(self) -> None:
        self._active = True
        logger.info("FacebookPlugin: started")

    def stop(self) -> None:
        self._active = False
        logger.info("FacebookPlugin: stopped")

    def publish(
        self,
        content: str,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        **kwargs,
    ) -> dict[str, Any]:
        if not self._active:
            return {"ok": False, "error": "plugin not started"}
        try:
            post_id = self._svc.post_to_page(
                caption=content,
                image_bytes=image_bytes or None,
                image_url=image_url if not image_bytes else None,
            )
            return {"ok": bool(post_id), "post_id": post_id, "platform": "facebook"}
        except Exception as exc:
            logger.error("FacebookPlugin.publish: %s", exc)
            return {"ok": False, "error": str(exc), "platform": "facebook"}

    def health(self) -> dict[str, Any]:
        try:
            ok = self._svc is not None and bool(
                getattr(self._svc, "_page_access_token", None)
            )
        except Exception:
            ok = False
        return {"ok": ok, "platform": "facebook", "active": self._active}
