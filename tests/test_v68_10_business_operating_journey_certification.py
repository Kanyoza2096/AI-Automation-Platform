from services.v68_business_operating_journey_certification import (
    JOURNEYS,
    JourneyState,
    assert_operating_journey_is_non_executing,
    certify_operating_journeys,
)


def test_representative_business_journeys_are_certified_or_governed():
    results = certify_operating_journeys()
    assert len(results) == len(JOURNEYS)
    assert all(r.state in {JourneyState.CERTIFIED, JourneyState.REVIEW_REQUIRED} for r in results)


def test_sales_lead_journey_has_end_to_end_stages():
    result = next(r for r in certify_operating_journeys() if r.journey_id == "sales.lead-to-followup")
    assert result.state == JourneyState.CERTIFIED
    assert result.stages == ("event_ingress", "workflow_admission", "governed_execution", "business_outcome")


def test_customer_signal_journey_does_not_auto_approve_consequential_edge():
    result = next(r for r in certify_operating_journeys() if r.journey_id == "sales.customer-signal-to-next-action")
    assert result.state == JourneyState.REVIEW_REQUIRED


def test_content_publish_journey_uses_canonical_pipeline():
    result = next(r for r in certify_operating_journeys() if r.journey_id == "content.objective-to-publish-outcome")
    assert result.state == JourneyState.CERTIFIED
    assert "durable_publish" in result.stages
    assert "external_outcome" in result.stages


def test_certification_never_executes():
    result = certify_operating_journeys()[0].to_dict()
    assert_operating_journey_is_non_executing(result)
    try:
        assert_operating_journey_is_non_executing({"execute_now": True})
    except ValueError:
        pass
    else:
        raise AssertionError("execution metadata must be rejected")
