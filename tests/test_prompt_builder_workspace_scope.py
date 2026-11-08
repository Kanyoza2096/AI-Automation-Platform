from types import SimpleNamespace

import pytest

from services import knowledge_base
from services.prompt_builder import _get_kb_themes


@pytest.mark.parametrize("workspace_id", ["default", "unbound", "platform", "", None])
def test_synthetic_workspace_scopes_never_query_tenant_knowledge(
    monkeypatch, workspace_id
):
    calls = []

    def fake_get_brand_themes(*, brand_id, workspace_id):
        calls.append((brand_id, workspace_id))
        return {"should-not-load": "synthetic scope"}

    monkeypatch.setattr(knowledge_base, "get_brand_themes", fake_get_brand_themes)
    brand = SimpleNamespace(id="default", workspace_id=workspace_id)

    assert _get_kb_themes(brand) == {}
    assert calls == []


def test_concrete_workspace_still_loads_its_own_brand_themes(monkeypatch):
    calls = []

    def fake_get_brand_themes(*, brand_id, workspace_id):
        calls.append((brand_id, workspace_id))
        return {"recovery": "durable runtime recovery"}

    monkeypatch.setattr(knowledge_base, "get_brand_themes", fake_get_brand_themes)
    brand = SimpleNamespace(id="brand-kdo", workspace_id="kdo-engineering-staging")

    assert _get_kb_themes(brand) == {"recovery": "durable runtime recovery"}
    assert calls == [("brand-kdo", "kdo-engineering-staging")]
