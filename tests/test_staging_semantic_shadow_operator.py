from types import SimpleNamespace

import pytest

from models.brand import Brand
from scripts import run_staging_semantic_shadow_eval as operator


class FakeBrandRepository:
    def __init__(self, brands):
        self.brands = brands
        self.workspace_queries = []

    def find_by_workspace(self, workspace_id):
        self.workspace_queries.append(workspace_id)
        return self.brands

    def find_by_id(self, _brand_id):
        raise AssertionError("operator must not use an unscoped brand lookup")


def _brand(brand_id=operator.STAGING_BRAND_ID, workspace_id=operator.STAGING_WORKSPACE_ID):
    return Brand(
        id=brand_id,
        workspace_id=workspace_id,
        name="KDO Semantic Evaluation Lab",
        slug="kdo-semantic-evaluation-lab",
    )


def test_operator_loads_brand_only_from_canonical_workspace():
    repository = FakeBrandRepository([
        _brand(brand_id="other-brand"),
        _brand(),
    ])

    loaded = operator._load_staging_brand(repository)

    assert loaded.id == operator.STAGING_BRAND_ID
    assert repository.workspace_queries == [operator.STAGING_WORKSPACE_ID]


def test_operator_refuses_brand_outside_canonical_workspace():
    repository = FakeBrandRepository([
        _brand(workspace_id="ws-e6eb6528d5b9"),
    ])

    with pytest.raises(RuntimeError, match="canonical staging workspace"):
        operator._load_staging_brand(repository)

    assert repository.workspace_queries == [operator.STAGING_WORKSPACE_ID]


def test_operator_requires_staging_environment_and_explicit_flag(monkeypatch):
    monkeypatch.setattr(operator.settings, "environment", "staging")
    monkeypatch.setattr(
        operator.settings, "supabase_url",
        "https://kkzshurcmcptleyqjgzo.supabase.co",
    )
    monkeypatch.setattr(operator.settings, "supabase_service_role_key", "staging-test-key")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("KDO_ENVIRONMENT", raising=False)
    monkeypatch.delenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", raising=False)

    with pytest.raises(RuntimeError, match="semantic shadow flag is disabled"):
        operator._assert_operator_boundary()


def test_operator_refuses_production_database_even_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(operator.settings, "environment", "staging")
    monkeypatch.setattr(
        operator.settings, "supabase_url",
        "https://kfgutijhrywnxpsjjacd.supabase.co",
    )
    monkeypatch.setattr(operator.settings, "supabase_service_role_key", "staging-test-key")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "true")

    with pytest.raises(RuntimeError, match="not KDO staging"):
        operator._assert_operator_boundary()
