"""Infrastructure adapter for the content-intelligence application port."""
from __future__ import annotations

from services.content_intelligence import ContentIntelligenceEngine


class ContentIntelligenceAdapter:
    def __init__(self, engine: ContentIntelligenceEngine | None = None):
        self._engine = engine or ContentIntelligenceEngine()

    async def analyze(self, topic: str, *, context: str = "", max_results: int = 8,
                      fetch_top: int = 4, workspace_id: str = "platform"):
        return await self._engine.analyze(
            topic,
            context=context,
            max_results=max_results,
            fetch_top=fetch_top,
            workspace_id=workspace_id,
        )
