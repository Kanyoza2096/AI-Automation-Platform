"""Healthcare Administration / Hospital Operations sector pack."""
from plugins.industries.base import IndustryPlugin


class HealthcarePlugin(IndustryPlugin):
    pack_id = "healthcare"
    pack_version = "1.1"
    display_name = "Healthcare Administration & Hospital Operations"
    capabilities = ("patient_administration", "appointments", "encounters", "billing", "patient_communication", "care_coordination", "outcomes")
    resources = ("patients", "appointments", "encounters", "invoices", "care_plans", "healthcare_outcomes")
    policies = ("healthcare.workspace_access", "healthcare.patient_access", "healthcare.billing_authorization", "healthcare.communication_authorization", "healthcare.care_coordination_authorization", "healthcare.outcome_integrity")
    workflows = ("patient_lifecycle", "appointment_lifecycle", "encounter_lifecycle", "billing_cycle", "patient_communication", "care_coordination", "healthcare_outcome_loop")
    events = ("patient.created", "appointment.scheduled", "appointment.updated", "encounter.opened", "invoice.created", "patient_message.requested", "care_plan.recorded", "care_plan.updated", "healthcare.outcome.observed")
    external_actions = ("healthcare.send_patient_message", "healthcare.record_care_plan", "healthcare.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import HealthcareRepository
        from .commands import HealthcareCommands
        self.repo = HealthcareRepository(supabase_client)
        self.commands_handler = HealthcareCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/healthcare/appointment", "method": "POST", "handler": "handle_appointment_webhook", "description": "Appointment lifecycle event from an external healthcare system"},
            {"path": "/webhooks/healthcare/patient", "method": "POST", "handler": "handle_patient_webhook", "description": "Patient administration event from an external healthcare system"},
            {"path": "/webhooks/healthcare/outcome", "method": "POST", "handler": "handle_outcome_webhook", "description": "Observed administrative healthcare outcome from an external healthcare system"},
        ]
