from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

from plugins.base import Plugin

if TYPE_CHECKING:
    from services.linkedIn import LinkedInService

logger = logging.getLogger(__name__)


class LinkedInPlugin(Plugin):

    def __init__(self, linkedin_service: "LinkedInService", social_repo: "SocialAccountRepository" = None) -> None:
        self._svc = linkedin_service
        self._social_repo = social_repo
        self._active = False
        self._connector = None

    @property
    def name(self) -> str:
        return "linkedin"

    def configure(self, access_token: str, person_urn: str) -> None:
        self._svc.access_token = access_token
        self._svc.person_urn = person_urn
        from services.connectors.linkedin_connector import LinkedInConnector
        self._connector = LinkedInConnector()
        self._connector._token = access_token
        self._connector._person_urn = person_urn

    def start(self) -> None:
        self._active = True
        logger.info("LinkedInPlugin: started")

    def stop(self) -> None:
        self._active = False

    def publish(self, content: str, image_url: Optional[str] = None, brand: Optional[Any] = None, **kwargs) -> dict[str, Any]:
        if not self._active:
            return {"ok": False, "error": "plugin not started"}
            
        connector = self._connector
        if brand and self._social_repo:
            try:
                account = self._social_repo.find_by_brand_and_platform(brand.id, "linkedin")
                if account:
                    from services.connectors.connector_factory import create_connector
                    connector = create_connector(account)
            except Exception as e:
                logger.error("Failed to load LinkedIn connector for brand %s: %s", brand.name, e)

        if not connector:
            return {"ok": False, "error": "LinkedIn connector not configured", "platform": "linkedin"}

        res = connector.publish(content, image_url=image_url)
        return {"ok": res.ok, "platform": "linkedin", "post_id": res.post_id, "result": res.post_id, "error": res.error}

    def health(self) -> dict[str, Any]:
        if not self._connector:
            return {"ok": False, "platform": "linkedin", "active": self._active}
        h = self._connector.health()
        return {"ok": h.ok, "platform": "linkedin", "active": self._active, "error": h.error}

    @property
    def connector(self) -> Optional[Any]:
        return self._connector
