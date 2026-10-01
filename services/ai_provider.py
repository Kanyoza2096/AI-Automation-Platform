"""Provider-neutral AI analysis contract.

AI providers produce interpretation evidence. They do not produce
ActionIntent objects and they have no execution authority.

This module enforces the first of three boundaries:

  1. (this module) The AIReceipt type has no intent-shaped fields. A
     provider cannot express "what to do" through this interface.

  2. (services.ai_intent.formulate_action_intent_from_ai) Filters the
     AI's structured output through a sector allowlist. A provider can
     recommend an action; the recommendation is validated and its
     parameters are replaced with KDO-supplied values.

  3. (services.action_kernel) Validates the resulting ActionIntent
     against an ActionContract and requires a decision_id. Even a
     well-formed intent is refused without authorization.

The boundary is the composition. None of the three is sufficient alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol
import hashlib
import json

AIStatus = Literal["succeeded", "failed", "uncertain"]


@dataclass(frozen=True)
class AIUsage:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost: Optional[float] = None


@dataclass(frozen=True)
class AIReceipt:
    status: AIStatus
    provider: str
    model: str
    raw_output: str
    structured_output: Optional[dict[str, Any]]
    usage: Optional[AIUsage]
    correlation_id: str
    request_fingerprint: str
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("AIReceipt.provider is required")
        if not self.model.strip():
            raise ValueError("AIReceipt.model is required")
        if not self.correlation_id.strip():
            raise ValueError("AIReceipt.correlation_id is required")
        if not self.request_fingerprint.strip():
            raise ValueError("AIReceipt.request_fingerprint is required")
        # A succeeded receipt must carry *something* interpretable. Text is
        # the common case; structured output alone is valid for providers
        # that return a schema-typed answer (classifiers, embeddings with
        # labels, tabular extractors).
        if self.status == "succeeded" and not (self.raw_output.strip() or self.structured_output):
            raise ValueError(
                "succeeded AI receipt requires raw_output or structured_output"
            )
        if self.status == "succeeded" and self.error:
            raise ValueError("succeeded AI receipt cannot contain an error")
        if self.structured_output is not None and not isinstance(self.structured_output, dict):
            raise ValueError("structured_output must be a dict when present")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "raw_output": self.raw_output,
            "structured_output": self.structured_output,
            "usage": self.usage.__dict__ if self.usage else None,
            "correlation_id": self.correlation_id,
            "request_fingerprint": self.request_fingerprint,
            "error": self.error,
        }


def request_fingerprint(*, model: str, system_prompt: str, user_prompt: str) -> str:
    canonical = json.dumps(
        {"model": model, "system_prompt": system_prompt, "user_prompt": user_prompt},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AIProviderPort(Protocol):
    provider: str

    def analyze(
        self,
        *,
        user_prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        correlation_id: str,
        structured: bool = False,
    ) -> AIReceipt:
        """Return interpretation evidence only; never an ActionIntent."""
