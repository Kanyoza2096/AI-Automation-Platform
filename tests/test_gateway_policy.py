def test_code_intelligence_is_an_allowed_gateway_caller_by_default(monkeypatch):
    monkeypatch.delenv("LLM_ALLOWED_CALLERS", raising=False)
    from ai.gateway import _caller_allowed
    assert _caller_allowed("code_intelligence") is True


def test_content_review_is_an_allowed_gateway_caller_by_default(monkeypatch):
    monkeypatch.delenv("LLM_ALLOWED_CALLERS", raising=False)
    from ai.gateway import _caller_allowed
    assert _caller_allowed("content_review") is True


def test_explicit_gateway_caller_policy_still_controls_content_review(monkeypatch):
    monkeypatch.setenv("LLM_ALLOWED_CALLERS", "content,director")
    from ai.gateway import _caller_allowed
    assert _caller_allowed("content_review") is False
    monkeypatch.setenv("LLM_ALLOWED_CALLERS", "content,director,content_review")
    assert _caller_allowed("content_review") is True
