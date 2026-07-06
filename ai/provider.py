from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ProviderCapability(str, Enum):
    CHAT = "chat"
    STRUCTURED_JSON = "structured_json"
    IMAGE_GENERATION = "image_generation"
    SENTIMENT = "sentiment"
    CODE_ANALYSIS = "code_analysis"
    EMBEDDING = "embedding"


@dataclass
class AIRequest:
    prompt: str
    capability: ProviderCapability = ProviderCapability.CHAT
    temperature: float = 0.7
    max_tokens: int = 1024
    thinking_budget: Optional[int] = None
    context: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    tokens_used: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.text)


class AIProvider(ABC):
    """Abstract base for all AI providers. Concrete providers must implement
    all abstract methods. The orchestrator calls providers through this interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> set[ProviderCapability]: ...

    @property
    @abstractmethod
    def cost_per_1k_tokens(self) -> float: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def _execute(self, request: AIRequest) -> str:
        """Raw call to the underlying model. Return empty string on failure."""

    def execute(self, request: AIRequest) -> AIResponse:
        start = time.monotonic()
        text = self._execute(request)
        latency_ms = (time.monotonic() - start) * 1000
        return AIResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
        )

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities
