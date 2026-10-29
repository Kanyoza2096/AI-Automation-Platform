from services.vnext_sector_platform_read_model import build_sector_platform_read_model


def test_sector_platform_has_canonical_thirteen_pack_portfolio():
    model = build_sector_platform_read_model()
    assert model["schema"] == "KDO.SectorPlatformReadModel/v1"
    assert model["count"] == 13
    assert len(model["packs"]) == 13
    assert len({p["pack_id"] for p in model["packs"]}) == 13


def test_sector_packs_do_not_gain_execution_authority():
    model = build_sector_platform_read_model()
    for pack in model["packs"]:
        assert pack["authority"]["sector_pack_execution"] == "NONE"
        for action in pack["external_actions"]:
            assert action["execution_binding"] in {"ACTION_KERNEL", "NOT_BOUND"}


def test_missing_action_bindings_are_explicit_not_inferred():
    model = build_sector_platform_read_model()
    summary = model["action_binding_summary"]
    assert summary["declared_external_actions"] >= summary["bound_to_action_kernel"]
    assert summary["not_bound"] >= 0
    assert model["authority"]["missing_action_bindings_remain_explicit"] is True


def test_publish_content_uses_canonical_action_kernel_contract():
    model = build_sector_platform_read_model()
    marketing = next(p for p in model["packs"] if p["pack_id"] == "marketing_media")
    publish = next(a for a in marketing["external_actions"] if a["action"] == "publish_content")
    assert publish["execution_binding"] == "ACTION_KERNEL"
    assert publish["contract"]["risk_level"] == "high"
    assert publish["contract"]["requires_approval"] is True


def test_it_deployment_uses_code_backed_action_kernel_contract():
    model = build_sector_platform_read_model()
    it = next(p for p in model["packs"] if p["pack_id"] == "it_operations")
    action = next(a for a in it["external_actions"] if a["action"] == "it.request_deployment")
    assert action["execution_binding"] == "ACTION_KERNEL"
    assert action["contract"]["risk_level"] == "critical"
    assert action["contract"]["requires_approval"] is True


def test_finance_payment_uses_code_backed_action_kernel_contract():
    model = build_sector_platform_read_model()
    finance = next(p for p in model["packs"] if p["pack_id"] == "finance")
    action = next(a for a in finance["external_actions"] if a["action"] == "finance.initiate_payment")
    assert action["execution_binding"] == "ACTION_KERNEL"
    assert action["contract"]["risk_level"] == "critical"
    assert action["contract"]["requires_approval"] is True
