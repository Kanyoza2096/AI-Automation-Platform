import services.v71_38_workforce_resource_cost_coordination as m
from services.v71_38_workforce_resource_cost_coordination import *
def test_version(): assert WorkforceResourceCostCoordinator.VERSION == 'phase7.27'
def test_authority_defaults():
    f=ResourceCostPlan.__dataclass_fields__
    assert f['advisory_only'].default is True and f['authority_granted'].default is False
    assert f['execution_requested'].default is False and f['provider_called'].default is False and f['action_kernel_invoked'].default is False
def test_expected_exports():
    assert all(x in m.__all__ for x in ('ResourceCostEstimate','ResourceCostPolicy','ResourceCostPlan','WorkforceResourceCostCoordinator'))
