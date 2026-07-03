from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from plugins.base import Plugin

if TYPE_CHECKING:
    from services.linkedIn import LinkedInService

logger = logging.getLogger(__name__)


class LinkedInPlugin(Plugin):

    def __init__(self, linkedin_service: "LinkedInService") -> None:
        self._svc = linkedin_service
        self._active = False

    @property
    def name(self) -> str:
        return "linkedin"

    def start(self) -> None:
        self._active = True
        logger.info("LinkedInPlugin: started")

    def stop(self) -> None:
        self._active = False

    def publish(self, content: str, image_url: Optional[str] = None, **kwargs) -> dict[str, Any]:
        if not self._active:
            return {"ok": False, "error": "plugin not started"}
        try:
            result = self._svc.post_article(content, image_url=image_url)
            return {"ok": bool(result), "platform": "linkedin", "result": result}
        except Exception as exc:
            logger.error("LinkedInPlugin.publish: %s", exc)
            return {"ok": False, "error": str(exc), "platform": "linkedin"}

    def health(self) -> dict[str, Any]:
        try:
            from config.settings import get_settings
            s = get_settings()
            ok = bool(getattr(s, "linkedin_access_token", None))
        except Exception:
            ok = False
        return {"ok": ok, "platform": "linkedin", "active": self._active}
