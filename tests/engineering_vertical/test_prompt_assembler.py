import pytest

from services.kdo_engineering_prompt_assembler import (
    EngineeringPromptAssembler, EngineeringTaskAssignment, PromptAssemblyError,
)


def task(**overrides):
    values = dict(
        task_id="auth-api-1", role="implementer", objective="Implement login endpoint",
        acceptance_criteria=("Match the API schema", "Tests pass"),
        depends_on=("auth-threat-model-1",), deliverables=("patch", "tests"),
        context={"repo_conventions": "Use existing service patterns"},
        policy_refs=("backend-policy-v1",),
    )
    values.update(overrides)
    return EngineeringTaskAssignment(**values)


def test_prompt_is_dynamic_by_task_but_role_template_is_stable():
    assembler = EngineeringPromptAssembler()
    first_prompt, first_manifest = assembler.assemble(task())
    second_prompt, second_manifest = assembler.assemble(task(objective="Implement logout endpoint", task_id="auth-api-2"))
    assert "Implement login endpoint" in first_prompt
    assert "Implement logout endpoint" in second_prompt
    assert "ROLE TEMPLATE (implementer v1)" in first_prompt
    assert first_manifest["role_template_version"] == second_manifest["role_template_version"]
    assert first_manifest["assembled_prompt_sha256"] != second_manifest["assembled_prompt_sha256"]


def test_same_inputs_produce_same_prompt_and_manifest():
    assembler = EngineeringPromptAssembler()
    prompt1, manifest1 = assembler.assemble(task())
    prompt2, manifest2 = assembler.assemble(task())
    assert prompt1 == prompt2
    assert manifest1 == manifest2


def test_unknown_role_and_missing_acceptance_criteria_are_rejected():
    assembler = EngineeringPromptAssembler()
    with pytest.raises(PromptAssemblyError, match="unknown_role"):
        assembler.assemble(task(role="root_admin"))
    with pytest.raises(PromptAssemblyError, match="acceptance_criteria_required"):
        assembler.assemble(task(acceptance_criteria=()))


def test_duplicate_and_self_dependencies_are_rejected():
    assembler = EngineeringPromptAssembler()
    with pytest.raises(PromptAssemblyError, match="invalid_dependencies"):
        assembler.assemble(task(depends_on=("auth-api-1",)))


def test_context_limit_is_enforced():
    assembler = EngineeringPromptAssembler(context_limit_chars=256)
    with pytest.raises(PromptAssemblyError, match="context_exceeds_limit"):
        assembler.assemble(task(context={"large": "x" * 1000}))


def test_manifest_tracks_policy_and_context_versions():
    assembler = EngineeringPromptAssembler()
    _, manifest = assembler.assemble(task(), policy_version="security-policy-7")
    assert manifest["policy_version"] == "security-policy-7"
    assert len(manifest["task_contract_sha256"]) == 64
    assert len(manifest["manifest_sha256"]) == 64



def test_every_registered_specialist_has_a_stable_prompt_template():
    from services.kdo_engineering_agent_registry import ROLE_BY_ID
    from services.kdo_engineering_prompt_assembler import DEFAULT_ROLE_TEMPLATES

    missing = sorted(set(ROLE_BY_ID) - set(DEFAULT_ROLE_TEMPLATES))
    assert missing == []
    for role_id in ROLE_BY_ID:
        template = DEFAULT_ROLE_TEMPLATES[role_id]
        assert template.role == role_id
        assert template.version
        assert template.instructions.strip()
        assert "Do not grant or expand permissions." in template.prohibited_actions


def test_specialist_roles_assemble_task_specific_prompts():
    assembler = EngineeringPromptAssembler()
    for role_id in ("frontend_engineer", "reliability_engineer", "context_engineer",
                    "agent_evaluation_engineer", "authorization_auditor"):
        prompt, manifest = assembler.assemble(task(role=role_id, objective="Inspect this task"))
        assert f"ROLE TEMPLATE ({role_id} v1)" in prompt
        assert "Inspect this task" in prompt
        assert manifest["role"] == role_id



def test_requested_capabilities_are_explicitly_non_authoritative_task_requirements():
    assembler = EngineeringPromptAssembler()
    assignment = task(requested_capabilities=("read_task_workspace", "propose_patch"))
    prompt, manifest = assembler.assemble(assignment)
    assert '"requested_capabilities":["read_task_workspace","propose_patch"]' in prompt
    assert "NOT permissions or authorization" in prompt
    assert len(manifest["task_contract_sha256"]) == 64


def test_duplicate_or_blank_requested_capabilities_are_rejected():
    assembler = EngineeringPromptAssembler()
    with pytest.raises(PromptAssemblyError, match="invalid_requested_capabilities"):
        assembler.assemble(task(requested_capabilities=("propose_patch", "propose_patch")))
    with pytest.raises(PromptAssemblyError, match="invalid_requested_capabilities"):
        assembler.assemble(task(requested_capabilities=(" ",)))
