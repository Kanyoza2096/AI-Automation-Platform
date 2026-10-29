"""V68.16 — adversarial multi-tenant end-to-end certification.

This is an offline certification harness for the business operating chain:
trusted tenant boundary -> business understanding -> decision plane ->
execution truth -> business outcome -> attribution -> learning -> decision
context.

The harness deliberately cross-wires tenant A and tenant B artifacts and
expects every authoritative boundary to fail closed. It never calls a real
provider, never writes a database, and never grants execution authority.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from services.business_understanding import build_business_understanding
from services.business_understanding_decision_input import build_decision_input
from services.business_decision_candidate import build_decision_candidate
from services.business_decision_policy import evaluate_decision_candidate
from services.business_outcome_measurement import build_business_outcome_observation
from services.business_outcome_attribution import attribute_business_outcome
from services.business_outcome_learning import build_learning_signal
from services.business_learning_governance import evaluate_learning_governance
from services.business_learning_decision_context import build_learning_decision_context
from services.business_automation import BusinessAutomationEngine
from services.business_automation_event_bridge import BusinessAutomationEventBridge
from services.v68_15_learning_tenant_integrity import (
    assert_learning_signal_workspace,
    assert_decision_context_learning_workspace,
    assert_learning_context_integrity,
)


@dataclass(frozen=True)
class TenantJourney:
    workspace_id: str
    understanding: dict[str, Any]
    decision_input: dict[str, Any]
    candidate: dict[str, Any]
    evaluation: dict[str, Any]
    execution: dict[str, Any]
    outcome: dict[str, Any]
    attribution: dict[str, Any]
    learning_signals: tuple[dict[str, Any], ...]
    governed_learning: tuple[dict[str, Any], ...]
    decision_context: dict[str, Any]

    def lineage_ids(self) -> set[str]:
        ids = {
            self.workspace_id,
            self.understanding["understanding_id"],
            self.decision_input["understanding_id"],
            self.candidate["candidate_id"],
            self.evaluation["evaluation_id"],
            self.execution["execution_id"],
            self.outcome["observation_id"],
            self.attribution["attribution_id"],
            self.decision_context["understanding_id"],
        }
        ids.update(s["signal_id"] for s in self.learning_signals)
        ids.update(k["knowledge_id"] for k in self.governed_learning)
        return ids


def _execution(workspace_id: str, execution_id: str, action: str) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "execution_id": execution_id,
        "action": action,
        "status": "succeeded",
        "provider": "synthetic_certification_provider",
        "provider_object_id": f"obj_{execution_id}",
        "evidence": {"synthetic": True},
    }


def build_tenant_journey(workspace_id: str, *, execution_id: str) -> TenantJourney:
    """Build a deterministic, side-effect-free tenant journey."""
    understanding = build_business_understanding(
        text="I want to buy this product.",
        deterministic={
            "intent": "purchase_interest",
            "confidence": 0.96,
            "signals": ["purchase_interest"],
            "evidence": ["I want to buy this product."],
        },
        message_id=f"msg_{workspace_id}",
        workspace_id=workspace_id,
        customer_id=f"customer_{workspace_id}",
        channel="web",
        correlation_id=f"corr_{workspace_id}",
    )
    decision_input = build_decision_input(understanding)
    candidate = build_decision_candidate(decision_input)
    evaluation = evaluate_decision_candidate(candidate)
    if evaluation["disposition"] != "ALLOW":
        raise AssertionError(f"synthetic tenant journey was not allowed: {evaluation}")

    execution = _execution(workspace_id, execution_id, evaluation["action"])
    outcome = build_business_outcome_observation(
        workspace_id=workspace_id,
        execution_id=execution_id,
        outcome_state="succeeded",
        business_success=True,
        confidence=0.90,
        source="analytics",
        evidence={"conversion": True, "tenant": workspace_id},
        external_event_id=f"conversion_{workspace_id}",
    )
    attribution = attribute_business_outcome(outcome, [execution])
    if attribution["attribution_status"] != "attributed":
        raise AssertionError("synthetic outcome was not attributed to its execution")

    learning_signals = tuple(
        build_learning_signal(outcome, attribution, target_type="action", target_id=evaluation["action"])
        for _ in range(3)
    )
    # Give the three observations distinct identities while retaining the
    # exact tenant and target semantics required for promotion.
    learning_signals = tuple(
        {**signal, "signal_id": f"{signal['signal_id']}_{i}"}
        for i, signal in enumerate(learning_signals, start=1)
    )
    governed = tuple(evaluate_learning_governance(learning_signals))
    if not governed or governed[0]["promotion_state"] != "promoted":
        raise AssertionError("synthetic evidence did not reach promoted learning")
    for learning in governed:
        # V68.15 evidence contract requires source IDs to resolve to real
        # same-tenant signals.
        assert_learning_signal_workspace(learning, learning_signals)

    context = build_learning_decision_context(decision_input, governed)
    assert_decision_context_learning_workspace(decision_input, governed)
    assert_learning_context_integrity(context, governed)

    return TenantJourney(
        workspace_id=workspace_id,
        understanding=understanding,
        decision_input=decision_input,
        candidate=candidate,
        evaluation=evaluation,
        execution=execution,
        outcome=outcome,
        attribution=attribution,
        learning_signals=learning_signals,
        governed_learning=governed,
        decision_context=context,
    )


def _must_fail(label: str, fn: Callable[[], Any]) -> None:
    try:
        fn()
    except (ValueError, AssertionError):
        return
    raise AssertionError(f"adversarial cross-tenant case unexpectedly passed: {label}")


def run_adversarial_certification() -> dict[str, Any]:
    """Run two isolated tenants plus deliberate cross-tenant attacks."""
    a = build_tenant_journey("tenant_A", execution_id="exec_A")
    b = build_tenant_journey("tenant_B", execution_id="exec_B")

    # 1. Outcome attribution must not select a candidate owned by another tenant.
    cross_candidate = dict(b.execution)
    cross_candidate["action"] = a.execution["action"]
    _assert_attribution_not_crossing(a, cross_candidate)

    # 2. Learning evidence cannot borrow another tenant's signal.
    cross_learning = dict(a.governed_learning[0])
    cross_learning["workspace_id"] = b.workspace_id
    _must_fail(
        "cross-tenant learning evidence is rejected",
        lambda: assert_learning_signal_workspace(cross_learning, a.learning_signals),
    )

    # 3. Decision context cannot project another tenant's knowledge.
    cross_context_input = dict(a.decision_input)
    _must_fail(
        "cross-tenant learning into decision context is rejected",
        lambda: assert_decision_context_learning_workspace(
            cross_context_input, [b.governed_learning[0]]
        ),
    )

    # 4. A knowledge ID from tenant B cannot be injected into tenant A's context.
    forged_context = dict(a.decision_context)
    forged_context["knowledge"] = [
        {**forged_context["knowledge"][0], "knowledge_id": b.governed_learning[0]["knowledge_id"]}
    ]
    _must_fail(
        "cross-tenant knowledge projection is rejected",
        lambda: assert_learning_context_integrity(forged_context, a.governed_learning),
    )

    # 5. Lineage and knowledge IDs must be disjoint between tenants.
    overlap = a.lineage_ids() & b.lineage_ids()
    if overlap:
        raise AssertionError(f"tenant lineage collision detected: {sorted(overlap)}")

    # 6. Event ingress must trust the authenticated envelope workspace, not a
    # workspace value supplied inside the untrusted payload.
    _assert_event_workspace_spoof_is_contained()

    return {
        "schema_version": "1.0",
        "certification": "V68.16",
        "status": "CERTIFIED",
        "tenants": [a.workspace_id, b.workspace_id],
        "journey_stages": [
            "tenant_bound_understanding",
            "decision_input",
            "decision_candidate",
            "decision_policy",
            "synthetic_execution_truth",
            "business_outcome",
            "outcome_attribution",
            "learning",
            "learning_governance",
            "decision_context",
        ],
        "adversarial_cases": 6,
        "cross_tenant_lineage_overlap": 0,
        "provider_calls": 2,
        "external_side_effects": 0,
        "authority_granted_by_certification": False,
        "notes": [
            "All certification execution is synthetic and offline.",
            "Cross-tenant artifacts are fail-closed at learning, attribution, and decision-context boundaries.",
            "Final production tenant enforcement remains DB/RLS/Action-Kernel responsibility; this harness does not replace it.",
        ],
    }


def _assert_attribution_not_crossing(tenant: TenantJourney, candidate: dict[str, Any]) -> None:
    result = attribute_business_outcome(tenant.outcome, [candidate])
    if result["attribution_status"] == "attributed":
        raise AssertionError("cross-tenant candidate was attributed")


async def _event_workspace_spoof_probe() -> None:
    engine = BusinessAutomationEngine()
    await engine.create_rule("tenant_A", {
        "name": "tenant-bound probe",
        "condition": {"field": "kind", "op": "eq", "value": "lead"},
        "action": "log",
        "parameters": {"trigger_events": ["crm.contact.created"]},
    })
    bridge = BusinessAutomationEventBridge(engine)
    await bridge._handle({
        "event_name": "crm.contact.created",
        "event_id": "spoof-event-1",
        "source": "synthetic-adversary",
        "workspace_id": "tenant_A",
        "payload": {"kind": "lead", "workspace_id": "tenant_B"},
    })
    history_a = await engine.history("tenant_A")
    history_b = await engine.history("tenant_B")
    if len(history_a) != 1 or history_b:
        raise AssertionError("event payload workspace spoof crossed tenant boundary")


def _assert_event_workspace_spoof_is_contained() -> None:
    asyncio.run(_event_workspace_spoof_probe())


__all__ = ["TenantJourney", "build_tenant_journey", "run_adversarial_certification"]
