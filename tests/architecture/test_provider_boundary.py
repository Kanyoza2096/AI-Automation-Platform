from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "core"


def test_socket_server_has_no_concrete_provider_health_implementation():
    source = (CORE / "socket_server.py").read_text()
    forbidden = ("services.facebook", "twitter_access_token", "linkedin_access_token", "whatsapp_token", "telegram_bot_token")
    assert not any(token in source for token in forbidden)


def test_provider_health_registry_owns_provider_specific_probes():
    source = (ROOT / "services" / "connectors" / "health_registry.py").read_text()
    for token in ("facebook", "twitter", "linkedin", "instagram", "whatsapp", "telegram"):
        assert token in source


def test_core_exceptions_expose_provider_neutral_base():
    source = (CORE / "exceptions.py").read_text()
    assert "class ProviderError" in source
    assert "class FacebookError(ProviderError)" in source


def test_live_stats_uses_health_registry():
    source = (CORE / "socket_server.py").read_text()
    assert "check_integration_health" in source
    assert "_check_facebook_health" not in source
