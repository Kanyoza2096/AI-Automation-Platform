from services.ipc18_workforce_coordination import *
import pytest

WS="ws-1"; OBJ="obj-1"; CYCLE="cycle-1"

def plan():
    return create_plan(workspace_id=WS, objective_id=OBJ, cycle_id=CYCLE, coordinator_worker_id="w0", coordination_id="coord-1", tasks=(
        WorkforceTask("a","w1","research","Research"),
        WorkforceTask("b","w2","publish","Publish",depends_on=("a",)),
    ))

def test_dependencies_gate_parallel_workers():
    p=plan(); assert [t.task_id for t in ready_tasks(p)] == ["a"]
    p=claim_task(plan=p,task_id="a",worker_id="w1")
    ctx=execution_context(plan=p,task_id="a"); validate_execution_context(ctx,workspace_id=WS,objective_id=OBJ,cycle_id=CYCLE)
    p=mark_task(plan=p,task_id="a",worker_id="w1",state=TaskState.COMPLETED)
    assert [t.task_id for t in ready_tasks(p)] == ["b"]

def test_worker_cannot_claim_another_workers_task():
    with pytest.raises(ValueError,match="worker assignment"):
        claim_task(plan=plan(),task_id="a",worker_id="w2")

def test_cross_tenant_and_cycle_are_rejected():
    ctx=execution_context(plan=claim_task(plan=plan(),task_id="a",worker_id="w1"),task_id="a")
    with pytest.raises(ValueError,match="workspace mismatch"):
        validate_execution_context(ctx,workspace_id="ws-2",objective_id=OBJ,cycle_id=CYCLE)
    with pytest.raises(ValueError,match="cycle mismatch"):
        validate_execution_context(ctx,workspace_id=WS,objective_id=OBJ,cycle_id="cycle-2")

def test_tampering_and_authority_injection_are_rejected():
    p=plan()
    with pytest.raises(ValueError,match="fingerprint"):
        WorkforceCoordinationPlan(**{**p.__dict__,"objective_id":"obj-evil"}) and ready_tasks(WorkforceCoordinationPlan(**{**p.__dict__,"objective_id":"obj-evil"}))
    p=claim_task(plan=p,task_id="a",worker_id="w1"); ctx=dict(execution_context(plan=p,task_id="a")); ctx["authority"]="allow"
    with pytest.raises(ValueError,match="authority"):
        validate_execution_context(ctx,workspace_id=WS,objective_id=OBJ,cycle_id=CYCLE)

def test_dependency_cycles_rejected():
    with pytest.raises(ValueError,match="cycle"):
        create_plan(workspace_id=WS,objective_id=OBJ,cycle_id=CYCLE,coordinator_worker_id="w0",coordination_id="c",tasks=(
            WorkforceTask("a","w1","x","A",depends_on=("b",)), WorkforceTask("b","w2","y","B",depends_on=("a",))))
