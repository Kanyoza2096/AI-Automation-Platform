"""Cybersecurity completion command surface."""
from .repository import CybersecurityRepository


class CybersecurityCommands:
    def __init__(self, repository: CybersecurityRepository, redis_client=None):
        self.repo, self.redis = repository, redis_client

    def list_all(self):
        return [
            {"intent":"add_asset","description":"Register a security-managed asset","params":["name","asset_type","environment","criticality","status"],"category":"assets","execution_mode":"action_kernel","governed_action_type":"security_register_asset"},
            {"intent":"create_finding","description":"Create a security finding","params":["asset_id","title","severity","source"],"category":"findings","execution_mode":"action_kernel","governed_action_type":"security_create_finding"},
            {"intent":"get_finding","description":"Read a security finding","params":["finding_id"],"category":"findings","execution_mode":"read_only"},
            {"intent":"update_finding","description":"Advance or verify a finding","params":["finding_id","status"],"category":"findings","execution_mode":"action_kernel","governed_action_type":"security_update_finding"},
            {"intent":"create_alert","description":"Record a security alert","params":["asset_id","title","severity","source"],"category":"alerts","execution_mode":"action_kernel","governed_action_type":"security_create_alert"},
            {"intent":"create_incident","description":"Open a security incident for coordinated response","params":["title","severity","source"],"category":"response","execution_mode":"action_kernel","governed_action_type":"security_create_incident"},
            {"intent":"update_incident","description":"Advance a security incident lifecycle","params":["incident_id","status"],"category":"response","execution_mode":"action_kernel","governed_action_type":"security_update_incident"},
            {"intent":"contain_asset","description":"Request governed containment of an asset","params":["asset_id","reason"],"category":"response","execution_mode":"action_kernel","governed_action_type":"security_contain_asset"},
            {"intent":"remediate_finding","description":"Request governed remediation of a finding","params":["finding_id","reason"],"category":"response","execution_mode":"action_kernel","governed_action_type":"security_remediate_finding"},
            {"intent":"record_response_outcome","description":"Record authoritative security-response provider outcome","params":["action_type","target_id","status","provider_reference","evidence"],"category":"response","execution_mode":"action_kernel","governed_action_type":"security_record_response_outcome"},
            {"intent":"record_outcome","description":"Record an observed security outcome","params":["resource_type","resource_id","metric","value","source","evidence"],"category":"analytics","execution_mode":"action_kernel","governed_action_type":"security_record_outcome"},
        ]

    def handle(self, command, params, workspace_id):
        handlers = {
            "add_asset": lambda: self.repo.add_asset(workspace_id, params),
            "create_finding": lambda: self.repo.create_finding(workspace_id, str(params.get("asset_id", "")), params),
            "get_finding": lambda: self.repo.get_finding(workspace_id, str(params.get("finding_id", ""))),
            "update_finding": lambda: self.repo.update_finding(workspace_id, str(params.get("finding_id", "")), str(params.get("status", "")).lower()),
            "create_alert": lambda: self.repo.create_alert(workspace_id, params),
            "create_incident": lambda: self.repo.create_incident(workspace_id, params),
            "update_incident": lambda: self.repo.update_incident(workspace_id, str(params.get("incident_id", "")), str(params.get("status", "")).lower()),
            "record_response_outcome": lambda: self.repo.record_response_outcome(workspace_id, params),
            "record_outcome": lambda: self.repo.record_outcome(workspace_id, params),
        }
        if command in {"contain_asset", "remediate_finding"}:
            return self._deferred_external(params, workspace_id, command)
        handler = handlers.get(command)
        if not handler:
            return {"ok":False,"error":f"Unknown cybersecurity command: {command}"}
        x = handler()
        if command == "get_finding":
            return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None,"error":None if x else "Finding not found"}
        if command in {"update_finding", "update_incident"}:
            return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None,"error":None if x else "Resource not found"}
        status = "observed" if command in {"record_response_outcome", "record_outcome"} else None
        result = {"ok":True,"data":x.model_dump(mode="json"),"execution_mode":"action_kernel"}
        if status: result["status"] = status
        return result

    def _deferred_external(self, params, workspace_id, command):
        action_type = "security_contain_asset" if command == "contain_asset" else "security_remediate_finding"
        return {"ok":True,"status":"action_required","execution_mode":"action_kernel","workspace_id":workspace_id,"action_type":action_type,"parameters":{k:v for k,v in params.items() if k!="workspace_id"},"message":"External security side effect will be implemented in the connector layer."}
