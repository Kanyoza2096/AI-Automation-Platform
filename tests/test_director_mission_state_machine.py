import pytest

from services.director.orchestration.state_machine import (
    MissionEventCommand,
    MissionRuntimeSnapshot,
    MissionStateMachine,
    MissionTransitionRejected,
)


def _snapshot(*, status="admitted", version=0, state=None):
    return MissionRuntimeSnapshot(
        mission_id="mission-state-test",
        scope_type="workspace",
        workspace_id="workspace-state-test",
        version=version,
        status=status,
        phase="admission",
        task_ids=("task-a",),
        state_snapshot=state or MissionStateMachine.initial_state(
            ["task-a"], max_tokens=1000, max_provider_calls=8, max_reasoning_rounds=3
        ),
    )


def _command(event_type, *, actor_type="director", actor_id="director",
             key=None, task_id=None, payload=None, **kwargs):
    return MissionEventCommand(
        event_type=event_type,
        idempotency_key=key or f"key-{event_type}",
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id="corr-state-test",
        task_id=task_id,
        payload=payload or {},
        **kwargs,
    )


def _advance(machine, snapshot, command):
    return machine.prepare(snapshot, command).next_snapshot


def test_mission_requires_operator_approval_and_verification_before_completion():
    machine = MissionStateMachine()
    current = _snapshot()

    with pytest.raises(MissionTransitionRejected, match="task_approval_required_before_start"):
        machine.prepare(current, _command("task.started", task_id="task-a"))

    current = _advance(machine, current, _command(
        "task.assigned", task_id="task-a",
        payload={"engine_id": "content_intelligence", "capability": "research.analyze"},
    ))
    assert current.state_snapshot["task_status"]["task-a"] == "approval_required"

    with pytest.raises(MissionTransitionRejected, match="task_approval_requires_governed_actor"):
        machine.prepare(current, _command("task.approved", task_id="task-a"))

    current = _advance(machine, current, _command(
        "task.approved", actor_type="operator", actor_id="operator-1", task_id="task-a"
    ))
    current = _advance(machine, current, _command("task.started", task_id="task-a"))
    current = _advance(machine, current, _command(
        "task.completed", actor_type="engine", actor_id="content_intelligence",
        task_id="task-a", payload={"summary": "Research completed"},
    ))

    with pytest.raises(MissionTransitionRejected, match="task_verification_evidence_required"):
        machine.prepare(current, _command("task.verified", task_id="task-a"))

    current = _advance(machine, current, _command(
        "task.verified", task_id="task-a",
        payload={"verification_ref": "verification-1"},
    ))
    assert current.state_snapshot["task_status"]["task-a"] == "verified"

    current = _advance(machine, current, _command("mission.completed"))
    assert current.status == "completed"
    assert current.phase == "completed"


def test_engine_question_requires_correlated_answer_and_bounded_rounds():
    machine = MissionStateMachine(max_reasoning_rounds=2)
    current = _snapshot()

    question = _command(
        "engine.question.asked",
        actor_type="engine", actor_id="content_intelligence",
        recipient_type="engine", recipient_id="meta_engine",
        message_id="question-1", round_number=1,
        payload={"question": "Challenge the source quality."},
    )
    current = _advance(machine, current, question)
    assert "question-1" in current.state_snapshot["pending_questions"]

    bad_answer = _command(
        "engine.answer.received",
        actor_type="engine", actor_id="wrong_engine",
        recipient_type="engine", recipient_id="content_intelligence",
        message_id="answer-1", round_number=1,
        payload={"in_reply_to": "question-1", "answer": "Response"},
    )
    with pytest.raises(MissionTransitionRejected, match="answer_sender_or_recipient_mismatch"):
        machine.prepare(current, bad_answer)

    answer = _command(
        "engine.answer.received",
        actor_type="engine", actor_id="meta_engine",
        recipient_type="engine", recipient_id="content_intelligence",
        message_id="answer-1", round_number=1,
        payload={"in_reply_to": "question-1", "answer": "Evidence comparison"},
    )
    current = _advance(machine, current, answer)
    assert not current.state_snapshot["pending_questions"]

    current = _advance(machine, current, _command(
        "engine.question.asked",
        actor_type="engine", actor_id="meta_engine",
        recipient_type="engine", recipient_id="content_intelligence",
        message_id="question-2", round_number=2,
        payload={"question": "One final bounded check."},
    ))
    with pytest.raises(MissionTransitionRejected, match="reasoning_round_limit_exceeded"):
        machine.prepare(current, _command(
            "engine.question.asked",
            actor_type="engine", actor_id="meta_engine",
            recipient_type="engine", recipient_id="content_intelligence",
            message_id="question-3", round_number=3,
            payload={"question": "Unbounded extra round"},
        ))


def test_mission_budget_preflight_and_usage_pause_are_enforced():
    machine = MissionStateMachine()
    current = _snapshot()

    machine.ensure_budget_available(current, estimated_tokens=900, estimated_provider_calls=1)
    with pytest.raises(MissionTransitionRejected, match="mission_token_budget_would_be_exceeded"):
        machine.ensure_budget_available(current, estimated_tokens=1001)

    current = _advance(machine, current, _command(
        "engine.call.completed",
        actor_type="system", actor_id="llm_gateway",
        payload={"usage_tokens": 1000, "provider_calls": 1},
    ))
    assert current.status == "paused"
    assert current.state_snapshot["budget"]["exhausted"] is True

    with pytest.raises(MissionTransitionRejected, match="mission_budget_exhausted"):
        machine.ensure_budget_available(current)


def test_engine_cannot_resolve_disagreement_or_escalate_execution_authority():
    machine = MissionStateMachine()
    current = _snapshot()
    current = _advance(machine, current, _command(
        "engine.disagreement.detected",
        actor_type="engine", actor_id="risk_engine",
        payload={"disagreement_id": "d-1", "summary": "Conflicting source interpretation"},
    ))
    with pytest.raises(MissionTransitionRejected, match="disagreement_resolution_requires_director_or_operator"):
        machine.prepare(current, _command(
            "engine.disagreement.resolved",
            actor_type="engine", actor_id="risk_engine",
            payload={"disagreement_id": "d-1", "resolution": "I choose my own view"},
        ))
    assert current.state_snapshot["unresolved_disagreements"] == ["d-1"]
