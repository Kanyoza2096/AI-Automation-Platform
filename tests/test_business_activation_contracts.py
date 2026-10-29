from domain.business_activation.facts import FactType, ExtractionRequest
from services.business_activation_extraction import extract_business_facts


def test_proof_fact_types_remain_supported():
    assert FactType.BUSINESS_HOURS.value in {item.value for item in FactType}
    assert FactType.REFUND_POLICY.value in {item.value for item in FactType}
    assert FactType.BUSINESS_KNOWLEDGE.value in {item.value for item in FactType}


def test_business_hours_extracts_with_source_span_and_threshold():
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="We are open Monday-Friday 9:00-17:00 EAT and Sunday is closed.",
        fact_types_in_scope=[FactType.BUSINESS_HOURS],
    ))
    assert result.proposed_facts
    assert all(f.confidence >= 0.85 for f in result.proposed_facts)
    assert all(f.source_span["text"] for f in result.proposed_facts)
    assert all(f.extraction_id == result.extraction_id for f in result.proposed_facts)


def test_refund_ambiguity_becomes_clarification_not_guess():
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="Refunds are usually fine within two weeks.",
        fact_types_in_scope=[FactType.REFUND_POLICY],
    ))
    assert result.proposed_facts == []
    assert result.clarifications
    assert result.clarifications[0].field == "window_days"


def test_refund_policy_extracts_exact_window_and_condition():
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="Refunds are accepted within 14 days if the item is unused.",
        fact_types_in_scope=[FactType.REFUND_POLICY],
    ))
    assert len(result.proposed_facts) == 1
    fact = result.proposed_facts[0]
    assert fact.value["days"] == 14
    assert fact.value["condition"] == "item_unused"
    assert fact.confirmed is False


def test_same_input_is_idempotent():
    req = ExtractionRequest(
        workspace_id="ws-1",
        operator_text="We are open Monday-Friday 9:00-17:00 EAT.",
        fact_types_in_scope=[FactType.BUSINESS_HOURS],
    )
    a = extract_business_facts(req)
    b = extract_business_facts(req)
    assert a.input_fingerprint == b.input_fingerprint
    assert a.extraction_id == b.extraction_id


def test_review_reextract_is_scoped_to_one_fact_type():
    from domain.business_activation.facts import ReviewReextractRequest
    from services.business_activation_extraction import reextract_review
    result = reextract_review(ReviewReextractRequest(
        workspace_id="ws-1",
        operator_text="Refunds are accepted within 14 days.",
        fact_type=FactType.REFUND_POLICY,
        correction_text="The exact window is 21 days if the item is unused.",
    ))
    assert result.proposed_facts
    assert {fact.fact_type for fact in result.proposed_facts} == {FactType.REFUND_POLICY}
    assert all(f.confirmed is False for f in result.proposed_facts)


def test_review_reextract_never_persists_or_confirms():
    from domain.business_activation.facts import ReviewReextractRequest
    from services.business_activation_extraction import reextract_review
    result = reextract_review(ReviewReextractRequest(
        workspace_id="ws-1",
        operator_text="We are open Monday-Friday 9:00-17:00 EAT.",
        fact_type=FactType.BUSINESS_HOURS,
        correction_text="Actually, we close at 18:00 EAT.",
    ))
    assert all(f.confirmed is False for f in result.proposed_facts)
    assert result.model_used == "deterministic-business-activation-v2-generalized"


def test_generalized_business_activation_preserves_unmodeled_business_knowledge():
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text=("We are a furniture shop in Blantyre. We sell locally made sofas and tables. "
                       "Customers can pay with Airtel Money. We deliver within Blantyre and Zomba. "
                       "Our team can arrange urgent deliveries by phone."),
        fact_types_in_scope=[FactType.BUSINESS_KNOWLEDGE],
    ))
    assert result.proposed_facts
    assert {fact.fact_type for fact in result.proposed_facts} >= {
        FactType.BUSINESS_IDENTITY, FactType.PRODUCT_SERVICE,
        FactType.PAYMENT_METHOD, FactType.DELIVERY_AREA,
    }
    assert all(f.source_span["text"].strip() for f in result.proposed_facts)
    assert all(f.confirmed is False for f in result.proposed_facts)


def test_generalized_mode_does_not_promote_speculation():
    result = extract_business_facts(ExtractionRequest(
        workspace_id="ws-1",
        operator_text="We might start delivering to Lilongwe next month.",
        fact_types_in_scope=[FactType.BUSINESS_KNOWLEDGE],
    ))
    assert result.proposed_facts == []
    assert result.unparsed_text
