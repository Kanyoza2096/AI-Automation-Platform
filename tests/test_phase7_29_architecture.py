from services.v71_25_worker_assignment import WorkerAssignmentAdmission
from services.v71_40_coordinated_worker_assignment_admission import CoordinatedWorkerAssignmentAdmission


def test_phase_version_and_authority_boundary():
    assert CoordinatedWorkerAssignmentAdmission.VERSION == 'phase7.29'
    fields = CoordinatedWorkerAssignmentAdmission.__dict__
    assert 'admit' in fields
    assert 'assign_many' in WorkerAssignmentAdmission.__dict__


def test_coordinated_admission_does_not_expose_execution_authority():
    assert 'execute' not in CoordinatedWorkerAssignmentAdmission.__name__.lower()
