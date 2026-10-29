"""IT Operations sector pack — services, incidents, deployments, remediation and outcomes."""
from plugins.industries.base import IndustryPlugin

class ITOperationsPlugin(IndustryPlugin):
    pack_id="it_operations"; pack_version="1.1"; display_name="IT Operations"
    capabilities=("service_inventory","incident_management","incident_timeline","deployment_governance","deployment_outcomes","service_reliability_outcomes","remediation")
    resources=("services","incidents","incident_updates","deployments","deployment_outcomes","service_outcomes","environments")
    policies=("it.workspace_access","it.incident_access","it.deployment_authorization","it.outcome_integrity","it.remediation_authorization")
    workflows=("incident_response","incident_lifecycle","deployment_lifecycle","deployment_verification","service_reliability","service_remediation")
    events=("service.created","incident.created","incident.updated","incident.update.recorded","deployment.requested","deployment.outcome.observed","service.outcome.observed","remediation.requested")
    external_actions=("it.request_deployment","it.remediate_service")
    def __init__(self,supabase_client=None,redis_client=None):
        super().__init__(supabase_client,redis_client)
        from .repository import ITOperationsRepository
        from .commands import ITOperationsCommands
        self.repo=ITOperationsRepository(supabase_client); self.commands_handler=ITOperationsCommands(self.repo,redis_client)
    def get_commands(self): return self.commands_handler.list_all()
    def execute(self,command,params,workspace_id="default"):
        workspace_id=self.resolve_workspace(workspace_id)
        safe={k:v for k,v in (params or {}).items() if k!="workspace_id"}
        return self.commands_handler.handle(command,safe,workspace_id)
    def get_webhooks(self):
        return [
          {"path":"/webhooks/it/incident","method":"POST","handler":"handle_incident_webhook","description":"Incident event from monitoring/ITSM provider"},
          {"path":"/webhooks/it/deployment","method":"POST","handler":"handle_deployment_webhook","description":"Deployment status event from CI/CD provider"},
        ]
