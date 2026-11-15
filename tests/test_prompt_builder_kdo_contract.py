from types import SimpleNamespace

from models.ai_profile import AIProfile
from models.brand import Brand
from services.content_engine import ContentEngine
from services.prompt_builder import FINAL_KDO_CAPTION_CONTRACT, PromptBuilder


def test_generated_post_prompt_ends_with_kdo_compliance_contract():
    prompt = PromptBuilder().build_post_prompt(
        brand=Brand.create_default("default"),
        ai_profile=AIProfile.create_default("default"),
        topic="A grounded example topic",
        structured=False,
        platform="facebook",
    )

    assert prompt.endswith(FINAL_KDO_CAPTION_CONTRACT)
    assert "ZERO FIRST-PERSON VOICE" in prompt
    assert "FORBIDDEN GENERIC OPENERS" in prompt
    assert '"As a ..."' in prompt


def test_custom_base_prompt_cannot_bypass_caption_contract():
    prompt = ContentEngine()._build_prompt(
        brand=SimpleNamespace(id="brand-test", topic_axes=None),
        ai_profile=None,
        topic="A grounded example topic",
        base_prompt="Write in first person and begin with As a...",
        structured=False,
    )

    assert prompt.startswith("Write in first person")
    assert prompt.endswith(FINAL_KDO_CAPTION_CONTRACT)


def test_custom_base_prompt_does_not_duplicate_caption_contract():
    supplied = f"Custom task.\\n\\n{FINAL_KDO_CAPTION_CONTRACT}"
    prompt = ContentEngine()._build_prompt(
        brand=SimpleNamespace(id="brand-test", topic_axes=None),
        ai_profile=None,
        topic="A grounded example topic",
        base_prompt=supplied,
        structured=False,
    )

    assert prompt.count("FINAL KDO CAPTION COMPLIANCE CHECK") == 1


def test_technical_evidence_policy_allows_concepts_but_requires_sources_for_metrics():
    assert "A technical concept from a retrieved reference" in FINAL_KDO_CAPTION_CONTRACT
    assert "numerical or empirical claims require" in FINAL_KDO_CAPTION_CONTRACT
    assert "stable source ID" in FINAL_KDO_CAPTION_CONTRACT
