from pathlib import Path


def test_business_onboarding_route_is_mounted():
    main = Path(__file__).parents[1] / 'main.py'
    text = main.read_text(encoding='utf-8')
    assert 'routes.business_onboarding_routes' in text


def test_onboarding_route_is_non_mutating_contract_surface():
    route = Path(__file__).parents[1] / 'routes' / 'business_onboarding_routes.py'
    text = route.read_text(encoding='utf-8')
    assert '@router.post("/understand"' in text
    assert '@router.post("/plan"' in text
    assert '@router.post("/provision"' in text
