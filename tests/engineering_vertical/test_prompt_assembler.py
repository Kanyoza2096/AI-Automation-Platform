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
