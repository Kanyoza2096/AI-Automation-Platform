import pytest

from services.action_kernel import build_default_action_kernel
from plugins.industries.crm.repository import CRMRepository
from plugins.industries.education.repository import EducationRepository
from plugins.industries.healthcare.repository import HealthcareRepository
from plugins.industries.supply_chain.repository import SupplyChainRepository
from plugins.industries.energy.repository import EnergyRepository
from plugins.industries.government.repository import GovernmentRepository
from plugins.industries.agriculture.repository import AgricultureRepository


@pytest.mark.asyncio
async def test_phase4_internal_record_actions_execute_through_action_kernel():
    ws = "ws-internal-records"
    crm = CRMRepository(None)
    education = EducationRepository(None)
    healthcare = HealthcareRepository(None)
    supply = SupplyChainRepository(None)
    energy = EnergyRepository(None)
    government = GovernmentRepository(None)
    agriculture = AgricultureRepository(None)

    shipment = supply.create_shipment(ws, {"reference": "S1", "origin": "A", "destination": "B"})
    asset = energy.create_asset(ws, {"name": "Substation 1", "asset_type": "substation"})
    service = government.create_service(ws, {"name": "Permit Desk", "category": "permit"})
    request = government.create_request(ws, {"service_id": service.id, "title": "Permit request"})
    farm = agriculture.create_farm(ws, {"name": "Farm 1", "farm_type": "crop", "location": "Zone A"})
    field = agriculture.create_field(ws, {"farm_id": farm.id, "name": "Field 1", "area": 1.0})

    kernel = build_default_action_kernel(
        allow_in_memory=True,
        crm_repository=crm,
        education_repository=education,
        healthcare_repository=healthcare,
        supply_chain_repository=supply,
        energy_repository=energy,
        government_repository=government,
        agriculture_repository=agriculture,
    )

    cases = [
        ("crm_record_activity", {"activity_type": "call", "subject": "Check-in"}),
        ("crm_record_outcome", {"metric": "conversion_rate", "value": 0.4, "source": "test", "evidence": {"case": "1"}}),
        ("education_record_intervention", {"student_id": "student-1", "intervention_type": "support"}),
        ("education_record_outcome", {"metric": "attendance_rate", "value": 0.9, "source": "test", "evidence": {}}),
        ("healthcare_record_care_plan", {"patient_id": "patient-1", "plan_type": "follow_up"}),
        ("healthcare_record_outcome", {"metric": "follow_up_complete", "value": 1, "source": "test", "evidence": {}}),
        ("supply_chain_record_exception", {"shipment_id": shipment.id, "exception_type": "delay", "severity": "medium"}),
        ("supply_chain_record_outcome", {"reference_type": "shipment", "reference_id": shipment.id, "outcome_type": "delivered"}),
        ("energy_record_exception", {"asset_id": asset.id, "exception_type": "voltage", "severity": "low"}),
        ("energy_record_outcome", {"reference_type": "asset", "reference_id": asset.id, "outcome_type": "restored"}),
        ("government_record_resolution", {"request_id": request.id, "resolution": "Resolved", "status": "verified"}),
        ("government_record_outcome", {"reference_type": "service_request", "reference_id": request.id, "outcome_type": "resolved"}),
        ("agriculture_record_exception", {"field_id": field.id, "exception_type": "pest", "severity": "medium"}),
        ("agriculture_record_outcome", {"reference_type": "field", "reference_id": field.id, "outcome_type": "healthy"}),
    ]

    for index, (action_type, parameters) in enumerate(cases):
        result = await kernel.execute(
            workspace_id=ws,
            action_type=action_type,
            parameters=parameters,
            risk_level="medium" if any(token in action_type for token in ("exception", "intervention", "care_plan", "resolution")) else "low",
            decision_id=f"decision-{index}",
            idempotency_key=f"internal-record-{index}",
            source="architecture-test",
        )
        assert result["status"] == "succeeded", (action_type, result)
        assert result["output"]["internal_receipt"]["kind"] == "KDOInternalDomainReceipt"
        assert result["reconciliation_required"] is False
        assert result["output"]["object_id"]
