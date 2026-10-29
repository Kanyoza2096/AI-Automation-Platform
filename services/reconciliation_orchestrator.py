"""Bounded orchestration for ambiguous external side effects.

This layer deliberately resolves only provider-authoritative outcomes. It never
creates a retry from a transport error or a single eventually-consistent miss.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from services.external_reconciliation import ExternalReconciler, ReconciliationResult
from services.sector_it_deployment_reconciliation import ITDeploymentReconciliationService
from services.sector_finance_payment_reconciliation import FinancePaymentReconciliationService
from services.sector_crm_message_reconciliation import CRMMessageReconciliationService
from services.sector_manufacturing_maintenance_reconciliation import ManufacturingMaintenanceReconciliationService

logger = logging.getLogger("kanyoza.reconciliation_orchestrator")

@dataclass(frozen=True)
class ReconciliationPolicy:
    max_attempts: int = 5
    not_found_confirmations: int = 2

class ReconciliationOrchestrator:
    def __init__(self, policy: Optional[ReconciliationPolicy] = None):
        self.policy = policy or ReconciliationPolicy()

    def reconcile_item(self, item: Any, connector: Any) -> ReconciliationResult:
        attempts = int(getattr(item, "reconciliation_attempts", 0) or 0)
        if attempts >= self.policy.max_attempts:
            return ReconciliationResult("unknown", getattr(item, "platform_post_id", None), "Reconciliation attempt budget exhausted; operator review required")
        return ExternalReconciler().reconcile(item, connector=connector)

    def reconcile_it_deployment(
        self,
        *,
        repository: Any,
        render_adapter: Any,
        workspace_id: str,
        service_id: str,
        deployment_id: str,
        provider_reference: str,
    ) -> dict[str, Any]:
        """Route an uncertain IT deployment through the canonical reconciler.

        This is orchestration only: it selects the already-authoritative IT
        reconciliation service. It never executes or retries a deployment.
        """
        return ITDeploymentReconciliationService(repository, render_adapter).reconcile(
            workspace_id=workspace_id,
            service_id=service_id,
            deployment_id=deployment_id,
            provider_reference=provider_reference,
        )

    def reconcile_it_service_remediation(self, *, repository, github_adapter, workspace_id: str, remediation_id: str) -> dict[str, Any]:
        from services.sector_it_service_remediation_reconciliation import ITServiceRemediationReconciliationService
        return ITServiceRemediationReconciliationService(repository, github_adapter).reconcile(workspace_id=workspace_id, remediation_id=remediation_id)

    def reconcile_it_service_remediations(self, *, repository, github_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_service_remediations(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "uncertain": 0}
        for item in pending:
            try:
                result = self.reconcile_it_service_remediation(repository=repository, github_adapter=github_adapter, workspace_id=item.workspace_id, remediation_id=item.id)
            except Exception:
                logger.exception("IT service remediation reconciliation failed for %s", item.id)
                stats["uncertain"] += 1
                continue
            stats["reconciled"] += 1
            if result.get("terminal"): stats["terminal"] += 1
            else: stats["in_progress"] += 1
        return stats


    def reconcile_manufacturing_maintenance(self, *, repository, jira_adapter, workspace_id: str, request_id: str) -> dict[str, Any]:
        return ManufacturingMaintenanceReconciliationService(repository, jira_adapter).reconcile(workspace_id=workspace_id, request_id=request_id)

    def reconcile_manufacturing_maintenances(self, *, repository, jira_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_maintenance_requests(workspace_id=workspace_id, limit=limit)
        stats = {"discovered":len(pending),"reconciled":0,"terminal":0,"in_progress":0,"uncertain":0}
        for item in pending:
            try: result=self.reconcile_manufacturing_maintenance(repository=repository,jira_adapter=jira_adapter,workspace_id=item.workspace_id,request_id=item.id)
            except Exception:
                logger.exception("Manufacturing maintenance reconciliation failed for %s", item.id); stats["uncertain"]+=1; continue
            stats["reconciled"]+=1
            if result.get("terminal"): stats["terminal"]+=1
            else: stats["in_progress"]+=1
        return stats
    def reconcile_government_applicant_notification(self, *, repository, twilio_adapter, workspace_id: str, notification_id: str) -> dict[str, Any]:
        from services.sector_government_applicant_notification_reconciliation import GovernmentApplicantNotificationReconciliationService
        return GovernmentApplicantNotificationReconciliationService(repository, twilio_adapter).reconcile(workspace_id=workspace_id, notification_id=notification_id)

    def reconcile_government_applicant_notifications(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required(workspace_id=workspace_id, limit=limit)
        stats={"discovered":len(pending),"reconciled":0,"terminal":0,"in_progress":0,"operator_review":0,"uncertain":0}
        for item in pending:
            try: result=self.reconcile_government_applicant_notification(repository=repository,twilio_adapter=twilio_adapter,workspace_id=item.workspace_id,notification_id=item.id)
            except Exception:
                logger.exception("Government applicant notification reconciliation failed for %s", item.id); stats["uncertain"]+=1; continue
            stats["reconciled"]+=1
            if result.get("terminal"): stats["terminal"]+=1
            elif result.get("status")=="in_progress": stats["in_progress"]+=1
            elif result.get("status")=="operator_review": stats["operator_review"]+=1
            else: stats["uncertain"]+=1
        return stats

    def reconcile_government_field_service(self, *, repository, jira_adapter, workspace_id: str, dispatch_id: str) -> dict[str, Any]:
        from services.sector_government_field_service_reconciliation import GovernmentFieldServiceReconciliationService
        return GovernmentFieldServiceReconciliationService(repository, jira_adapter).reconcile(workspace_id=workspace_id, dispatch_id=dispatch_id)

    def reconcile_government_field_services(self, *, repository, jira_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_field_service_dispatches(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "operator_review": 0, "uncertain": 0}
        for item in pending:
            try:
                result = self.reconcile_government_field_service(repository=repository, jira_adapter=jira_adapter, workspace_id=item.workspace_id, dispatch_id=item.id)
            except Exception:
                logger.exception("Government field-service reconciliation failed for %s", item.id); stats["uncertain"] += 1; continue
            stats["reconciled"] += 1
            if result.get("terminal"): stats["terminal"] += 1
            elif result.get("status") == "in_progress": stats["in_progress"] += 1
            elif result.get("operator_review"): stats["operator_review"] += 1
            else: stats["uncertain"] += 1
        return stats

    def reconcile_energy_crew_dispatch(self, *, repository, jira_adapter, workspace_id: str, dispatch_id: str) -> dict[str, Any]:
        from services.sector_energy_crew_dispatch_reconciliation import EnergyCrewDispatchReconciliationService
        return EnergyCrewDispatchReconciliationService(repository, jira_adapter).reconcile(workspace_id=workspace_id, dispatch_id=dispatch_id)

    def reconcile_energy_crew_dispatches(self, *, repository, jira_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_crew_dispatches(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "operator_review": 0, "uncertain": 0}
        for item in pending:
            try:
                result = self.reconcile_energy_crew_dispatch(repository=repository, jira_adapter=jira_adapter, workspace_id=item.workspace_id, dispatch_id=item.id)
            except Exception:
                logger.exception("Energy crew-dispatch reconciliation failed for %s", item.id); stats["uncertain"] += 1; continue
            stats["reconciled"] += 1
            if result.get("terminal"): stats["terminal"] += 1
            elif result.get("status") == "in_progress": stats["in_progress"] += 1
            elif result.get("operator_review"): stats["operator_review"] += 1
            else: stats["uncertain"] += 1
        return stats

    def reconcile_finance_statement(self, *, repository, twilio_adapter, workspace_id: str, statement_delivery_id: str) -> dict[str, Any]:
        from services.sector_finance_statement_reconciliation import FinanceStatementReconciliationService
        return FinanceStatementReconciliationService(repository, twilio_adapter).reconcile(workspace_id=workspace_id, statement_delivery_id=statement_delivery_id)

    def reconcile_finance_statements(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_statement_deliveries(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "operator_review": 0, "uncertain": 0}
        for item in pending:
            try:
                result = self.reconcile_finance_statement(repository=repository, twilio_adapter=twilio_adapter, workspace_id=item.workspace_id, statement_delivery_id=item.id)
            except Exception:
                logger.exception("Finance statement reconciliation failed for %s", item.id); stats["uncertain"] += 1; continue
            stats["reconciled"] += 1
            if result.get("terminal"): stats["terminal"] += 1
            elif result.get("status") == "in_progress": stats["in_progress"] += 1
            elif result.get("status") == "operator_review": stats["operator_review"] += 1
            else: stats["uncertain"] += 1
        return stats

    def reconcile_finance_payment(self, *, repository, stripe_adapter, workspace_id: str, payment_id: str, provider_reference: str | None = None) -> dict[str, Any]:
        """Route a Finance payment through the canonical lifecycle reconciler."""
        return FinancePaymentReconciliationService(repository, stripe_adapter).reconcile(
            workspace_id=workspace_id, payment_id=payment_id, provider_reference=provider_reference
        )

    def reconcile_finance_payments(self, *, repository, stripe_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_payments(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "uncertain": 0, "skipped": 0}
        for payment in pending:
            if not payment.provider_payment_intent_id:
                stats["skipped"] += 1
                continue
            try:
                result = self.reconcile_finance_payment(
                    repository=repository, stripe_adapter=stripe_adapter, workspace_id=payment.workspace_id,
                    payment_id=payment.id, provider_reference=payment.provider_payment_intent_id
                )
            except Exception:
                logger.exception("Finance payment reconciliation failed for %s", payment.id)
                stats["uncertain"] += 1
                continue
            stats["reconciled"] += 1
            if result.get("terminal"):
                stats["terminal"] += 1
            elif result.get("status") == "in_progress":
                stats["in_progress"] += 1
            else:
                stats["uncertain"] += 1
        return stats

    def reconcile_crm_message(self, *, repository, twilio_adapter, workspace_id: str, delivery_id: str) -> dict[str, Any]:
        """Route a CRM message delivery through the canonical provider-read reconciler."""
        return CRMMessageReconciliationService(repository, twilio_adapter).reconcile(
            workspace_id=workspace_id, delivery_id=delivery_id
        )

    def reconcile_crm_messages(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_reconciliation_required_message_deliveries(workspace_id=workspace_id, limit=limit)
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "operator_review": 0, "uncertain": 0, "skipped": 0}
        for delivery in pending:
            try:
                result = self.reconcile_crm_message(
                    repository=repository, twilio_adapter=twilio_adapter,
                    workspace_id=delivery.workspace_id, delivery_id=delivery.id,
                )
            except Exception:
                logger.exception("CRM message reconciliation failed for %s", delivery.id)
                stats["uncertain"] += 1
                continue
            stats["reconciled"] += 1
            if result.get("terminal"):
                stats["terminal"] += 1
            elif result.get("status") == "in_progress":
                stats["in_progress"] += 1
            elif result.get("status") == "operator_review":
                stats["operator_review"] += 1
            else:
                stats["uncertain"] += 1
        return stats


    def reconcile_education_parent_message(self, *, repository, twilio_adapter, workspace_id: str, delivery_id: str) -> dict[str, Any]:
        from services.sector_education_parent_message_reconciliation import EducationParentMessageReconciliationService
        return EducationParentMessageReconciliationService(repository, twilio_adapter).reconcile(workspace_id=workspace_id, delivery_id=delivery_id)

    def reconcile_education_parent_messages(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending=repository.list_reconciliation_required_parent_message_deliveries(workspace_id=workspace_id, limit=limit)
        stats={"discovered":len(pending),"reconciled":0,"terminal":0,"in_progress":0,"operator_review":0,"uncertain":0,"skipped":0}
        for delivery in pending:
            try: result=self.reconcile_education_parent_message(repository=repository,twilio_adapter=twilio_adapter,workspace_id=delivery.workspace_id,delivery_id=delivery.id)
            except Exception:
                logger.exception("Education parent message reconciliation failed for %s", delivery.id); stats["uncertain"]+=1; continue
            stats["reconciled"]+=1
            if result.get("terminal"): stats["terminal"]+=1
            elif result.get("status")=="in_progress": stats["in_progress"]+=1
            elif result.get("status")=="operator_review": stats["operator_review"]+=1
            else: stats["uncertain"]+=1
        return stats

    def reconcile_healthcare_patient_message(self, *, repository, twilio_adapter, workspace_id: str, delivery_id: str) -> dict[str, Any]:
        from services.sector_healthcare_patient_message_reconciliation import HealthcarePatientMessageReconciliationService
        return HealthcarePatientMessageReconciliationService(repository, twilio_adapter).reconcile(workspace_id=workspace_id, delivery_id=delivery_id)

    def reconcile_healthcare_patient_messages(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending=repository.list_reconciliation_required_patient_message_deliveries(workspace_id=workspace_id, limit=limit)
        stats={"discovered":len(pending),"reconciled":0,"terminal":0,"in_progress":0,"operator_review":0,"uncertain":0,"skipped":0}
        for delivery in pending:
            try: result=self.reconcile_healthcare_patient_message(repository=repository,twilio_adapter=twilio_adapter,workspace_id=delivery.workspace_id,delivery_id=delivery.id)
            except Exception:
                logger.exception("Healthcare patient message reconciliation failed for %s", delivery.id); stats["uncertain"]+=1; continue
            stats["reconciled"]+=1
            if result.get("terminal"): stats["terminal"]+=1
            elif result.get("status")=="in_progress": stats["in_progress"]+=1
            elif result.get("status")=="operator_review": stats["operator_review"]+=1
            else: stats["uncertain"]+=1
        return stats

    def reconcile_it_deployments(
        self,
        *,
        repository,
        render_adapter,
        workspace_id=None,
        limit: int = 100,
    ) -> dict[str, int]:
        """Reconcile durable uncertain IT deployments without re-execution.

        This is the scheduled reconciliation entrypoint. Pending work is
        discovered from the durable IT repository, then each item is routed
        through the canonical IT deployment reconciler. The orchestrator never
        calls the deployment Action Kernel handler and never creates a retry.
        """
        pending = repository.list_reconciliation_required_deployments(
            workspace_id=workspace_id, limit=limit
        )
        stats = {"discovered": len(pending), "reconciled": 0, "terminal": 0, "in_progress": 0, "uncertain": 0, "skipped": 0}
        for item in pending:
            provider_reference = item.get("provider_reference")
            if not provider_reference:
                stats["skipped"] += 1
                continue
            attempts = int(item.get("reconciliation_attempts", 0) or 0)
            if attempts >= self.policy.max_attempts:
                repository.record_reconciliation_evidence(
                    item["workspace_id"], item["deployment_id"], item["service_id"], "render",
                    "unknown", provider_reference, attempts + 1, "exhausted", False,
                    {"reason": "reconciliation_attempt_budget_exhausted"}, "operator_review",
                    {"reconciliation_state": "operator_review", "operator_review_required": True},
                )
                stats["uncertain"] += 1
                continue
            result = self.reconcile_it_deployment(
                repository=repository,
                render_adapter=render_adapter,
                workspace_id=item["workspace_id"],
                service_id=item["service_id"],
                deployment_id=item["deployment_id"],
                provider_reference=provider_reference,
            )
            stats["reconciled"] += 1
            if result.get("terminal"):
                stats["terminal"] += 1
            elif result.get("status") == "in_progress":
                stats["in_progress"] += 1
            else:
                stats["uncertain"] += 1
        return stats

    def reconcile_commerce_order_notification(self, *, repository, twilio_adapter, workspace_id: str, delivery_id: str) -> dict[str, Any]:
        from services.sector_commerce_order_notification_reconciliation import CommerceOrderNotificationReconciliationService
        return CommerceOrderNotificationReconciliationService(repository, twilio_adapter).reconcile(workspace_id=workspace_id, delivery_id=delivery_id)

    def reconcile_commerce_order_notifications(self, *, repository, twilio_adapter, workspace_id=None, limit: int = 100) -> dict[str, int]:
        pending = repository.list_pending_order_notification_deliveries(workspace_id=workspace_id, limit=limit)
        stats={"discovered":len(pending),"reconciled":0,"terminal":0,"in_progress":0,"operator_review":0,"uncertain":0,"skipped":0}
        for delivery in pending:
            try: result=self.reconcile_commerce_order_notification(repository=repository,twilio_adapter=twilio_adapter,workspace_id=delivery.workspace_id,delivery_id=delivery.id)
            except Exception:
                logger.exception("Commerce order notification reconciliation failed for %s", delivery.id); stats["uncertain"]+=1; continue
            stats["reconciled"]+=1
            if result.get("terminal"): stats["terminal"]+=1
            elif result.get("status")=="in_progress": stats["in_progress"]+=1
            elif result.get("status")=="operator_review": stats["operator_review"]+=1
            else: stats["uncertain"]+=1

    def reconcile_many(self, items: Iterable[Any], connector_resolver: Callable[[Any], Any]) -> dict[str, int]:
        stats = {"checked": 0, "published": 0, "requeued": 0, "uncertain": 0, "skipped": 0}
        for item in items:
            if getattr(item, "status", None) != "uncertain":
                stats["skipped"] += 1
                continue
            stats["checked"] += 1
            connector = connector_resolver(item)
            if connector is None:
                stats["uncertain"] += 1
                continue
            result = self.reconcile_item(item, connector)
            if result.status == "published":
                stats["published"] += 1
            elif result.status == "not_found":
                # State transition is applied by PublishOutbox, which owns
                # durable counters and persistence.
                stats["requeued"] += 1
            else:
                stats["uncertain"] += 1
        return stats
