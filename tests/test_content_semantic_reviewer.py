import asyncio
from types import SimpleNamespace

from services.content_semantic_reviewer import ContentSemanticReviewer


def _brand(workspace_id="workspace-test"):
    return SimpleNamespace(
        id="brand-test",
        name="Test Engineering Brand",
        description="Practical software engineering education",
        industry="software engineering",
        target_audience="working developers",
        voice="precise, useful, direct",
        workspace_id=workspace_id,
    )


def _data(**overrides):
    data = {
        "decision": "approve",
        "has_material_factual_claims": True,
        "claim_coverage": 1.0,
        "claims": [{
            "claim": "Kruskal's algorithm uses disjoint sets to detect cycles.",
            "support_status": "supported",
            "source_ids": ["kb-kruskal-1"],
            "rationale": "The retrieved source states this directly.",
        }],
        "brand_relevance": 0.94,
        "originality": 0.82,
        "audience_value": 0.88,
        "genericity": 0.12,
        "rationale": "The post makes a focused, useful engineering point.",
        "improvements": ["Add a concise implementation edge case."],
    }
    data.update(overrides)
    return data


class FakeGateway:
    def __init__(self, data=None, *, ok=True, reason=""):
        self.data = data if data is not None else _data()
        self.ok = ok
        self.reason = reason
        self.calls = []

    async def complete_async_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(
            ok=self.ok,
            data=self.data,
            reason=self.reason,
            request_id="review-request-1",
            provider="fake",
            model="fake-reviewer",
        )


def _evidence():
    return [{
        "source_id": "kb-kruskal-1",
        "text": "Kruskal's algorithm uses disjoint sets to detect cycles when adding edges.",
        "metadata": {"verified": True},
    }]


def _review(gateway, *, workspace_id="workspace-test", evidence=None, caption="Kruskal's algorithm uses disjoint sets to detect cycles.", topic="Kruskal's algorithm"):
    reviewer = ContentSemanticReviewer(gateway=gateway)
    return asyncio.run(reviewer.review(
        caption=caption,
        topic=topic,
        evidence=_evidence() if evidence is None else evidence,
        brand=_brand(workspace_id),
        platform="facebook",
    ))


def test_semantic_reviewer_approves_supported_specific_content():
    gateway = FakeGateway()
    result = _review(gateway)

    assert result["approved"] is True
    assert result["status"] == "approved"
    assert result["supporting_source_ids"] == ["kb-kruskal-1"]
    assert gateway.calls[0][1]["workspace_id"] == "workspace-test"
    assert gateway.calls[0][1]["caller"] == "content"


def test_semantic_reviewer_rejects_unsupported_claim_even_if_model_says_approve():
    gateway = FakeGateway(data=_data(
        claims=[{
            "claim": "The algorithm is 95% faster in production.",
            "support_status": "unsupported",
            "source_ids": [],
            "rationale": "No evidence establishes this performance claim.",
        }],
    ))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "rejected"
    assert "lack evidence support" in result["reason"]


def test_semantic_reviewer_blocks_uncertain_claims_for_human_review():
    gateway = FakeGateway(data=_data(
        decision="needs_review",
        claims=[{
            "claim": "This approach always produces the fastest possible result.",
            "support_status": "uncertain",
            "source_ids": ["kb-kruskal-1"],
            "rationale": "The source does not establish this universal claim.",
        }],
    ))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "needs_review"


def test_semantic_reviewer_rejects_generic_low_value_content():
    gateway = FakeGateway(data=_data(
        brand_relevance=0.88,
        originality=0.18,
        audience_value=0.31,
        genericity=0.93,
        rationale="The caption is a generic textbook summary.",
    ))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "rejected"
    assert "too generic" in result["reason"]


def test_semantic_reviewer_rejects_unknown_source_citations():
    gateway = FakeGateway(data=_data(
        claims=[{
            "claim": "A fact supported by a fabricated source.",
            "support_status": "supported",
            "source_ids": ["kb-not-in-context"],
            "rationale": "The source id was not supplied to the reviewer.",
        }],
    ))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "rejected"
    assert "unknown knowledge source" in result["reason"]


def test_semantic_reviewer_requires_citation_for_supported_claim():
    gateway = FakeGateway(data=_data(
        claims=[{
            "claim": "A claim with no citation.",
            "support_status": "supported",
            "source_ids": [],
            "rationale": "No source was identified.",
        }],
    ))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "needs_review"


def test_semantic_reviewer_rejects_invalid_score_and_malformed_response():
    bad_score = _review(FakeGateway(data=_data(originality=1.5)))
    bad_schema = _review(FakeGateway(data={"decision": "approve"}))

    assert bad_score["approved"] is False
    assert "out-of-range" in bad_score["reason"]
    assert bad_schema["approved"] is False
    assert "schema is invalid" in bad_schema["reason"]


def test_semantic_reviewer_fails_closed_when_gateway_is_unavailable():
    result = _review(FakeGateway(ok=False, reason="provider_unavailable"))

    assert result["approved"] is False
    assert result["status"] == "needs_review"
    assert "no valid structured result" in result["reason"]


def test_semantic_reviewer_refuses_synthetic_workspace_without_calling_gateway():
    gateway = FakeGateway()
    result = _review(gateway, workspace_id="default")

    assert result["approved"] is False
    assert gateway.calls == []


def test_semantic_reviewer_allows_reflective_content_without_factual_claims():
    gateway = FakeGateway(data=_data(
        has_material_factual_claims=False,
        claims=[],
        claim_coverage=1.0,
    ))
    result = _review(
        gateway,
        evidence=[],
        caption="Take a moment to encourage someone today. A little kindness can brighten an ordinary day.",
        topic="Encouragement",
    )

    assert result["approved"] is True
    assert result["supporting_source_ids"] == []
    assert len(gateway.calls) == 1


def test_semantic_reviewer_does_not_approve_factual_claims_without_evidence():
    gateway = FakeGateway(data=_data(
        claims=[{
            "claim": "This intervention reduces costs by 40%.",
            "support_status": "unsupported",
            "source_ids": [],
            "rationale": "No verified evidence was supplied.",
        }],
    ))
    result = _review(gateway, evidence=[])

    assert result["approved"] is False
    assert result["status"] == "rejected"
    assert "lack evidence support" in result["reason"]


def test_semantic_reviewer_rejects_claim_inventory_that_hides_claims():
    gateway = FakeGateway(data=_data(
        has_material_factual_claims=False,
        claims=[{
            "claim": "This intervention reduces costs by 40%.",
            "support_status": "unsupported",
            "source_ids": [],
            "rationale": "No verified evidence was supplied.",
        }],
    ))
    result = _review(gateway, evidence=[])

    assert result["approved"] is False
    assert result["status"] == "rejected"



def test_semantic_reviewer_requires_high_claim_coverage():
    gateway = FakeGateway(data=_data(claim_coverage=0.72))
    result = _review(gateway)

    assert result["approved"] is False
    assert result["status"] == "needs_review"
    assert "did not assess enough" in result["reason"]



def test_semantic_reviewer_receives_brand_model_audience_and_style_fields():
    gateway = FakeGateway()
    brand = SimpleNamespace(
        id="brand-test",
        name="Engineering Notes",
        workspace_id="workspace-test",
        tone="technical and approachable",
        language="en",
        audience_description="Backend engineers maintaining production systems",
        style_category="technical_education",
        custom_style_guide="Use concrete engineering trade-offs and avoid generic filler.",
        metadata={},
    )
    reviewer = ContentSemanticReviewer(gateway=gateway)

    result = asyncio.run(reviewer.review(
        caption="A specific engineering post.",
        topic="Production systems",
        evidence=_evidence(),
        brand=brand,
    ))

    assert result["approved"] is True
    prompt = gateway.calls[0][0]
    assert "Backend engineers maintaining production systems" in prompt
    assert "technical and approachable" in prompt
    assert "avoid generic filler" in prompt
