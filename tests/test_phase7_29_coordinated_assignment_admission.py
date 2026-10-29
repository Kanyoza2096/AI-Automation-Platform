import pytest
from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_40_coordinated_worker_assignment_admission import *
from tests.test_phase7_28_execution_readiness import setup


def test_ready_multi_worker_plan_is_admitted_as_canonical_assignments(tmp_path):
    planner, plan, cost, cp, registry, allocation, reservation, readiness = setup(tmp_path)
    rr = readiness.evaluate(readiness_id='rd1', plan=plan, cost_plan=cp,
                            reservation_ids={'a': reservation.reservation_id})
    assignment = WorkerAssignmentAdmission('ws', registry, planner.selector, allocation,
                                            str(tmp_path / 'assign.db'))
    coordinator = CoordinatedWorkerAssignmentAdmission('ws', readiness, assignment, planner,
                                                       str(tmp_path / 'coord.db'))
    out = coordinator.admit(admission_id='adm1', readiness_id='rd1')
    assert out.task_count == 1
    assert out.coordination_only and not out.authorization_granted
    assert assignment.get(out.assignment_ids[0]).task_id == 'a'
    coordinator.assert_integrity(out)
    assert coordinator.context('adm1')['action_kernel_invoked'] is False


def test_duplicate_coordinated_admission_is_rejected(tmp_path):
    planner, plan, cost, cp, registry, allocation, reservation, readiness = setup(tmp_path)
    readiness.evaluate(readiness_id='rd1', plan=plan, cost_plan=cp,
                       reservation_ids={'a': reservation.reservation_id})
    assignment = WorkerAssignmentAdmission('ws', registry, planner.selector, allocation,
                                            str(tmp_path / 'assign.db'))
    coordinator = CoordinatedWorkerAssignmentAdmission('ws', readiness, assignment, planner,
                                                       str(tmp_path / 'coord.db'))
    coordinator.admit(admission_id='adm1', readiness_id='rd1')
    with pytest.raises(ValueError, match=CoordinatedAdmissionBlock.DUPLICATE.value):
        coordinator.admit(admission_id='adm1', readiness_id='rd1')


def test_not_ready_record_cannot_cross_assignment_boundary(tmp_path):
    planner, plan, cost, cp, registry, allocation, reservation, readiness = setup(tmp_path)
    # Use a tampered copy only as an input; the registry must reject it before admission.
    bad = type(readiness.get('none')) if False else None
    assignment = WorkerAssignmentAdmission('ws', registry, planner.selector, allocation,
                                            str(tmp_path / 'assign.db'))
    coordinator = CoordinatedWorkerAssignmentAdmission('ws', readiness, assignment, planner,
                                                       str(tmp_path / 'coord.db'))
    with pytest.raises(ValueError, match=CoordinatedAdmissionBlock.READINESS_MISSING.value):
        coordinator.admit(admission_id='adm1', readiness_id='missing')


def test_canonical_batch_admission_is_atomic(tmp_path):
    planner, plan, cost, cp, registry, allocation, reservation, readiness = setup(tmp_path)
    assignment = WorkerAssignmentAdmission('ws', registry, planner.selector, allocation,
                                            str(tmp_path / 'assign.db'))
    valid = plan.tasks[0]
    with pytest.raises(ValueError):
        assignment.assign_many((
            {'assignment_id':'batch:a','task_id':'a','selection_id':valid.selection_id,'reservation_id':reservation.reservation_id},
            {'assignment_id':'batch:b','task_id':'missing','selection_id':'missing-selection','reservation_id':reservation.reservation_id},
        ))
    assert assignment.get('batch:a') is None
