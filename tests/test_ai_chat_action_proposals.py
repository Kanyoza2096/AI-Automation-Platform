import asyncio
from types import SimpleNamespace

from api.chat_action_proposals import (
    build_chat_action_proposal,
    is_explicit_action_request,
)


class FakeGateway:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def complete_async_structured(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(ok=True, data=self.data)


def _proposal(**overrides):
    data = {
        "proposed": True,
        "title": "Improve workspace reliability",
        "description": "Create an advisory plan to improve reliability using verified workspace evidence.",
        "category": "operations",
        "success_criteria": ["A measurable reliability baseline is documented."],
        "constraints": ["No mission is staged or executed."],
        "clarifying_question": "",
    }
    data.update(overrides)
    return data


def test_action_request_detection_requires_direct_instruction():
    assert is_explicit_action_request("Please run a security scan")
    assert is_explicit_action_request("Can you create a strategic plan?")
    assert is_explicit_action_request("I need you to stage a topic")
    assert not is_explicit_action_request("Why does KDO say it cannot run scans?")
    assert not is_explicit_action_request("Tell me how strategic planning works")


def test_director_proposal_uses_server_owned_command_and_requires_success_criteria():
    gateway = FakeGateway(_proposal())
    result = asyncio.run(build_chat_action_proposal(
        gateway, workspace_id="ws-1", engine="director",
        message="Create a strategic plan",
    ))
    assert result["status"] == "ready"
    assert result["command_id"] == "director.strategic_plan.create"
    assert result["risk_level"] == "medium"
    assert result["requires_operator_submission"] is True
    assert result["execution_authority"] == "none"
    assert result["executed"] is False
    assert "command ID" in gateway.calls[0]["prompt"]


def test_director_proposal_without_success_criteria_requires_clarification():
    gateway = FakeGateway(_proposal(success_criteria=[]))
    result = asyncio.run(build_chat_action_proposal(
        gateway, workspace_id="ws-1", engine="director",
        message="Create a strategic plan",
    ))
    assert result["status"] == "needs_clarification"
    assert result["clarifying_question"]


def test_guardian_handoff_is_scan_only():
    gateway = FakeGateway(_proposal())
    result = asyncio.run(build_chat_action_proposal(
        gateway, workspace_id="ws-1", engine="guardian",
        message="Run a security scan",
    ))
    assert result["command_id"] == "guardian.scan"
    assert result["risk_level"] == "medium"
    assert "lockdown" in gateway.calls[0]["prompt"]


def test_content_handoff_only_stages_a_topic_candidate():
    gateway = FakeGateway(_proposal(category="business"))
    result = asyncio.run(build_chat_action_proposal(
        gateway, workspace_id="ws-1", engine="content",
        message="Prepare a content topic candidate",
    ))
    assert result["command_id"] == "content.candidate.stage"
    assert result["risk_level"] == "low"
    assert "publish" in gateway.calls[0]["prompt"]


def test_read_only_engines_never_receive_mutation_proposals():
    gateway = FakeGateway(_proposal())
    for engine in ("general", "council", "ai_brain"):
        result = asyncio.run(build_chat_action_proposal(
            gateway, workspace_id="ws-1", engine=engine,
            message="Please run a scan",
        ))
        assert result is None
    assert gateway.calls == []


def test_non_action_turn_does_not_call_structured_proposal_model():
    gateway = FakeGateway(_proposal())
    result = asyncio.run(build_chat_action_proposal(
        gateway, workspace_id="ws-1", engine="guardian",
        message="What does Guardian monitor?",
    ))
    assert result is None
    assert gateway.calls == []
