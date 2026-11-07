import logging

from plugins.trend_intelligence.plugin import TrendIntelligencePlugin
from services.smart_sensor import AdaptiveFrequency, CircuitBreaker


class Resp:
    def __init__(self, data):
        self.data = data


class Table:
    def __init__(self, data):
        self.data = data

    def select(self, *_a): return self
    def eq(self, *_a): return self
    def execute(self): return Resp(self.data)


class DB:
    def __init__(self, sources): self.sources = sources
    def table(self, name):
        return Table(self.sources if name == "trend_sources" else [])


def test_adaptive_frequency_backs_off_and_speeds_up():
    f = AdaptiveFrequency(300, 60, 3600)
    f.on_result(False)
    assert f.current_interval == 600
    f.on_result(False)
    assert f.current_interval == 1200
    f.on_result(True)
    assert f.current_interval == 600


def test_circuit_breaker_opens_after_threshold_and_recovers():
    b = CircuitBreaker(threshold=2, cooldown_seconds=3600)
    assert b.can_attempt()
    b.record_failure()
    assert b.can_attempt()
    b.record_failure()
    assert not b.can_attempt()
    b.record_success()
    assert b.can_attempt()
    assert b.failures == 0


def test_trend_preflight_excludes_unconfigured_sources(caplog):
    db = DB([
        {"id": "news", "name": "AI News", "platform": "newsapi", "enabled": True, "workspace_id": "ws-test"},
        {"id": "rss", "name": "RSS", "platform": "rss", "query": "https://example.com/feed", "enabled": True, "workspace_id": "ws-test"},
    ])
    plugin = TrendIntelligencePlugin(db)
    with caplog.at_level(logging.INFO):
        assert plugin._preflight_sources() is True
    assert list(plugin._sources) == ["rss"]
    assert "disabled (not configured)" in caplog.text


def test_trend_scan_logs_aggregate_not_per_source(caplog, monkeypatch):
    db = DB([
        {"id": "rss1", "name": "RSS One", "platform": "rss", "query": "https://example.com/1", "enabled": True, "workspace_id": "ws-test"},
        {"id": "rss2", "name": "RSS Two", "platform": "rss", "query": "https://example.com/2", "enabled": True, "workspace_id": "ws-test"},
    ])
    plugin = TrendIntelligencePlugin(db)
    plugin._preflight_sources()
    monkeypatch.setattr(plugin, "_fetch_from_source", lambda source: [])
    with caplog.at_level(logging.INFO):
        result = plugin.scan_all_sources()
    assert result["items_fetched"] == 0
    assert caplog.text.count("TrendIntelligence scan:") == 1
    assert "RSS One — 0 posts" not in caplog.text
    assert plugin._frequency.current_interval == 600


def test_trend_failed_source_opens_breaker(monkeypatch, caplog):
    db = DB([{"id": "reddit", "name": "Reddit", "platform": "reddit", "query": "technology", "enabled": True, "workspace_id": "ws-test"}])
    plugin = TrendIntelligencePlugin(db)
    plugin._preflight_sources()
    monkeypatch.setattr(plugin, "_fetch_from_source", lambda source: (_ for _ in ()).throw(RuntimeError("HTTP 403")))
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            plugin.scan_all_sources()
    assert plugin._breakers["reddit"].state == "open"
    assert "circuit opened" in caplog.text


def test_trend_status_exposes_sensor_state():
    plugin = TrendIntelligencePlugin(None)
    status = plugin.get_status()
    assert status["name"] == "trend_intelligence"
    assert "current_interval_seconds" in status
    assert "breakers" in status


def test_trend_preflight_rejects_sources_without_database_workspace(caplog):
    db = DB([{"id": "rss-unbound", "name": "Unbound RSS", "platform": "rss", "query": "https://example.com/feed", "enabled": True}])
    plugin = TrendIntelligencePlugin(db)
    with caplog.at_level(logging.WARNING):
        assert plugin._preflight_sources() is False
    assert plugin._sources == {}
    assert "active tenant workspace_id required" in caplog.text
