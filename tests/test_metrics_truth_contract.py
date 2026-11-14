import asyncio
import json
import sys
from types import SimpleNamespace

from api.routers import metrics


def response_payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_resource_provider_unavailable_returns_unknown_not_zero(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)

    response = asyncio.run(metrics.get_resource_metrics())
    payload = response_payload(response)

    assert response.status_code == 200
    assert payload["available"] is False
    assert payload["source"] == "unavailable"
    assert payload["reason"] == "psutil_unavailable"
    assert payload["cpu_percent"] is None
    assert payload["memory_percent"] is None
    assert payload["disk_percent"] is None


def test_resource_provider_partial_failure_preserves_unknown_field(monkeypatch):
    class Memory:
        percent = 63.5

    fake_psutil = SimpleNamespace(
        cpu_percent=lambda interval=None: 21.0,
        virtual_memory=lambda: Memory(),
        disk_usage=lambda path: (_ for _ in ()).throw(OSError("disk metrics unavailable")),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    response = asyncio.run(metrics.get_resource_metrics())
    payload = response_payload(response)

    assert payload["available"] is True
    assert payload["source"] == "psutil"
    assert payload["reason"] == "partial"
    assert payload["cpu_percent"] == 21.0
    assert payload["memory_percent"] == 63.5
    assert payload["disk_percent"] is None


def test_metrics_query_rejects_general_promql(monkeypatch):
    async def unexpected_snapshot(*args, **kwargs):
        raise AssertionError("unsupported query must be rejected before reading counters")

    monkeypatch.setattr(metrics, "_async_wrap", unexpected_snapshot)
    response = asyncio.run(metrics.metrics_query(q="rate(node_cpu_seconds_total[5m])"))
    payload = response_payload(response)

    assert response.status_code == 422
    assert payload["status"] == "error"
    assert payload["error"] == "unsupported_query"
    assert "kanyoza_errors_total" in payload["supported_metrics"]


def test_supported_counter_without_backend_value_is_unavailable(monkeypatch):
    async def snapshot(*args, **kwargs):
        return {"counters": {}}

    monkeypatch.setattr(metrics, "_async_wrap", snapshot)
    response = asyncio.run(metrics.metrics_query(q="kanyoza_errors_total"))
    payload = response_payload(response)

    assert response.status_code == 503
    assert payload["error"] == "metric_unavailable"


def test_supported_counter_returns_observed_numeric_value(monkeypatch):
    async def snapshot(*args, **kwargs):
        return {"counters": {"errors": 7}}

    monkeypatch.setattr(metrics, "_async_wrap", snapshot)
    response = asyncio.run(metrics.metrics_query(q="kanyoza_errors_total"))
    payload = response_payload(response)

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["data"]["result"][0]["metric"]["__name__"] == "kanyoza_errors_total"
    assert payload["data"]["result"][0]["value"][1] == "7"
