from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from plugins.base import Plugin

if TYPE_CHECKING:
    from services.twitter import TwitterService

logger = logging.getLogger(__name__)


class TwitterPlugin(Plugin):

    def __init__(self, twitter_service: "TwitterService") -> None:
        self._svc = twitter_service
        self._active = False

    @property
    def name(self) -> str:
        return "twitter"

    def start(self) -> None:
        self._active = True
        logger.info("TwitterPlugin: started")

    def stop(self) -> None:
        self._active = False

    def publish(self, content: str, image_url: Optional[str] = None, **kwargs) -> dict[str, Any]:
        if not self._active:
            return {"ok": False, "error": "plugin not started"}
        try:
            image_bytes: Optional[bytes] = kwargs.get("image_bytes")
            result = self._svc.cross_post(content, image_bytes=image_bytes)
            return {"ok": bool(result), "platform": "twitter", "result": result}
        except Exception as exc:
            logger.error("TwitterPlugin.publish: %s", exc)
            return {"ok": False, "error": str(exc), "platform": "twitter"}

    def health(self) -> dict[str, Any]:
        try:
            from config.settings import get_settings
            s = get_settings()
            ok = bool(getattr(s, "twitter_api_key", None))
        except Exception:
            ok = False
        return {"ok": ok, "platform": "twitter", "active": self._active}
