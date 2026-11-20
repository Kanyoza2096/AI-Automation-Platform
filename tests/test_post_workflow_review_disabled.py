import asyncio

from workflows.post_workflow import ValidateContentStep


class ReviewerMustNotRun:
    async def review(self, **kwargs):
        raise AssertionError("semantic reviewer must remain disabled")


def test_validate_content_step_does_not_invoke_reviewers_or_block():
    step = ValidateContentStep(semantic_reviewer=ReviewerMustNotRun())
    context = {
        "caption": "A post without a verified evidence source.",
        "topic": "Example topic",
        "knowledge_evidence": [],
    }

    result = asyncio.run(step.async_run(context))

    assert result["status"] == "disabled"
    assert result["review_status"] == "disabled"
    assert context["semantic_review"]["status"] == "disabled"
    assert context["content_review_status"] == "disabled"
    assert context["evidence_validation"]["ok"] is None
