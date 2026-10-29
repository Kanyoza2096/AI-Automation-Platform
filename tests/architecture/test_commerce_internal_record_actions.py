import asyncio
from services.action_kernel import build_default_action_kernel
from plugins.industries.commerce.repository import CommerceRepository


def test_commerce_internal_record_actions_are_kernel_bound_without_provider_authority():
    repo = CommerceRepository(None)
    kernel = build_default_action_kernel(db=None, allow_in_memory=True, commerce_repository=repo)
    registered = set(kernel.registered_action_types())
    assert {"commerce_record_payment", "commerce_record_fulfillment", "commerce_record_outcome"} <= registered
