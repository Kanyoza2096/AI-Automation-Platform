"""Canonical read-only execution evidence chain for Command Center."""
from __future__ import annotations
from typing import Any, Mapping, Sequence

SECTIONS = ("intent", "governance", "actionKernel", "connector", "providerReceipt", "canonicalOutcome", "verification", "reconciliation", "identity")

def _clean(value: Any) -> Any:
    if isinstance(value, Mapping): return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_clean(v) for v in value]
    return value

def _state(value: Any) -> str: return "AVAILABLE" if value is not None else "UNKNOWN"

def build_execution_detail_read_model(*, workspace_id: str, execution: Mapping[str, Any], intent: Mapping[str, Any] | None = None, governance: Mapping[str, Any] | None = None, observations: Sequence[Mapping[str, Any]] = (), attributions: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    execution, intent, governance = dict(execution), (dict(intent) if intent else None), (dict(governance) if governance else None)
    observations, attributions = [dict(x) for x in observations], [dict(x) for x in attributions]
    if str(execution.get("workspace_id")) != str(workspace_id): raise ValueError("execution workspace mismatch")
    if intent is not None and str(intent.get("workspace_id")) != str(workspace_id): raise ValueError("intent workspace mismatch")
    if governance is not None and governance.get("workspace_id") is not None and str(governance.get("workspace_id")) != str(workspace_id): raise ValueError("governance workspace mismatch")
    execution_id = execution.get("execution_id")
    linked_obs = [o for o in observations if str(o.get("execution_id")) == str(execution_id)]
    linked_attrs = [a for a in attributions if str(a.get("execution_id")) == str(execution_id)]
    business = [o for o in linked_obs if o.get("observation_type") == "business_outcome"]
    latest_business = business[0] if business else None
    provider_receipt = {"state":"AVAILABLE","provider":execution.get("provider"),"providerRequestId":execution.get("provider_request_id"),"providerObjectId":execution.get("provider_object_id"),"status":execution.get("status"),"attempt":execution.get("attempt"),"evidence":execution.get("evidence") or {}}
    outcome = {"state":_state(intent),"status":intent.get("status") if intent else None,"output":intent.get("output") if intent else None,"error":intent.get("error") if intent else None,"executionId":execution_id}
    verification = {"state":"AVAILABLE" if linked_obs else "UNKNOWN","observations":_clean(linked_obs),"latestBusinessOutcome":_clean(latest_business),"attributions":_clean(linked_attrs),"businessSuccess":latest_business.get("business_success") if latest_business else None,"businessSuccessConfidence":latest_business.get("business_success_confidence") if latest_business else None,"authority":"none"}
    evidence = execution.get("evidence") or {}
    reconciliation_required = execution.get("status") == "uncertain" or bool(evidence.get("reconciliation_required")) or bool(intent and intent.get("status") == "reconciliation_required")
    reconciliation = {"state":"REQUIRED" if reconciliation_required else "NOT_REQUIRED" if execution.get("status") in {"succeeded","failed","cancelled"} else "UNKNOWN","required":bool(reconciliation_required),"status":execution.get("status") if reconciliation_required else None,"reason":execution.get("error") or evidence.get("reconciliation_reason"),"authority":"reconciliation_service"}
    connector = {"state":"AVAILABLE" if execution.get("provider") else "UNKNOWN","provider":execution.get("provider"),"providerObjectId":execution.get("provider_object_id"),"identitySource":"external_execution_ledger"}
    identity = {"workspaceId":workspace_id,"executionId":execution_id,"intentId":intent.get("intent_id") if intent else None,"decisionId":(intent or {}).get("decision_id"),"correlationId":(intent or {}).get("correlation_id"),"idempotencyKey":execution.get("idempotency_key") or (intent or {}).get("idempotency_key")}
    sections = {
      "intent":{"state":_state(intent),"data":_clean(intent)},
      "governance":{"state":_state(governance),"data":_clean(governance)},
      "actionKernel":{"state":"AVAILABLE","data":{"executionId":execution_id,"status":execution.get("status"),"attempt":execution.get("attempt"),"idempotencyKey":execution.get("idempotency_key"),"authority":"Action Kernel / durable execution ledger"}},
      "connector":{"state":connector["state"],"data":connector}, "providerReceipt":{"state":"AVAILABLE","data":provider_receipt}, "canonicalOutcome":{"state":outcome["state"],"data":outcome}, "verification":{"state":verification["state"],"data":verification}, "reconciliation":{"state":reconciliation["state"],"data":reconciliation}, "identity":{"state":"AVAILABLE","data":identity}}
    return {"schema":"CommandCenterExecutionDetail/v1","version":"vNext.1","workspaceId":workspace_id,"executionId":execution_id,"state":"LIVE","authority":{"readOnly":True,"actionAuthorityGranted":False,"reconciliationAuthorityGranted":False,"sourceOfTruth":"external_execution_ledger"},"sections":sections,"summary":{"status":execution.get("status"),"provider":execution.get("provider"),"intentId":identity["intentId"],"decisionId":identity["decisionId"],"reconciliationRequired":reconciliation["required"],"verificationState":verification["state"],"businessSuccess":verification["businessSuccess"]}}
