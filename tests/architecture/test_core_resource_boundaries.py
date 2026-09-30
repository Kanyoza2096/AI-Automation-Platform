"""Regression tests for sector-neutral core resource contracts."""

from core.workspace_context import (
    clear_workspace_context,
    get_current_resource,
    get_workspace_context,
    set_workspace_context,
)
from core.metabolism import UsageMeter


def test_workspace_context_carries_generic_resource_refs():
    try:
        set_workspace_context(
            "ws-test",
            resource_refs={
                "social_account": "sa-123",
                "patient": "patient-456",
            },
        )
        assert get_current_resource("social_account") == "sa-123"
        assert get_current_resource("patient") == "patient-456"
        assert get_workspace_context().workspace_id == "ws-test"
    finally:
        clear_workspace_context()


def test_usage_meter_supports_domain_defined_resources():
    meter = UsageMeter()
    meter.set_resource_count("ws-test", "patient", 12)
    meter.set_resource_count("ws-test", "vehicle", 4)

    assert meter.resource_count("ws-test", "patient") == 12
    assert meter.resource_count("ws-test", "vehicle") == 4
    assert meter.resource_count("ws-test", "customer") == 0
