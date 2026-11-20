def test_code_intelligence_is_an_allowed_gateway_caller_by_default(monkeypatch):
    monkeypatch.delenv("LLM_ALLOWED_CALLERS", raising=False)
    from ai.gateway import _caller_allowed
    assert _caller_allowed("code_intelligence") is True


def test_content_caller_is_allowed_by_scoped_deployment_policy(monkeypatch):
    monkeypatch.setenv("LLM_ALLOWED_CALLERS", "content,director,content_engine")
    from ai.gateway import _caller_allowed
    assert _caller_allowed("content") is True
    assert _caller_allowed("content_review") is False
