"""Phase 5 chaos-monkey regression tests: attack the trust boundaries, not the happy path."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_species_auth_never_defaults_unbound_jwt_to_tenant(monkeypatch):
    from core.species_auth import SpeciesAuth

    class FakeQuery:
        def select(self, *_): return self
        def eq(self, *_): return self
        def limit(self, *_): return self
        def execute(self): return SimpleNamespace(data=[])

    class FakeClient:
        def table(self, *_): return FakeQuery()

    class User:
        email = "orphan@example.test"
        id = "user-orphan"

    class Auth:
        def get_user(self, token): return SimpleNamespace(user=User())

    client = FakeClient()
    client.auth = Auth()
    identity = SpeciesAuth().validate(bearer_token="eyJorphan", supabase_client=client)
    assert not identity.valid
    assert identity.workspace_id is None
    assert identity.method == ""


def test_v1_has_no_query_string_api_token_fallback():
    source = Path("api/v1.py").read_text()
    assert 'request.query_params.get("token"' not in source
    assert 'request.query_params.get("secret"' not in source


def test_webhook_missing_secret_fails_closed(monkeypatch):
    from api import v1

    class Settings:
        webhook_secret = ""

    monkeypatch.setattr(v1, "settings", Settings(), raising=False)

    class Headers:
        def get(self, *_): return ""

    class Request:
        headers = Headers()
        query_params = {}
        async def body(self): return b"{}"

    # Function imports settings internally, so patch its config module.
    import config.settings as cs
    monkeypatch.setattr(cs, "settings", Settings(), raising=False)
    assert asyncio_run(v1._verify_webhook_signature(Request())) is False


def asyncio_run(awaitable):
    import asyncio
    return asyncio.run(awaitable)
