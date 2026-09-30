"""Explicit registry for non-social enterprise provider adapters.

This registry intentionally does not auto-enable credentials or mutations. The
caller supplies provider credentials and chooses mutation policy per instance.
"""
from __future__ import annotations
from typing import Any


def create_enterprise_adapter(provider: str, **kwargs: Any):
    name = str(provider or "").strip().lower()
    if name == "github":
        from services.connectors.github_adapter import GitHubAdapter
        return GitHubAdapter(**kwargs)
    if name == "render":
        from services.connectors.render_adapter import RenderAdapter
        return RenderAdapter(**kwargs)
    if name in {"stripe","shopify","slack","jira","pagerduty","datadog","cloudflare","twilio"}:
        from services.connectors.modern_adapters import StripeAdapter, ShopifyAdapter, SlackAdapter, JiraAdapter, PagerDutyAdapter, DatadogAdapter, CloudflareAdapter, TwilioAdapter
        return {"stripe":StripeAdapter,"shopify":ShopifyAdapter,"slack":SlackAdapter,"jira":JiraAdapter,"pagerduty":PagerDutyAdapter,"datadog":DatadogAdapter,"cloudflare":CloudflareAdapter,"twilio":TwilioAdapter}[name](**kwargs)
    if name == "google_workspace":
        from services.connectors.google_workspace_adapter import GoogleWorkspaceAdapter
        return GoogleWorkspaceAdapter(**kwargs)
    if name == "aws":
        from services.connectors.aws_adapter import AWSAdapter
        return AWSAdapter(**kwargs)
    raise ValueError(f"unsupported enterprise provider {name!r}; supported: github, render, stripe, shopify, slack, jira, pagerduty, datadog, cloudflare, twilio, google_workspace, aws")


def supported_enterprise_providers() -> tuple[str, ...]:
    return ("github", "render", "stripe", "shopify", "slack", "jira", "pagerduty", "datadog", "cloudflare", "twilio", "google_workspace", "aws")
