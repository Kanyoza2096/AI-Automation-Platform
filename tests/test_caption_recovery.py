from types import SimpleNamespace

from workflows.post_workflow import GenerateContentStep


def test_caption_quality_failure_is_detected_from_top_level_engine_result():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "caption_quality: too short: 5 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) == result["error"]


def test_caption_quality_failure_is_detected_from_media_artifact():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "media_artifact": SimpleNamespace(
            error_code="CONTENT_PIPELINE_FAILED",
            error="caption_quality: too short: 5 words (min 80)",
        ),
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) == (
        "caption_quality: too short: 5 words (min 80)"
    )


def test_non_caption_pipeline_failure_is_not_repaired():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "image renderer unavailable",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None


def test_empty_caption_quality_failure_is_not_repaired():
    result = {
        "caption": "  ",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "caption_quality: too short: 0 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None


def test_unrelated_error_code_is_not_repaired_even_with_caption_quality_text():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "PROVIDER_UNAVAILABLE",
        "error": "caption_quality: too short: 5 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None


def test_generate_content_step_repairs_real_media_artifact_caption_failure(monkeypatch):
    import asyncio
    import services.content_evidence as evidence
    import services.knowledge_base as knowledge_base
    from workflows.post_workflow import GenerateContentStep

    monkeypatch.setattr(
        knowledge_base, "aget_brand_themes",
        lambda *_args, **_kwargs: _async_none(),
    )
    monkeypatch.setattr(
        evidence, "validate_editorial_specificity",
        lambda _caption: (True, "ok"),
    )
    monkeypatch.setattr(
        evidence, "validate_caption_evidence",
        lambda _caption, _sources: (True, "ok", ["growthbridge-service-1"]),
    )

    class PromptBuilder:
        last_used_axis = None
        def build_post_prompt(self, **_kwargs):
            return "Grounded GrowthBridge prompt with verified service evidence."

    class Engine:
        def __init__(self):
            self.calls = 0
        async def generate_post(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                artifact = SimpleNamespace(
                    error_code="CONTENT_PIPELINE_FAILED",
                    error="caption_quality: too short: 65 words (min 80)",
                )
                return {
                    "caption": "A short but otherwise valid generated draft.",
                    "publishable": False,
                    "degraded": True,
                    "error_code": "CONTENT_PIPELINE_FAILED",
                    "error": artifact.error,
                    "media_artifact": artifact,
                }
            assert "BOUNDED CAPTION COMPLIANCE REPAIR" in kwargs["base_prompt"]
            caption = (
                "GrowthBridge Virtual helps organisations address practical "
                "business and digital needs through services documented in its "
                "verified service information. A clear service offer helps a "
                "business understand what support is available, why it matters, "
                "and how to begin a conversation about its priorities. The right "
                "engagement starts with the organisation's actual needs, its "
                "customers, and the outcomes it is seeking to achieve. GrowthBridge "
                "connects relevant expertise and opportunities across its "
                "business ecosystem, while keeping each proposed service grounded "
                "in the information published by the organisation. Businesses "
                "looking for relevant support can review the available services "
                "and make an enquiry to discuss fit, scope, and next steps. Which "
                "business or digital challenge would be most useful to discuss?"
            )
            return {
                "caption": caption,
                "publishable": True,
                "degraded": False,
                "image_bytes": b"image",
                "media_artifact": SimpleNamespace(
                    status=SimpleNamespace(value="media_ready"),
                    is_publishable=True,
                    is_valid=True,
                    is_text_only=False,
                    is_pending=False,
                    has_failed=False,
                    source="test",
                    size_kb=1,
                    bytes=b"image",
                ),
            }
        def _validate_caption_quality(self, _caption):
            return True, "ok"

    async def _async_none():
        return {}

    engine = Engine()
    step = GenerateContentStep(
        content_engine=engine,
        brand=SimpleNamespace(
            id="growthbridge-test",
            name="GrowthBridge Virtual",
            workspace_id="workspace-test",
            platform="facebook",
        ),
        ai_profile=object(),
        prompt_builder=PromptBuilder(),
    )
    context = {
        "topic": "business consulting and operational improvement",
        "knowledge_context": "Verified GrowthBridge service information.",
        "knowledge_evidence": [{
            "source_id": "growthbridge-service-1",
            "text": "Verified GrowthBridge service information.",
            "metadata": {},
        }],
        "scheduled_caption": None,
        "platform": "facebook",
    }

    result = asyncio.run(step.async_run(context))
    assert engine.calls == 2
    assert result["publishable"] is True
    assert len(context["caption"].split()) >= 80
