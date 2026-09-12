from core.plan_authority import (
    AuthorityLevel,
    authority_for_plan,
    can_auto_execute,
    director_decision_for_plan,
)


def test_free_is_suggest():
    assert authority_for_plan("free") == AuthorityLevel.SUGGEST
    assert director_decision_for_plan("free") == "SUGGEST"
    assert can_auto_execute("free") is False


def test_enterprise_auto_high():
    assert authority_for_plan("enterprise") == AuthorityLevel.AUTO_HIGH
    assert can_auto_execute("enterprise") is True


def test_species_always_auto():
    assert can_auto_execute("free", species=True) is True
    assert director_decision_for_plan("free", species=True) == "ALLOW"
