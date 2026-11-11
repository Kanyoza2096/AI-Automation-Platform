from ai.gateway import IntelligenceMode, IntelligenceResult, LLMGateway


def test_complete_structured_prefers_provider_parsed_data():
    gateway = LLMGateway()
    expected = {"summary": "Evidence-first plan", "steps": [{"step_id": "inspect"}]}
    gateway.complete = lambda *args, **kwargs: IntelligenceResult(
        ok=True,
        mode=IntelligenceMode.FULL,
        text="not-json-but-provider-returned-validated-structured-data",
        data=expected,
        provider="gemini",
        finish_reason="stop",
    )

    result = gateway.complete_structured(
        "Create a plan",
        schema_hint='{"type":"object"}',
        required_keys=["summary", "steps"],
        caller="director",
    )

    assert result.ok is True
    assert result.data == expected
    assert result.provider == "gemini"


def test_complete_structured_rejects_provider_data_missing_required_keys():
    gateway = LLMGateway()
    gateway.complete = lambda *args, **kwargs: IntelligenceResult(
        ok=True,
        mode=IntelligenceMode.FULL,
        text='{"summary":"Only a summary"}',
        data={"summary": "Only a summary"},
        provider="gemini",
        finish_reason="stop",
    )

    result = gateway.complete_structured(
        "Create a plan",
        schema_hint='{"type":"object"}',
        required_keys=["summary", "steps"],
        caller="director",
    )

    assert result.ok is False
    assert result.reason == "missing_keys:steps"
    assert result.data == {"summary": "Only a summary"}


def test_complete_structured_keeps_raw_text_fallback_for_legacy_providers():
    gateway = LLMGateway()
    expected = {"summary": "Evidence-first plan", "steps": []}
    gateway.complete = lambda *args, **kwargs: IntelligenceResult(
        ok=True,
        mode=IntelligenceMode.FULL,
        text='{"summary":"Evidence-first plan","steps":[]}',
        data=None,
        provider="legacy",
        finish_reason="stop",
    )

    result = gateway.complete_structured(
        "Create a plan",
        schema_hint='{"type":"object"}',
        required_keys=["summary", "steps"],
        caller="director",
    )

    assert result.ok is True
    assert result.data == expected
