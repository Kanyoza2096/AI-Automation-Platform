from pathlib import Path


def test_canonical_publish_has_no_non_durable_external_fallback():
    text = Path("domain/publishing/canonical_pipeline.py").read_text(encoding="utf-8")
    assert "FALLBACK direct plugin.publish" not in text
    assert "durable_execution_required" in text
    assert "make_publish_idempotency_key" in text


def test_plugin_registry_mutations_are_disabled():
    text = Path("plugins/base.py").read_text(encoding="utf-8")
    assert "PluginRegistry.publish_all is disabled" in text
    assert "PluginRegistry.publish_to is disabled" in text


def test_pipeline_publisher_uses_canonical_publish():
    text = Path("services/pipeline/pipeline.py").read_text(encoding="utf-8")
    assert "run_canonical_publish" in text
    assert "registry.publish_all" not in text


def test_legacy_news_scraper_cannot_mutate_external_world_directly():
    text = Path("services/news_scraper.py").read_text(encoding="utf-8")
    assert "registry.publish_all" not in text
    assert "facebook.post_to_page" not in text
    assert "direct external publishing is disabled" in text


def test_external_receipt_accepts_whatsapp_provider_message_id():
    text = Path("services/external_action_executor.py").read_text(encoding="utf-8")
    assert '"provider_message_id"' in text


def test_current_connector_registry_is_governable_for_all_supported_social_platforms():
    from services.connectors.connector_factory import supported_platforms
    from services.connectors.governed_connector import GovernedConnector
    from services.execution_ledger import ExecutionLedger

    class Fake:
        def __init__(self, platform): self.platform, self.calls = platform, 0
        def publish(self, content, **kwargs):
            self.calls += 1
            from services.connectors.connector import ConnectorResult
            return ConnectorResult(ok=True, platform=self.platform, post_id=f"{self.platform}-1", outcome="succeeded")
        def edit(self, post_id, content): return self.publish(content)
        def delete(self, post_id): return self.publish(post_id)

    expected = {"facebook", "instagram", "twitter", "linkedin", "telegram", "whatsapp", "website"}
    assert set(supported_platforms()) == expected
    for platform in sorted(expected):
        fake = Fake(platform)
        governed = GovernedConnector(fake, workspace_id="ws-connector", ledger=ExecutionLedger(allow_in_memory=True))
        result = __import__("asyncio").run(governed.publish("hello", idempotency_key=f"{platform}-stable"))
        assert result["status"] == "succeeded"
        assert fake.calls == 1


def test_website_connector_is_registered_and_requires_explicit_https_configuration():
    from services.connectors.connector_factory import supported_platforms, create_connector
    from models.social_account import SocialAccount
    from services.connectors.connector import Platform

    assert Platform.WEBSITE.value in supported_platforms()
    account = SocialAccount(
        id="website-1", workspace_id="ws-website", platform=Platform.WEBSITE,
        access_token="secret", platform_account_id="site-1",
        platform_config={"base_url": "http://example.com", "publish_path": "/api/kdo/content"},
    )
    result = create_connector(account).publish("hello", idempotency_key="stable")
    assert result.ok is False
    assert result.outcome == "failed"
    assert "HTTPS" in (result.error or "")

def test_website_publish_is_a_governed_action_contract():
    from services.action_kernel import build_default_action_kernel
    kernel = build_default_action_kernel(None, allow_in_memory=True)
    result = __import__("asyncio").run(kernel.execute(
        workspace_id="ws-website", action_type="website_publish",
        parameters={"social_account_id": "website-1", "content": "hello", "decision_id": "decision-website"},
        risk_level="high", decision_id="decision-website",
    ))
    assert result["status"] == "approval_required"
