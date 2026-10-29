from services.v71_37_workforce_multi_worker_planning import *

def test_schema_version_boundary(): assert WorkforceMultiWorkerPlanner.VERSION == 'phase7.26'
def test_plan_is_advisory(): assert MultiWorkerPlan.__dataclass_fields__['advisory_only'].default is True
def test_plan_has_no_authority_default():
    assert MultiWorkerPlan.__dataclass_fields__['authority_granted'].default is False
def test_plan_does_not_request_execution(): assert MultiWorkerPlan.__dataclass_fields__['execution_requested'].default is False
def test_plan_does_not_call_provider(): assert MultiWorkerPlan.__dataclass_fields__['provider_called'].default is False
def test_plan_does_not_invoke_kernel(): assert MultiWorkerPlan.__dataclass_fields__['action_kernel_invoked'].default is False
def test_planning_is_separate_from_assignment(): assert 'assignment' not in WorkforceMultiWorkerPlanner.__name__.lower()
def test_planner_exports_expected_types():
    assert all(x in __import__('services.v71_37_workforce_multi_worker_planning',fromlist=['__all__']).__all__ for x in ('CoordinationTaskSpec','MultiWorkerPlan','WorkforceMultiWorkerPlanner'))
