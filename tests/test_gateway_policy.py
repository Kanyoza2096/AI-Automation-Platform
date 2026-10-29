def test_code_intelligence_is_an_allowed_gateway_caller_by_default(monkeypatch):
    monkeypatch.delenv("LLM_ALLOWED_CALLERS", raising=False)
    from ai.gateway import _caller_allowed
    assert _caller_allowed("code_intelligence") is True
