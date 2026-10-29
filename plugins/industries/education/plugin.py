"""Education / School Operations sector pack."""
from plugins.industries.base import IndustryPlugin


class EducationPlugin(IndustryPlugin):
    pack_id = "education"
    pack_version = "1.1"
    display_name = "Education & School Operations"
    capabilities = ("student_management", "attendance", "assessment", "fee_management", "intervention", "parent_communication", "outcomes")
    resources = ("students", "attendance", "assessments", "fees", "guardians", "interventions", "education_outcomes")
    policies = ("education.workspace_access", "education.student_access", "education.financial_authorization", "education.intervention_authorization", "education.communication_authorization", "education.outcome_integrity")
    workflows = ("student_lifecycle", "attendance_cycle", "assessment_cycle", "fee_collection", "student_intervention", "parent_communication", "education_outcome_loop")
    events = ("student.created", "student.updated", "attendance.recorded", "assessment.recorded", "fee.recorded", "intervention.recorded", "parent_message.requested", "education.outcome.observed")
    external_actions = ("education.send_parent_message", "education.record_intervention", "education.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import EducationRepository
        from .commands import EducationCommands
        self.repo = EducationRepository(supabase_client)
        self.commands_handler = EducationCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/education/attendance", "method": "POST", "handler": "handle_attendance_webhook", "description": "Attendance event from an external school system"},
            {"path": "/webhooks/education/student", "method": "POST", "handler": "handle_student_webhook", "description": "Student lifecycle event from an external school system"},
            {"path": "/webhooks/education/outcome", "method": "POST", "handler": "handle_outcome_webhook", "description": "Observed education outcome from an external school system"},
        ]
