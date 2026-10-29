from services.conversational_question_engine import plan_next_question


def test_policy_asks_for_authoritative_source():
    result = plan_next_question(requirements=["refund policy"])
    q = result["questions"][0]
    assert q["question_type"] == "source"
    assert "authoritative source" in q["question"].lower()


def test_service_requirement_becomes_specific_question():
    result = plan_next_question(requirements=["delivery areas"])
    assert "delivery areas" in result["questions"][0]["question"]


def test_answered_requirement_is_skipped():
    result = plan_next_question(requirements=["pricing", "delivery areas"], answered_requirements=["pricing"])
    assert len(result["questions"]) == 1
    assert result["questions"][0]["requirement"] == "delivery areas"


def test_question_is_not_truth():
    result = plan_next_question(requirements=["payment methods"])
    q = result["questions"][0]
    assert q["requires_explicit_confirmation"] is True
    assert q["answer_is_candidate_evidence"] is True
    assert q["creates_knowledge"] is False
    assert result["safety"]["does_not_confirm_knowledge"] is True


def test_multiple_questions_are_bounded():
    result = plan_next_question(requirements=["pricing", "delivery areas", "cancellation rules"], max_questions=2)
    assert len(result["questions"]) == 2
