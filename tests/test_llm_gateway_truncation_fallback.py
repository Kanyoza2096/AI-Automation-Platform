"""Regression tests for provider fallback on truncated structured output."""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai.gateway import LLMGateway


class _Adapter:
    def __init__(self, receipt):
        self.receipt = receipt

    def analyze(self, **kwargs):
        return self.receipt

    def close(self):
        pass


def _receipt(provider, model, finish_reason, raw_output='{"ok": true}'):
    return SimpleNamespace(
        status="succeeded",
        provider=provider,
        model=model,
        finish_reason=finish_reason,
        raw_output=raw_output,
        structured_output=None,
        error=None,
    )


class StructuredOutputFallbackTests(unittest.TestCase):
    def _run(self, receipts):
        routes = [
            SimpleNamespace(provider="gemini", model="gemini-2.5-flash"),
            SimpleNamespace(provider="cloudflare_ai", model="@cf/test-model"),
        ]
        route_module = types.ModuleType("services.ai_policy")
        route_module.select_ai_route = lambda task: tuple(routes)
        registry_module = types.ModuleType("services.connectors.ai_registry")
        remaining = iter(receipts)
        registry_module.create_ai_provider_adapter = (
            lambda provider, **kwargs: _Adapter(next(remaining))
        )
        env = {
            "GEMINI_API_KEY": "test-key",
            "CLOUDFLARE_ACCOUNT_ID": "test-account",
            "CLOUDFLARE_API_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env), patch.dict(
            sys.modules,
            {
                "services.ai_policy": route_module,
                "services.connectors.ai_registry": registry_module,
            },
        ):
            return LLMGateway()._call_ai_fabric(
                "return a JSON object",
                temperature=0.0,
                max_tokens=128,
                capability="structured_json",
                workspace_id="test-workspace",
            )

    def test_truncated_primary_falls_through_to_next_provider(self):
        result = self._run([
            _receipt("gemini", "gemini-2.5-flash", "length"),
            _receipt("cloudflare_ai", "@cf/test-model", "stop"),
        ])
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "cloudflare_ai")
        self.assertEqual(result.finish_reason, "stop")

    def test_all_truncated_routes_fail_closed(self):
        result = self._run([
            _receipt("gemini", "gemini-2.5-flash", "length"),
            _receipt("cloudflare_ai", "@cf/test-model", "max_tokens"),
        ])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "structured_output_truncated")

    def test_non_structured_truncation_is_not_reclassified_here(self):
        result = self._run([
            _receipt("gemini", "gemini-2.5-flash", "length"),
        ])
        # Chat/text callers may legitimately accept bounded partial output.
        self.assertTrue(result.ok)
        self.assertEqual(result.finish_reason, "length")


if __name__ == "__main__":
    unittest.main()
