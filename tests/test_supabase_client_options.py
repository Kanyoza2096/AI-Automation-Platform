"""Regression test for the public Supabase ClientOptions import."""

import sys
from types import ModuleType

from services import supabase as supabase_module


def test_supabase_client_uses_public_client_options_export(monkeypatch):
    seen = {}

    class ClientOptions:
        def __init__(self, **kwargs):
            seen["options"] = kwargs

    def create_client(url, key, options=None):
        seen["client"] = (url, key, options)
        return "client"

    fake_supabase = ModuleType("supabase")
    fake_supabase.ClientOptions = ClientOptions
    fake_supabase.create_client = create_client

    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)
    monkeypatch.setattr(supabase_module, "_build_httpx_client", lambda: "custom-httpx")
    result = supabase_module._create_supabase_client("https://example.test", "key")

    assert result == "client"
    assert seen["options"] == {"httpx_client": "custom-httpx"}
    assert seen["client"][0:2] == ("https://example.test", "key")
    assert isinstance(seen["client"][2], ClientOptions)
