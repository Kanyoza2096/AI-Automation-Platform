from services.phase9_correlation import (
    MetricObservation,
    OperationalCorrelationContext,
    TraceObservation,
)
import pytest


def test_context_preserves_correlation_and_trace_identity():
    root = OperationalCorrelationContext(workspace_id="ws_1", execution_id="exec_1")
    child = root.child_span()
    assert child.correlation_id == root.correlation_id
    assert child.trace_id == root.trace_id
    assert child.span_id != root.span_id
    assert child.causation_id == root.span_id


def test_context_cannot_grant_authority():
    with pytest.raises(ValueError):
        OperationalCorrelationContext(workspace_id="ws_1", authority="operator")


def test_metric_carries_operational_context():
    context = OperationalCorrelationContext(workspace_id="ws_1", execution_id="exec_1")
    metric = MetricObservation(
        name="provider.latency_ms",
        value=182.0,
        observed_at="2026-10-05T08:00:00+00:00",
        context=context,
        unit="ms",
    )
    payload = metric.to_dict()
    assert payload["context"]["execution_id"] == "exec_1"
    assert payload["context"]["trace_id"] == context.trace_id


def test_trace_carries_same_root_identity():
    context = OperationalCorrelationContext(workspace_id="ws_1", execution_id="exec_1")
    trace = TraceObservation(
        operation="connector.dispatch",
        started_at="2026-10-05T08:00:00+00:00",
        context=context,
        status="OK",
    )
    assert trace.to_dict()["context"]["correlation_id"] == context.correlation_id


def test_sensitive_dimensions_are_rejected():
    context = OperationalCorrelationContext(workspace_id="ws_1")
    with pytest.raises(ValueError):
        MetricObservation(
            name="auth.events",
            value=1,
            observed_at="2026-10-05T08:00:00+00:00",
            context=context,
            attributes={"authorization": "Bearer ..."},
        )


def test_trace_status_is_bounded():
    context = OperationalCorrelationContext(workspace_id="ws_1")
    with pytest.raises(ValueError):
        TraceObservation(
            operation="x",
            started_at="2026-10-05T08:00:00+00:00",
            context=context,
            status="SUCCESSFUL",
        )


def test_workspace_scope_is_required_for_telemetry_correlation():
    with pytest.raises(ValueError):
        OperationalCorrelationContext(workspace_id=" ")
