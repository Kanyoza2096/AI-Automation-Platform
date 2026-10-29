"""Council -> Action Kernel bridge for consequential engineering actions.

The bridge creates a fresh Council proposal for the exact task/action scope,
requires explicit approval and a Council-issued decision id, then submits the
approved action through the registered Action Kernel contract. It never treats
an LLM response, caller-provided decision id, or local event as authorization.
Production deployments must provide the authoritative durable Action Kernel.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping

from council.integration import deliberate_action as _deliberate_action
from services.kdo_engineering_team_events import TeamEventStore
from services.kdo_engineering_receipts import build_durable_action_receipt_verifier


class GovernedEngineeringActionError(RuntimeError):
    """Raised when governance or execution evidence is incomplete."""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _decision_value(decision: Any, key: str, default: Any = None) -> Any:
    if isinstance(decision, Mapping):
        return decision.get(key, default)
    return getattr(decision, key, default)


def _approved(decision: Any) -> bool:
    # Deliberately require the Council adapter's explicit boolean, not a truthy string.
    return _decision_value(decision, "is_approved", False) is True


async def execute_council_governed_engineering_action(
    *,
    task: Mapping[str, Any],
    action_type: str,
    parameters: Mapping[str, Any],
    agent_id: str,
    tool_name: str,
    objective: str,
    action_kernel: Any,
    event_store: TeamEventStore | None = None,
    council_deliberate: Callable[..., Any] | None = None,
    receipt_verifier: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Propose, authorize, and execute one action through real KDO boundaries.

    `council_deliberate` is injectable for deterministic tests; production callers
    should omit it to use KDO's configured Council integration. This function
    requires the Council decision to be explicitly approved and to carry a fresh
    decision id. Action Kernel contract validation and its ledger remain in force.
    """
    required_scope = ("tenant_id", "workspace_id", "task_id")
    if any(not _nonempty(task.get(key)) for key in required_scope):
        raise GovernedEngineeringActionError("complete_task_scope_required")
    if not all(_nonempty(value) for value in (action_type, agent_id, tool_name, objective)):
        raise GovernedEngineeringActionError("action_agent_tool_and_objective_required")
    if not isinstance(parameters, Mapping):
        raise GovernedEngineeringActionError("action_parameters_must_be_mapping")
    if action_kernel is None or not callable(getattr(action_kernel, "execute", None)):
        raise GovernedEngineeringActionError("action_kernel_not_configured")

    contract = action_kernel.get_contract_metadata(action_type) if callable(
        getattr(action_kernel, "get_contract_metadata", None)
    ) else None
    if not isinstance(contract, Mapping):
        raise GovernedEngineeringActionError("registered_action_contract_required")

    scoped_params = dict(parameters)
    # Decision identity is never accepted from model/caller parameters.
    scoped_params.pop("decision_id", None)
    # These identity fields are set by the bridge, not caller-overridable values.
    scoped_params.update({
        "tenant_id": task["tenant_id"],
        "workspace_id": task["workspace_id"],
        "task_id": task["task_id"],
        "agent_id": agent_id,
        "tool_name": tool_name,
    })
    if event_store is not None:
        event_store.append(
            task_id=task["task_id"], tenant_id=task["tenant_id"],
            workspace_id=task["workspace_id"], event_type="governed_action_proposed",
            actor_id=agent_id,
            payload={"action_type": action_type, "tool_name": tool_name,
                     "objective": objective, "authority": "proposal_only"},
        )

    deliberate = council_deliberate or _deliberate_action
    try:
        decision = deliberate(
            action_name=action_type,
            source_engine="kdo_engineering_vertical",
            description=objective,
            proposal_type="software_engineering",
            metadata={
                "tenant_id": task["tenant_id"], "workspace_id": task["workspace_id"],
                "task_id": task["task_id"], "agent_id": agent_id,
                "tool_name": tool_name, "contract_risk_level": contract.get("risk_level"),
            },
            action_params=scoped_params,
        )
        if inspect.isawaitable(decision):
            decision = await decision
    except Exception as exc:
        if event_store is not None:
            event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
                workspace_id=task["workspace_id"], event_type="governed_action_denied",
                actor_id=agent_id, payload={"reason": "council_unavailable", "error_type": type(exc).__name__})
        raise GovernedEngineeringActionError("council_deliberation_failed_closed") from exc

    decision_id = _decision_value(decision, "decision_id")
    if not _approved(decision) or not _nonempty(decision_id):
        reason = "council_approval_not_confirmed" if not _approved(decision) else "council_decision_id_missing"
        if event_store is not None:
            event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
                workspace_id=task["workspace_id"], event_type="governed_action_denied",
                actor_id=agent_id, payload={"reason": reason, "action_type": action_type})
        raise GovernedEngineeringActionError(reason)

    if event_store is not None:
        event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
            workspace_id=task["workspace_id"], event_type="governed_action_approved",
            actor_id="council", payload={"decision_id": decision_id, "action_type": action_type,
                                          "scope": {key: task[key] for key in required_scope}})

    try:
        result = await action_kernel.execute(
            workspace_id=task["workspace_id"], action_type=action_type,
            parameters=scoped_params, risk_level=str(contract.get("risk_level") or "low"),
            decision_id=decision_id,
            correlation_id=f"engineering:{task['task_id']}:{action_type}",
            idempotency_key=idempotency_key or f"engineering:{task['tenant_id']}:{task['workspace_id']}:{task['task_id']}:{action_type}",
            source="kdo_engineering_vertical",
            metadata={"tenant_id": task["tenant_id"], "task_id": task["task_id"],
                      "agent_id": agent_id, "tool_name": tool_name, "decision_id": decision_id},
        )
    except Exception as exc:
        if event_store is not None:
            event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
                workspace_id=task["workspace_id"], event_type="governed_action_execution_error",
                actor_id=agent_id, payload={"decision_id": decision_id, "error_type": type(exc).__name__})
        raise GovernedEngineeringActionError("action_kernel_execution_failed") from exc

    # Success requires terminal kernel status plus authoritative identifiers.
    # Missing receipt identity is treated as uncertain, never success.
    valid_receipt = (
        isinstance(result, Mapping)
        and result.get("status") == "succeeded"
        and _nonempty(result.get("execution_id"))
        and _nonempty(result.get("intent_id"))
    )
    if not valid_receipt:
        if event_store is not None:
            event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
                workspace_id=task["workspace_id"], event_type="governed_action_receipt_unverified",
                actor_id=agent_id, payload={"decision_id": decision_id,
                    "kernel_status": result.get("status") if isinstance(result, Mapping) else "invalid_result",
                    "reconciliation_required": True})
        return {"status": "uncertain", "ok": False, "decision_id": decision_id,
                "reconciliation_required": True, "reason": "authoritative_execution_receipt_required",
                "kernel_result": dict(result) if isinstance(result, Mapping) else {}}

    receipt = {"execution_id": result["execution_id"], "intent_id": result["intent_id"],
               "status": result["status"], "action_type": result.get("action_type", action_type)}
    # ActionKernel response identifiers are not, by themselves, proof that the
    # authoritative ledger contains a matching terminal record. Require an
    # injected verifier backed by the canonical ledger/read API before success.
    if receipt_verifier is None and getattr(action_kernel, "db", None) is not None:
        # Use the kernel's own configured database for authoritative read-back.
        # In-memory/test kernels intentionally have no implicit verifier.
        receipt_verifier = build_durable_action_receipt_verifier(action_kernel.db)
    if receipt_verifier is None:
        verified = False
        verification_reason = "authoritative_receipt_verifier_not_configured"
    else:
        try:
            verified_value = receipt_verifier(
                {"tenant_id": task["tenant_id"], "workspace_id": task["workspace_id"],
                 "task_id": task["task_id"], "decision_id": decision_id, "action_type": action_type},
                receipt,
            )
            if inspect.isawaitable(verified_value):
                verified_value = await verified_value
            verified = verified_value is True
            verification_reason = "authoritative_receipt_not_confirmed"
        except Exception as exc:
            verified = False
            verification_reason = "authoritative_receipt_verification_failed:" + type(exc).__name__
    if not verified:
        if event_store is not None:
            event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
                workspace_id=task["workspace_id"], event_type="governed_action_receipt_unverified",
                actor_id=agent_id, payload={"decision_id": decision_id, "receipt": receipt,
                                            "reason": verification_reason,
                                            "reconciliation_required": True})
        return {"status": "uncertain", "ok": False, "decision_id": decision_id,
                "receipt": receipt, "reconciliation_required": True,
                "reason": verification_reason, "kernel_result": dict(result)}
    if event_store is not None:
        event_store.append(task_id=task["task_id"], tenant_id=task["tenant_id"],
            workspace_id=task["workspace_id"], event_type="governed_action_receipt_verified",
            actor_id="authoritative_receipt_verifier", payload={"decision_id": decision_id,
                "receipt": receipt, "receipt_authority": "verified_by_configured_canonical_adapter"})
    return {"status": "succeeded", "ok": True, "decision_id": decision_id,
            "receipt": receipt, "receipt_verified": True, "kernel_result": dict(result)}
