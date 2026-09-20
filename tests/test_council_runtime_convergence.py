from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_director_uses_canonical_council_integration():
    source = (ROOT / "services" / "director.py").read_text()
    assert "from council.integration import get_council" in source
    assert "from plugins.council.personas import get_council" not in source


def test_condition_engine_uses_canonical_council_integration():
    source = (ROOT / "services" / "condition_engine.py").read_text()
    assert "from council.integration import get_council" in source
    assert "from plugins.council.personas import get_council" not in source


def test_canonical_council_has_all_twelve_active_personas():
    from council.personas import ALL_PERSONAS

    names = [cls.__name__ for cls in ALL_PERSONAS]
    assert len(names) == 12
    assert "AffiliateMarketer" in names
    assert "Theologian" in names


def test_legacy_plugin_is_not_runtime_imported_by_production_services():
    production = [
        ROOT / "services" / "director.py",
        ROOT / "services" / "condition_engine.py",
    ]
    for path in production:
        assert "plugins.council" not in path.read_text()
