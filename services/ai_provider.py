"""
Provider-neutral AI analysis contract.

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

v1.1 (2026-10-22) — FINISH_REASON:
  AIReceipt.finish_reason added (Optional[str], default None).
  Populated by provider adapters from the raw provider response, mapped
  to a fixed vocabulary by the adapter (STOP -> "stop", MAX_TOKENS ->
  "length", etc.) so downstream consumers reason about one set of values.

  finish_reason is intentionally excluded from
  receipt_fingerprint() in services/intelligence_ai_fabric.py. The
  fingerprint identifies what request produced what response;
  finish_reason describes how the response terminated. Keeping it out
  of the fingerprint preserves replay compatibility: existing receipts
  in ai_receipts were fingerprinted before finish_reason existed, and
  the projection of their stored JSON must remain byte-identical after
  this module ships.

  Consequence for consumers:
    - ai/gateway.py::_extract_finish_reason  sees the value via
      getattr(receipt, "finish_reason", None).
    - services/content_engine.py::_llm_generate_text rejects truncated
      responses (finish_reason="length") instead of returning them.
    - workflows/post_workflow.py::GenerateContentStep remains the
      structural backstop for truncated captions when finish_reason is
      unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol
import hashlib
import json

AIStatus = Literal["succeeded", "failed", "uncertain"]

# Normalized finish_reason vocabulary. Adapters translate provider-native
# values into these so downstream consumers reason about one set of
# values. None is honest ("we do not know") and distinct from "stop"
# ("the provider reported normal completion").
#
#   "stop"           — model finished normally
#   "length"         — max_tokens hit; response is truncated
#   "content_filter" — provider safety filter cut the response
#   "tool_calls"     — model ended by requesting a tool call
#   "other"          — provider reported something we did not map
#
# Downstream code must treat None and "other" as "no signal" — they must
# not assume completion.
FinishReason = Literal["stop", "length", "content_filter", "tool_calls", "other"]


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
    # v1.1 — normalized finish reason (see module docstring and
    # FinishReason). None means "not available". Never fabricated.
    finish_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("AIReceipt.provider is required")
        if not self.model.strip():
            raise ValueError("AIReceipt.model is required")
        if not self.correlation_id.strip():
            raise ValueError("AIReceipt.correlation_id is required")
        if not self.request_fingerprint.strip():
            raise ValueError("AIReceipt.request_fingerprint is required")
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
            "finish_reason": self.finish_reason,
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
