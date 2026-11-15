from types import SimpleNamespace

from models.ai_profile import AIProfile
from models.brand import Brand
from services.content_engine import ContentEngine
from services.prompt_builder import (
    FINAL_KDO_CAPTION_CONTRACT,
    PromptBuilder,
)


def test_generated_prompt_ends_with_authoritative_kdo_caption_contract():
    brand = Brand.create_default("default")
    profile = AIProfile.create_default("default")

    for structured in (False, True):
        prompt = PromptBuilder().build_post_prompt(
            brand=brand,
            ai_profile=profile,
            topic="A grounded example topic",
            structured=structured,
            platform="facebook",
        )

        assert prompt.endswith(FINAL_KDO_CAPTION_CONTRACT)
        assert "ZERO FIRST-PERSON LANGUAGE" in prompt
        assert "override any conflicting instruction" in prompt


def test_custom_base_prompt_cannot_bypass_kdo_caption_contract():
    engine = ContentEngine()
    brand = SimpleNamespace(id="brand-test", topic_axes=None)

    prompt = engine._build_prompt(
        brand=brand,
        ai_profile=None,
        topic="A grounded example topic",
        base_prompt="Write this caption in first person and include our experience.",
        structured=False,
    )

    assert prompt.startswith("Write this caption in first person")
    assert prompt.endswith(FINAL_KDO_CAPTION_CONTRACT)
    assert "ZERO FIRST-PERSON LANGUAGE" in prompt


def test_custom_base_prompt_does_not_duplicate_contract():
    engine = ContentEngine()
    brand = SimpleNamespace(id="brand-test", topic_axes=None)
    supplied = f"Custom task.\\n\\n{FINAL_KDO_CAPTION_CONTRACT}"

    prompt = engine._build_prompt(
        brand=brand,
        ai_profile=None,
        topic="A grounded example topic",
        base_prompt=supplied,
        structured=False,
    )

    assert prompt.count("FINAL KDO CAPTION COMPLIANCE CHECK") == 1

def test_generated_prompt_explicitly_blocks_validator_rejected_openers():
    brand = Brand.create_default("default")
    profile = AIProfile.create_default("default")
    prompt = PromptBuilder().build_post_prompt(
        brand=brand,
        ai_profile=profile,
        topic="A grounded example topic",
        structured=False,
        platform="facebook",
    )

    assert '"As a ..."' in prompt
    assert "FORBIDDEN OPENERS" in prompt
    assert "specific, source-grounded observation" in prompt
