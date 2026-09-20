"""V68.2 end-to-end business objective certification harness.

This is a certification harness, not a new automation engine. It proves that a
bounded business objective can traverse KDO's existing governed planes without
creating a parallel authority or execution path.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.action_kernel import ActionContract, ActionKernel
from services.business_autonomy_envelope import BusinessAutonomyEnvelope, evaluate_envelope_for_action
from services.business_decision_action_handoff import build_action_kernel_handoff
from services.business_decision_candidate import build_decision_candidate
from services.business_decision_policy import evaluate_decision_candidate
from services.business_outcome_attribution import attribute_business_outcome
from services.business_outcome_measurement import build_business_outcome_observation, build_execution_observation


@dataclass(frozen=True)
class ObjectiveCertificationResult:
    certified: bool
    stage_results: dict[str, bool]
    lineage: dict[str, str]
    evidence: dict[str, Any] = field(default_factory=dict)
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "certified": self.certified,
            "stage_results": dict(self.stage_results),
            "lineage": dict(self.lineage),
            "evidence": dict(self.evidence),
            "failures": list(self.failures),
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _understanding(workspace_id: str, correlation_id: str) -> dict[str, Any]:
    """Build a real canonical V65 understanding for the offline fixture."""
    from services.business_understanding import build_business_understanding

    return build_business_understanding(
        text="I want to buy this product. Please follow up with me.",
        deterministic={
            "intent": "purchase_interest",
            "confidence": 0.92,
            "signals": ["purchase_interest"],
            "evidence": ["I want to buy this product"],
        },
        message_id="msg_v68_cert_001",
        workspace_id=workspace_id,
        customer_id="customer_v68_cert_001",
        channel="whatsapp",
        correlation_id=correlation_id,
    )


def _promoted_learning(workspace_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "knowledge_id": "know_v68_cert_001",
        "workspace_id": workspace_id,
        "target_type": "action",
        "target_id": "crm_schedule_followup",
        "direction": "positive",
        "confidence": 0.84,
        "support_count": 5,
        "consistency": 0.80,
        "lesson": "Bounded follow-up has historically produced qualified conversations.",
        "promotion_state": "promoted",
        "authority": "none",
    }


def certify_business_objective(
    envelope: BusinessAutonomyEnvelope,
    *,
    now=None,
    provider_handler: Optional[Callable[[dict[str, Any], Any], dict[str, Any]]] = None,
) -> ObjectiveCertificationResult:
    """Run an offline, side-effect-free certification through the real contracts.

    The Action Kernel is used with its explicit in-memory test mode. No external
    provider is contacted. The fixture handler represents a certified provider
    boundary and records whether the kernel invoked it.
    """
    failures: list[str] = []
    stages: dict[str, bool] = {}
    lineage: dict[str, str] = {}
    evidence: dict[str, Any] = {}
    workspace_id = envelope.workspace_id
    correlation_id = "corr_v68_cert_001"

    # 1. Objective/envelope boundary.
    stages["objective_envelope"] = envelope.is_active(now) and envelope.mode.value == "execute"
    _assert(stages["objective_envelope"], "objective envelope is not active execute mode", failures)
    _assert(envelope.authority == "none", "envelope granted authority", failures)
    _assert(envelope.execution_effect == "none", "envelope granted execution effect", failures)

    # 2. Business understanding -> decision input.
    from services.business_understanding_decision_input import build_decision_input
    understanding = _understanding(workspace_id, correlation_id)
    decision_input = build_decision_input(understanding)
    stages["business_understanding"] = decision_input["governance_status"] == "decision_eligible"
    _assert(decision_input["authority"] == "none", "decision input authority changed", failures)

    # 3. Candidate -> policy.
    candidate = build_decision_candidate(decision_input)
    evaluation = evaluate_decision_candidate(candidate)
    stages["decision_policy"] = evaluation["disposition"] == "ALLOW"
    _assert(stages["decision_policy"], f"unexpected policy disposition: {evaluation['disposition']}", failures)

    # 4. Envelope declaration is an additional constraint, never an authority grant.
    envelope_check = evaluate_envelope_for_action(
        envelope,
        workspace_id=workspace_id,
        action_type=evaluation["action"],
        risk=evaluation["risk_level"],
        now=now,
    )
    stages["autonomy_boundary"] = bool(envelope_check["eligible"])
    _assert(stages["autonomy_boundary"], "objective envelope rejected the governed action", failures)
    _assert(envelope_check["requires_authoritative_recheck"] is True, "envelope skipped authoritative recheck", failures)

    # Fail closed before any Action Kernel admission when the objective envelope
    # rejects the candidate. Certification must never demonstrate a bypass.
    if failures:
        return ObjectiveCertificationResult(
            certified=False, stage_results=stages, lineage=lineage, evidence=evidence, failures=tuple(failures)
        )

    # 5. Handoff into the single Action Kernel admission boundary.
    handoff = build_action_kernel_handoff(evaluation)
    stages["action_handoff"] = handoff["state"] == "ready"
    _assert(stages["action_handoff"], "decision did not produce a ready kernel handoff", failures)

    # 6. Real Action Kernel admission + deterministic provider fixture.
    calls: list[dict[str, Any]] = []
    handler = provider_handler or (lambda params, intent: {"provider_object_id": "obj_v68_cert_001", "ok": True})

    def wrapped(params, intent):
        calls.append({"action_type": intent.action_type, "intent_id": intent_id_holder[0]})
        return handler(params, intent)

    kernel = ActionKernel(allow_in_memory=True)
    kernel.register(ActionContract(
        action_type="crm_schedule_followup",
        risk_level="medium",
        required_parameters=("customer_id",),
        handler=wrapped,
        autonomous_retryable=True,
    ))
    intent_id_holder = [""]

    parameters = {"customer_id": decision_input["customer_id"], "message_id": decision_input["message_id"], "correlation_id": correlation_id}
    enqueue = asyncio.run(kernel.enqueue(
        workspace_id=workspace_id,
        action_type=handoff["action_type"],
        parameters=parameters,
        risk_level=handoff["risk_level"],
        decision_id=evaluation["evaluation_id"],
        correlation_id=correlation_id,
        idempotency_key=handoff["idempotency_key"],
        source="v68.2.objective_certification",
        approved=False,
    ))
    intent_id_holder[0] = enqueue["intent_id"]
    row = dict(kernel._memory_intents[intent_id_holder[0]])
    processed = asyncio.run(kernel.process_outbox_row(row))
    stages["action_kernel"] = processed["status"] == "succeeded" and len(calls) == 1
    _assert(stages["action_kernel"], "Action Kernel did not produce exactly one successful provider invocation", failures)

    # 7. Execution truth -> business outcome truth -> attribution.
    execution = kernel.ledger._memory.get(enqueue["idempotency_key"])
    _assert(execution is not None, "execution ledger record missing", failures)
    execution_obs = build_execution_observation(execution)
    business_obs = build_business_outcome_observation(
        workspace_id=workspace_id,
        execution_id=execution.execution_id,
        outcome_state="succeeded",
        business_success=True,
        confidence=0.86,
        source="analytics",
        evidence={"qualified_conversation": True},
        external_event_id="event_v68_cert_001",
    )
    attribution = attribute_business_outcome(
        business_obs,
        [{
            "workspace_id": workspace_id,
            "execution_id": execution.execution_id,
            "decision_id": evaluation["evaluation_id"],
            "evaluation_id": evaluation["evaluation_id"],
            "candidate_id": candidate["candidate_id"],
            "handoff_id": handoff["handoff_id"],
            "external_event_ids": ["event_v68_cert_001"],
        }],
        external_event_id="event_v68_cert_001",
    )
    stages["outcome_attribution"] = (
        execution_obs["business_success"] is None
        and attribution["attribution_status"] == "attributed"
        and attribution["execution_id"] == execution.execution_id
    )
    _assert(stages["outcome_attribution"], "outcome truth was not attributed to the exact execution", failures)

    # 8. Learning remains advisory and cannot mutate policy or execution authority.
    from services.business_learning_decision_context import build_learning_decision_context
    learning_context = build_learning_decision_context(decision_input, [_promoted_learning(workspace_id)])
    stages["learning_boundary"] = (
        learning_context["knowledge_count"] == 1
        and learning_context["advisory_only"] is True
        and learning_context["authority"] == "none"
        and learning_context["policy_effect"] == "none"
        and learning_context["execution_effect"] == "none"
    )
    _assert(stages["learning_boundary"], "learning escaped its advisory boundary", failures)

    lineage.update({
        "workspace_id": workspace_id,
        "envelope_id": envelope.envelope_id,
        "understanding_id": decision_input["understanding_id"],
        "candidate_id": candidate["candidate_id"],
        "evaluation_id": evaluation["evaluation_id"],
        "handoff_id": handoff["handoff_id"],
        "intent_id": enqueue["intent_id"],
        "execution_id": execution.execution_id if execution else "",
        "business_observation_id": business_obs["observation_id"],
        "attribution_id": attribution["attribution_id"],
    })
    evidence.update({
        "provider_invocations": len(calls),
        "execution_status": processed["status"],
        "execution_observation_state": execution_obs["outcome_state"],
        "business_success": business_obs["business_success"],
        "attribution_status": attribution["attribution_status"],
        "learning_advisory_only": learning_context["advisory_only"],
    })

    certified = not failures and all(stages.values())
    return ObjectiveCertificationResult(
        certified=certified,
        stage_results=stages,
        lineage=lineage,
        evidence=evidence,
        failures=tuple(failures),
    )


def certification_fingerprint(result: ObjectiveCertificationResult) -> str:
    payload = "|".join(f"{k}={v}" for k, v in sorted(result.lineage.items()))
    return hashlib.sha256(payload.encode()).hexdigest()
