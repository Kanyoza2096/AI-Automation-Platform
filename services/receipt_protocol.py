"""Canonical provider-receipt protocol for external execution.

This module is sector-neutral and vendor-neutral. It defines the evidence
contract between provider adapters and the KDO execution substrate.

Architectural invariant:
    once a provider returns an authoritative receipt, downstream layers may
    normalize its representation but must not silently discard its identity,
    status, or provenance.

The protocol deliberately distinguishes an observed provider object ID from
the durable ``provider_object_id`` stored as terminal execution truth. An ID
observed alongside an uncertain outcome is evidence for reconciliation, not a
proof of successful completion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional


PROVIDER_OBJECT_ID_KEYS = (
    "post_id",
    "platform_post_id",
    "id",
    "provider_object_id",
    "tweet_id",
    "message_id",
    "provider_message_id",
    "transaction_id",
    "object_id",
)

SUCCESS = frozenset({"ok", "success", "succeeded", "completed", "done"})
FAILURE = frozenset({"error", "failed", "failure", "rejected"})
UNCERTAIN = frozenset({"in_progress", "pending", "processing", "queued", "ambiguous"})


@dataclass(frozen=True)
class ProviderReceipt:
    """Sector-neutral evidence returned by an external provider.

    ``provider_object_id`` is authoritative only when ``status`` is
    ``succeeded``. ``observed_object_id`` may be populated for uncertain
    responses and is retained as reconciliation evidence.
    """

    provider: Optional[str]
    status: str
    provider_object_id: Optional[str] = None
    provider_request_id: Optional[str] = None
    observed_object_id: Optional[str] = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "uncertain", "in_progress"}:
            raise ValueError(f"invalid provider receipt status: {self.status}")
        if self.status == "succeeded" and not self.provider_object_id:
            raise ValueError("successful provider receipt requires provider_object_id")

    @property
    def authoritative(self) -> bool:
        return self.status == "succeeded" and bool(self.provider_object_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _value(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _error_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, Mapping):
        message = value.get("message") or value.get("reason") or value.get("error_message")
        code = value.get("type") or value.get("code") or value.get("error_code")
        if message and code:
            return f"{code}: {message}"
        return str(message or code or value)
    return str(getattr(value, "message", None) or getattr(value, "reason", None) or value)


def extract_provider_object_id(result: Any) -> Optional[str]:
    for key in PROVIDER_OBJECT_ID_KEYS:
        value = _value(result, key)
        if value:
            return str(value)
    return None


def normalize_provider_status(raw: Any, *, object_id: Optional[str], ok: bool = True,
                              explicit_outcome: str = "", partial: bool = False) -> str:
    explicit = str(explicit_outcome or "").strip().lower()
    if explicit in {"uncertain", "ambiguous"}:
        return "uncertain"
    if explicit == "failed":
        return "failed"
    if explicit == "succeeded":
        return "succeeded" if object_id else "uncertain"
    if partial:
        return "uncertain"
    value = str(raw or "").strip().lower()
    if value in SUCCESS:
        return "succeeded" if object_id else "uncertain"
    if value in FAILURE:
        return "failed"
    if value in UNCERTAIN:
        return "uncertain"
    return "uncertain" if ok and not object_id else ("succeeded" if ok else "failed")


def receipt_from_result(result: Any, *, provider: Optional[str] = None) -> ProviderReceipt:
    """Convert a provider/connector result into the canonical receipt protocol."""
    object_id = extract_provider_object_id(result)
    ok = bool(_value(result, "ok"))
    explicit = str(_value(result, "outcome") or "").strip().lower()
    execution_status = str(
        _value(result, "delivery_status") or _value(result, "execution_status") or ""
    ).strip().lower()
    status = normalize_provider_status(
        _value(result, "status"),
        object_id=object_id,
        ok=ok,
        explicit_outcome=explicit,
        partial=execution_status == "partial",
    )
    error = _error_text(_value(result, "error"))
    if status == "uncertain" and ok and not error and not object_id:
        error = "Provider returned success without an object ID"
    request_id = _value(result, "request_id") or _value(result, "provider_request_id")
    return ProviderReceipt(
        provider=provider or _value(result, "platform") or _value(result, "provider"),
        status=status,
        provider_object_id=object_id if status == "succeeded" else None,
        provider_request_id=str(request_id) if request_id else None,
        observed_object_id=object_id if status != "succeeded" else None,
        error=error,
        metadata={
            "result_type": type(result).__name__,
            "raw_outcome": explicit or None,
            "partial_delivery": execution_status == "partial",
            "had_object_id": bool(object_id),
        },
    )


def assert_receipt_propagation(receipt: ProviderReceipt, downstream: Mapping[str, Any]) -> None:
    """Enforce the Receipt Propagation Invariant at a downstream boundary.

    A successful authoritative receipt must remain represented downstream;
    an uncertain receipt must not be silently upgraded to success.
    """
    status = str(downstream.get("status") or "").lower()
    downstream_id = (
        downstream.get("provider_object_id")
        or downstream.get("post_id")
        or downstream.get("platform_post_id")
        or (downstream.get("receipt") or {}).get("provider_object_id")
    )
    if receipt.authoritative:
        if status not in {"succeeded", "success", "ok", "completed", "done"}:
            raise AssertionError(
                "Receipt Propagation Invariant violated: authoritative provider success "
                f"was downgraded to {status!r}"
            )
        if str(downstream_id or "") != str(receipt.provider_object_id):
            raise AssertionError(
                "Receipt Propagation Invariant violated: provider object ID was lost or changed"
            )
    elif receipt.status == "uncertain" and status in {"succeeded", "success", "ok", "completed", "done"}:
        raise AssertionError(
            "Receipt Propagation Invariant violated: uncertain provider outcome was upgraded to success"
        )
