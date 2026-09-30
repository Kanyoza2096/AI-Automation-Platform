import importlib
import warnings


def test_root_council_has_exactly_12_personas():
    from council.personas import ALL_PERSONAS
    assert len(ALL_PERSONAS) == 12
    assert {cls.__module__ for cls in ALL_PERSONAS} == {m.__module__ for m in ALL_PERSONAS}
    assert {cls.__name__ for cls in ALL_PERSONAS} >= {"AffiliateMarketer", "Theologian"}


def test_organism_does_not_shrink_canonical_council_to_workspace_persona_subset():
    from core.organism import Organism, OrganismConfig
    cfg = OrganismConfig(workspace_id="ws-test", plan="free", enabled_personas=["youth_advocate"])
    org = Organism(cfg)
    council = org._build_council()
    assert council is not None
    assert council.__class__.__module__ == "council.engine"
    assert len(council._personas) == 12


def test_plugin_council_is_only_a_compatibility_facade():
    legacy = importlib.import_module("plugins.council.personas")
    canonical = importlib.import_module("council.integration").get_council()
    assert legacy.get_council() is canonical
    assert len(canonical.members) == 12
    assert legacy.CommunityCouncil() is canonical


def test_business_automation_engine_is_registered_in_canonical_container():
    from dependency_registry import build_container
    from services.business_automation import BusinessAutomationEngine
    container = build_container()
    assert container.is_registered(BusinessAutomationEngine)
    engine = container.resolve(BusinessAutomationEngine)
    assert isinstance(engine, BusinessAutomationEngine)


def test_production_event_wiring_can_resolve_registered_business_engine():
    from dependency_registry import build_container
    from services.business_automation import BusinessAutomationEngine
    from services.v71_14_production_route_event_wiring import ProductionRouteEventWiring

    class Runtime: pass
    container = build_container()
    wiring = ProductionRouteEventWiring(container=container, runtime=Runtime())
    assert isinstance(wiring._engine(), BusinessAutomationEngine)


def test_no_import_warning_from_root_council():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        importlib.import_module("council")
        importlib.import_module("council.engine")
