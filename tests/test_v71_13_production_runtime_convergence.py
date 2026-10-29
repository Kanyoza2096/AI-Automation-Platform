import pytest
from service_container import ServiceContainer
from services.business_automation import BusinessAutomationEngine
from services.v71_13_production_runtime_convergence import ProductionRuntimeConvergence


def test_boot_verification_composes_v70_v71_layers():
    c = ServiceContainer()
    c.register_instance(BusinessAutomationEngine, BusinessAutomationEngine())
    r = ProductionRuntimeConvergence(container=c, db=object())
    status = r.verify_boot()
    assert status["boot_verified"] is True
    assert status["parallel_execution_plane"] is False


def test_workspace_composition_is_tenant_scoped():
    c = ServiceContainer()
    c.register_instance(BusinessAutomationEngine, BusinessAutomationEngine())
    r = ProductionRuntimeConvergence(container=c, db=object())
    a = r.workspace_runtime("w-a")
    b = r.workspace_runtime("w-b")
    assert a["business_operating_layer"].workspace_id == "w-a"
    assert b["business_operating_layer"].workspace_id == "w-b"
    assert a["agent_identity"].workspace_id != b["agent_identity"].workspace_id
    assert r.workspace_runtime("w-a") is a
