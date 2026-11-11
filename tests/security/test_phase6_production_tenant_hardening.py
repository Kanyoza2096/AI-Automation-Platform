from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.species_auth import SpeciesAuth, validate_species_workspace


def test_species_workspace_selection_requires_existing_active_workspace():
    client = Mock()
    def _eq(field, value):
        q = Mock()
        q.limit.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": value, "status": "active"}] if value == "ws-1" else []
        )
        return q
    client.table.return_value.select.return_value.eq.side_effect = _eq
    assert validate_species_workspace("ws-1", client) is True
    assert validate_species_workspace("ws-2", client) is False


def test_species_workspace_selection_rejects_suspended_workspace():
    client = Mock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(
        data=[{"id": "ws-1", "status": "suspended"}]
    )
    assert validate_species_workspace("ws-1", client) is False


def test_species_workspace_selection_fails_closed_without_database():
    assert validate_species_workspace("ws-1", None) is False


def test_workspace_token_without_workspace_scope_is_rejected(monkeypatch):
    auth = SpeciesAuth()
    manager = Mock()
    manager.validate_token.return_value = {"name": "bad-token", "permissions": ["read"]}
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    monkeypatch.delenv("ALLOW_LEGACY_API_KEYS", raising=False)
    identity = auth.validate(bearer_token="kny_test")
    assert identity.valid is False
    assert "workspace scope" in identity.error


def test_workspace_token_with_workspace_scope_is_accepted(monkeypatch):
    auth = SpeciesAuth()
    manager = Mock()
    manager.validate_token.return_value = {
        "workspace_id": "ws-1",
        "name": "good-token",
        "permissions": ["read"],
    }
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    identity = auth.validate(bearer_token="kny_test")
    assert identity.valid is True
    assert identity.workspace_id == "ws-1"


def test_kdo_prefixed_workspace_token_is_accepted(monkeypatch):
    auth = SpeciesAuth()
    manager = Mock()
    manager.validate_token.return_value = {
        "workspace_id": "ws-kdo",
        "name": "kdo-token",
        "permissions": ["read"],
    }
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    identity = auth.validate(bearer_token="KDO_API_test")
    assert identity.valid is True
    assert identity.workspace_id == "ws-kdo"


def test_expired_legacy_key_is_rejected(monkeypatch):
    from datetime import datetime, timezone, timedelta
    auth = SpeciesAuth()
    manager = Mock()
    manager.validate_token.return_value = None
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    monkeypatch.setenv("ALLOW_LEGACY_API_KEYS", "1")
    key_store = SimpleNamespace(find_by_hash_sync=lambda _: {
        "workspace_id": "ws-1",
        "permissions": ["read"],
        "revoked": False,
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    })
    identity = auth.validate(bearer_token="kny_test", key_store=key_store)
    assert identity.valid is False
    assert identity.error == "API key expired"


def test_unknown_workspace_token_fails_closed_when_supabase_client_unavailable(monkeypatch):
    auth = SpeciesAuth()
    manager = Mock()
    manager.validate_token.return_value = None
    monkeypatch.setattr("core.api_tokens.api_token_manager", manager)
    monkeypatch.setattr("services.supabase.supabase_service", SimpleNamespace(client=None))
    monkeypatch.delenv("ALLOW_LEGACY_API_KEYS", raising=False)

    identity = auth.validate(bearer_token="kny_unknown")

    assert identity.valid is False
    assert identity.error == "Legacy api_keys disabled — use api_tokens (kny_)"
