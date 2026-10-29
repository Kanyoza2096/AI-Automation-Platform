from core.slo_metrics import _request_slos
from utils.metrics import MetricsCollector

def test_request_metrics_use_route_templates_and_capture_server_errors():
    m = MetricsCollector()
    m.record_request("GET", "/api/v1/workspaces/{workspace_id}", 200, 100)
    m.record_request("GET", "/api/v1/workspaces/{workspace_id}", 503, 300)
    snap = m.get_snapshot()
    key = "http.server.GET./api/v1/workspaces/{workspace_id}"
    assert snap["counters"][f"{key}.requests"] == 2
    assert snap["counters"][f"{key}.server_errors"] == 1
    assert snap["timings"][key]["p95_ms"] == 100.0

def test_slo_request_projection_is_safe_when_no_requests_exist():
    m = MetricsCollector()
    # The global helper is intentionally process-global; verify its empty shape
    # without requiring external telemetry infrastructure.
    result = _request_slos()
    assert "five_xx_rate" in result
    assert result["five_xx_rate"] is None or 0 <= result["five_xx_rate"] <= 1
