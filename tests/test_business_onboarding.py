import pytest
from services.business_onboarding import _fallback_profile, load_playbook, build_business_plan

def test_creative_agency_description_maps_to_playbook():
    p = _fallback_profile("I run a small visual media agency. We do photography, video editing and social media management. We want more clients, consistent posting and lead follow up.")
    assert p.business_type == "creative_agency"
    assert "acquire_more_clients" in p.goals
    assert "lead_follow_up" in p.goals
    plan = build_business_plan(p, load_playbook(p.business_type))
    assert {w["role"] for w in plan["workers"]} >= {"content_creator", "lead_responder"}
    assert "instagram" in plan["connectors"]

def test_coffee_shop_description_maps_to_playbook():
    p = _fallback_profile("I'm opening a coffee shop in Lilongwe. I need Instagram and a way to bring regulars back.")
    assert p.business_type == "coffee_shop"
    plan = build_business_plan(p, load_playbook(p.business_type))
    assert "customer_retention" in {w["role"] for w in plan["workers"]}

def test_unknown_business_falls_back_safely():
    p = _fallback_profile("I operate a specialized business with unusual requirements.")
    assert p.business_type == "generic"
    assert load_playbook("not-real").business_type == "generic"
