"""Application boundary for content-intelligence use cases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ContentIntelligencePort(Protocol):
    async def analyze(self, topic: str, *, context: str = "", max_results: int = 8,
                      fetch_top: int = 4, workspace_id: str = "platform") -> Any: ...


@dataclass(frozen=True)
class ContentIntelligenceContext:
    workspace_id: str

    def validate(self) -> None:
        if not self.workspace_id or not self.workspace_id.strip():
            raise ValueError("workspace scope is required")


@dataclass(frozen=True)
class AnalyzeContentCommand:
    topic: str
    context: str = ""
    max_results: int = 8
    fetch_top: int = 4


class ContentIntelligenceApplicationService:
    def __init__(self, port: ContentIntelligencePort):
        self._port = port

    async def analyze(self, ctx: ContentIntelligenceContext, command: AnalyzeContentCommand) -> Any:
        ctx.validate()
        topic = (command.topic or "").strip()
        if not topic:
            raise ValueError("topic is required")
        return await self._port.analyze(
            topic,
            context=command.context,
            max_results=command.max_results,
            fetch_top=command.fetch_top,
            workspace_id=ctx.workspace_id.strip(),
        )
