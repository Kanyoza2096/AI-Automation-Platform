from services.business_service_discovery import plan_service_discovery


def test_delivery_business_maps_to_supply_chain():
    result = plan_service_discovery(business_text="We run a courier and delivery business across the city.")
    assert result["candidates"][0]["sector_id"] == "supply_chain"
    assert result["candidates"][0]["candidate_services"]
    assert result["safety"]["does_not_create_knowledge"] is True


def test_ecommerce_business_maps_to_commerce():
    result = plan_service_discovery(business_text="We sell products online, take payments and fulfill customer orders.")
    ids = [x["sector_id"] for x in result["candidates"]]
    assert "commerce" in ids
    commerce = next(x for x in result["candidates"] if x["sector_id"] == "commerce")
    assert {s["capability"] for s in commerce["candidate_services"]} >= {"orders", "payments", "fulfillment"}


def test_healthcare_requirements_are_derived_from_existing_pack():
    result = plan_service_discovery(business_text="We operate a clinic with patients and appointments.")
    healthcare = next(x for x in result["candidates"] if x["sector_id"] == "healthcare")
    assert any("appointment" in req.lower() for s in healthcare["candidate_services"] for req in s["knowledge_requirements"])


def test_explicit_scope_can_include_sector_without_keyword_match():
    result = plan_service_discovery(business_text="We are starting a new operation.", explicit_sector_ids=["agriculture"])
    assert result["candidates"][0]["sector_id"] == "agriculture"
    assert "explicit_scope" in result["candidates"][0]["evidence"]


def test_planner_does_not_execute_or_confirm():
    result = plan_service_discovery(business_text="We provide financial services and invoicing.")
    assert result["read_only"] is True
    assert result["safety"]["does_not_confirm_business_facts"] is True
    assert result["safety"]["does_not_authorize_actions"] is True
    assert result["safety"]["does_not_execute_providers"] is True
