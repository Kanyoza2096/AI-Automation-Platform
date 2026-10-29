import asyncio
import json
from types import SimpleNamespace

import pytest

from domain.affiliate.attribution import sign_webhook, verify_webhook
from domain.affiliate.compliance import enforce_disclosure
from domain.affiliate.policies import AffiliateDisclosurePolicy, validate_destination
from services.action_kernel import build_default_action_kernel


def test_affiliate_destination_requires_approved_https_domain():
    assert validate_destination("https://shop.example.com/item", ["example.com"]).startswith("https://")
    with pytest.raises(ValueError):
        validate_destination("http://shop.example.com/item", ["example.com"])
    with pytest.raises(ValueError):
        validate_destination("https://evil.example.net/item", ["example.com"])
    with pytest.raises(ValueError):
        validate_destination("https://127.0.0.1/item", ["127.0.0.1"])


def test_disclosure_is_policy_driven_and_idempotent():
    policy = AffiliateDisclosurePolicy()
    first = enforce_disclosure("Buy this", policy)
    second = enforce_disclosure(first.content, policy)
    assert first.disclosure_added is True
    assert second.disclosure_added is False
    assert "commission" in first.content.lower()


def test_webhook_signature():
    body = b'{"status":"approved"}'
    signature = sign_webhook(body, "secret")
    assert verify_webhook(body, signature, "secret")
    assert not verify_webhook(body, signature, "wrong")


def test_action_kernel_registers_affiliate_contracts_without_second_registry():
    kernel = build_default_action_kernel(None, allow_in_memory=True)
    assert {"affiliate_create_product", "affiliate_create_link", "affiliate_publish_promotion", "affiliate_pause_product", "affiliate_optimize_campaign"}.issubset(kernel._contracts)
    assert kernel._contracts["affiliate_publish_promotion"].requires_approval is True
    assert kernel._contracts["affiliate_pause_product"].requires_approval is True


def test_affiliate_publish_enqueue_is_governed():
    kernel = build_default_action_kernel(None, allow_in_memory=True)
    result = asyncio.run(kernel.enqueue(
        workspace_id="ws1", action_type="affiliate_publish_promotion",
        parameters={"platform": "facebook", "caption": "A product"}, risk_level="high", decision_id="dec-aff-1",
        source="test"))
    assert result["status"] == "approval_required"
    assert result["accepted"] is False
