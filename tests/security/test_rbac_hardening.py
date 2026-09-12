from core.authorization import at_least, normalize_role, role_level


def test_owner_is_highest_human_workspace_role():
    assert role_level("owner") > role_level("admin") > role_level("operator") > role_level("viewer")


def test_owner_inherits_all_human_lower_tiers():
    for minimum in ("viewer", "operator", "admin", "owner"):
        assert at_least("owner", minimum) is True


def test_lower_roles_do_not_inherit_owner():
    for role in ("viewer", "operator", "admin"):
        assert at_least(role, "owner") is False


def test_editor_compatibility_maps_to_operator():
    assert normalize_role("editor") == "operator"
    assert at_least("editor", "operator") is True
