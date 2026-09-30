"""IT Operations command surface."""
from .repository import ITOperationsRepository

class ITOperationsCommands:
    def __init__(self, repository: ITOperationsRepository, redis_client=None): self.repo, self.redis = repository, redis_client
    def list_all(self):
        return [
            {"intent":"add_service","description":"Register an operational service","params":["name","environment","status"],"category":"services","execution_mode":"action_kernel","governed_action_type":"it_register_service"},
            {"intent":"create_incident","description":"Create an incident for a service","params":["service_id","title","severity"],"category":"incidents","execution_mode":"action_kernel","governed_action_type":"it_create_incident"},
            {"intent":"get_incident","description":"Read an incident","params":["incident_id"],"category":"incidents","execution_mode":"read_only"},
            {"intent":"update_incident","description":"Advance incident status","params":["incident_id","status"],"category":"incidents","execution_mode":"action_kernel","governed_action_type":"it_update_incident"},
            {"intent":"add_incident_update","description":"Record an incident timeline update","params":["incident_id","status","note"],"category":"incidents","execution_mode":"action_kernel","governed_action_type":"it_add_incident_update"},
            {"intent":"request_deployment","description":"Request a governed deployment","params":["service_id","version","environment"],"category":"deployments","execution_mode":"action_kernel","governed_action_type":"it_request_deployment"},
            {"intent":"get_deployment","description":"Read a deployment","params":["deployment_id"],"category":"deployments","execution_mode":"read_only"},
            {"intent":"record_deployment_outcome","description":"Record authoritative deployment outcome evidence","params":["deployment_id","status","provider_reference","evidence"],"category":"deployments","execution_mode":"action_kernel","governed_action_type":"it_record_deployment_outcome"},
            {"intent":"record_service_outcome","description":"Record service reliability or operational outcome evidence","params":["service_id","metric","value","source","evidence"],"category":"outcomes","execution_mode":"action_kernel","governed_action_type":"it_record_service_outcome"},
            {"intent":"remediate_service","description":"Request governed service remediation","params":["service_id","reason"],"category":"remediation","execution_mode":"action_kernel","governed_action_type":"it_remediate_service"},
        ]
    def handle(self, command, params, workspace_id):
        if command == "add_service":
            x=self.repo.add_service(workspace_id, params); return {"ok":True,"data":x.model_dump(mode="json")}
        if command == "create_incident":
            x=self.repo.create_incident(workspace_id,str(params.get("service_id","")),params); return {"ok":True,"data":x.model_dump(mode="json")}
        if command == "get_incident":
            x=self.repo.get_incident(workspace_id,str(params.get("incident_id",""))); return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None, **({} if x else {"error":"Incident not found"})}
        if command == "update_incident":
            x=self.repo.update_incident(workspace_id,str(params.get("incident_id","")),str(params.get("status","")).lower()); return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None, **({} if x else {"error":"Incident not found"})}
        if command == "add_incident_update":
            x=self.repo.add_incident_update(workspace_id,str(params.get("incident_id","")),str(params.get("status","")).lower(),params.get("note","")); return {"ok":True,"data":x.model_dump(mode="json")}
        if command == "request_deployment":
            x=self.repo.request_deployment(workspace_id,str(params.get("service_id","")),params); return {"ok":True,"data":x.model_dump(mode="json"),"execution_mode":"action_kernel"}
        if command == "get_deployment":
            x=self.repo.get_deployment(workspace_id,str(params.get("deployment_id",""))); return {"ok":bool(x),"data":x.model_dump(mode="json") if x else None, **({} if x else {"error":"Deployment not found"})}
        if command == "record_deployment_outcome":
            x=self.repo.record_deployment_outcome(workspace_id,str(params.get("deployment_id","")),str(params.get("status","")).lower(),params.get("provider_reference"),params.get("evidence")); return {"ok":True,"status":"observed","data":x.model_dump(mode="json")}
        if command == "record_service_outcome":
            x=self.repo.record_service_outcome(workspace_id,str(params.get("service_id","")),params.get("metric",""),params.get("value",0),params.get("source",""),params.get("evidence")); return {"ok":True,"status":"observed","data":x.model_dump(mode="json")}
        if command == "remediate_service":
            return self._deferred_external(params,workspace_id,"it_remediate_service")
        return {"ok":False,"error":f"Unknown IT operations command: {command}"}
    def _deferred_external(self, params, workspace_id, action_type):
        return {"ok":True,"status":"action_required","execution_mode":"action_kernel","workspace_id":workspace_id,"action_type":action_type,"parameters":{k:v for k,v in params.items() if k!="workspace_id"},"message":"External IT side effect will be implemented in the connector layer."}
