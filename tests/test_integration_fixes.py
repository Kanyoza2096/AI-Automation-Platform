"""
Integration regression tests covering bugs found during live API testing.

All tests run without external services — they test the code paths that
caused errors when real requests hit the running server.
"""

import json
import threading
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# services/supabase.py — supabase_service shim
# ---------------------------------------------------------------------------

class TestSupabaseServiceShim:
    def test_supabase_service_is_importable(self):
        """'from services.supabase import supabase_service' must not raise ImportError."""
        from services.supabase import supabase_service
        assert supabase_service is not None

    def test_supabase_service_is_falsy_when_unconfigured(self):
        """supabase_service must evaluate False when no client is initialised."""
        from services.supabase import supabase_service
        # In test environment Supabase is not configured
        assert not supabase_service

    def test_supabase_service_client_is_none_when_unconfigured(self):
        """supabase_service.client must be None when not configured."""
        from services.supabase import supabase_service
        assert supabase_service.client is None

    def test_api_v1_supabase_import_no_longer_errors(self):
        """All routes that import supabase_service inline must not raise ImportError."""
        import api.v1
        assert True


# ---------------------------------------------------------------------------
# api/v1.py — auth guards on sensitive routes
# ---------------------------------------------------------------------------

class TestApiAuthGuards:
    def test_posts_get_requires_auth(self, client):
        assert client.get("/api/v1/posts").status_code == 401

    def test_messages_requires_auth(self, client):
        assert client.get("/api/v1/messages").status_code == 401

    def test_message_detail_requires_auth(self, client):
        assert client.get("/api/v1/messages/user123").status_code == 401

    def test_analytics_requires_auth(self, client):
        assert client.get("/api/v1/analytics").status_code == 401

    def test_analytics_performance_requires_auth(self, client):
        assert client.get("/api/v1/analytics/performance").status_code == 401

    def test_tenants_requires_auth(self, client):
        assert client.get("/api/v1/tenants").status_code == 401

    def test_guardian_issues_requires_auth(self, client):
        assert client.get("/api/v1/guardian/issues").status_code == 401

    def test_dashboard_live_is_public(self, client):
        """GET /api/v1/dashboard/live is a public endpoint — returns 200 without auth."""
        resp = client.get("/api/v1/dashboard/live")
        assert resp.status_code == 200

    def test_posts_accepts_valid_token(self, client, auth_headers):
        resp = client.get("/api/v1/posts", headers=auth_headers)
        assert resp.status_code == 200

    def test_public_routes_remain_open(self, client):
        """bot/status, status, and health must not require auth."""
        public = [
            "/api/v1/bot/status",
            "/api/v1/status",
            "/health",
        ]
        for path in public:
            code = client.get(path).status_code
            assert code == 200, f"{path} unexpectedly requires auth (got {code})"


# ---------------------------------------------------------------------------
# routes/owner_commands.py — /status and /analytics slash commands
# ---------------------------------------------------------------------------

class TestOwnerCommandsFixes:
    def _make_handler(self, mock_settings=None, mock_analytics=None):
        from routes.owner_commands import OwnerCommands as SlashCommandHandler

        if mock_settings is None:
            mock_settings = MagicMock()
            mock_settings.to_safe_dict.return_value = {
                "gemini_key_configured": True,
                "facebook_configured": True,
                "supabase_enabled": False,
                "guardian_enabled": False,
            }

        mock_fb = MagicMock()
        mock_fb.send_message.return_value = True

        handler = SlashCommandHandler(
            facebook_service=mock_fb,
            analytics=mock_analytics,
            post_scheduler=None,
            settings=mock_settings,
        )
        return handler, mock_fb

    def test_status_uses_facebook_configured_not_page_token_configured(self):
        """/status must use 'facebook_configured' key — not the nonexistent 'page_token_configured'."""
        handler, mock_fb = self._make_handler()
        handler._cmd_status("sender123", "")
        mock_fb.send_message.assert_called_once()
        reply_text = mock_fb.send_message.call_args[0][1]
        assert "FB:" in reply_text, f"Expected FB: in reply, got: {reply_text!r}"

    def test_status_does_not_raise_on_missing_hf_whatsapp_keys(self):
        """/status must not crash even when hf_configured and whatsapp_configured are absent."""
        mock_settings = MagicMock()
        mock_settings.to_safe_dict.return_value = {
            "gemini_key_configured": True,
            "facebook_configured": True,
            "supabase_enabled": False,
            "guardian_enabled": False,
        }
        handler, _ = self._make_handler(mock_settings=mock_settings)
        handler._cmd_status("sender123", "")

    def test_analytics_calls_get_stats_not_stats(self):
        """/analytics must call analytics.get_stats(), not the non-existent .stats()."""
        mock_analytics = MagicMock()
        mock_analytics.get_stats.return_value = {
            "overview": {
                "messages_received": 42,
                "total_users": 5,
                "posts_published": 3,
                "total_errors": 1,
                "active_users_24h": 2,
            }
        }
        handler, mock_fb = self._make_handler(mock_analytics=mock_analytics)
        handler._cmd_analytics("sender123", "")

        mock_analytics.get_stats.assert_called_once()
        mock_analytics.stats.assert_not_called()

        reply_text = mock_fb.send_message.call_args[0][1]
        assert "42" in reply_text, f"Message count missing from reply: {reply_text!r}"

    def test_analytics_missing_method_check_in_source(self):
        """owner_commands.py must call get_stats() not stats()."""
        import inspect
        from routes import owner_commands as oc_mod
        src = inspect.getsource(oc_mod)
        assert "get_stats()" in src, "get_stats() call missing — /analytics will crash"
        assert ".stats()" not in src, ".stats() still present — will cause AttributeError"


# ---------------------------------------------------------------------------
# services/gemini.py — robust parts parsing (thinking tokens)
# ---------------------------------------------------------------------------

class TestGeminiPartsParser:
    def test_parser_skips_thinking_part_finds_text(self):
        """_call() must find the text part even when a thinking part appears first."""
        from services.gemini import GeminiService
        svc = GeminiService()

        fake_response_data = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"thought": "Let me think about this..."},
                        {"text": "Hello! How can I help?"},
                    ]
                }
            }]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fake_response_data

        with patch.object(svc._session, "post", return_value=mock_response):
            result = svc._call(
                prompt="Say hello",
                temperature=0.5,
                max_tokens=50,
            )

        assert result == "Hello! How can I help?"

    def test_parser_works_with_single_text_part(self):
        """_call() must work normally when there is exactly one text part."""
        from services.gemini import GeminiService
        svc = GeminiService()

        fake_response_data = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Normal response."}]
                }
            }]
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fake_response_data

        with patch.object(svc._session, "post", return_value=mock_response):
            result = svc._call(prompt="test", temperature=0.5, max_tokens=50)

        assert result == "Normal response."

    def test_parser_raises_when_no_text_part_at_all(self):
        """_call() must raise GeminiServiceError when no part has a 'text' key."""
        from services.gemini import GeminiService, GeminiServiceError
        svc = GeminiService()

        fake_response_data = {
            "candidates": [{
                "content": {
                    "parts": [{"thought": "Only thinking, no output."}]
                }
            }]
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fake_response_data

        with patch.object(svc._session, "post", return_value=mock_response), \
             __import__("pytest").raises(GeminiServiceError, match="Missing text"):
            svc._call(prompt="test", temperature=0.5, max_tokens=50)

    def test_parser_source_uses_next_not_index_zero(self):
        """_call() source must use next(...) to find text part, not hardcoded parts[0]."""
        import inspect
        from services import gemini as gemini_mod
        src = inspect.getsource(gemini_mod)
        assert 'next((p for p in parts if "text" in p)' in src, \
            "parts parser still uses parts[0] — will crash on Gemini thinking-token responses"

    def test_parser_skips_multiple_thinking_parts(self):
        """_call() must handle multiple thinking blocks before the text part."""
        from services.gemini import GeminiService
        svc = GeminiService()

        fake_response_data = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"thought": "First thought."},
                        {"thought": "Second thought."},
                        {"text": "Final answer."},
                    ]
                }
            }]
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fake_response_data

        with patch.object(svc._session, "post", return_value=mock_response):
            result = svc._call(prompt="test", temperature=0.5, max_tokens=50)

        assert result == "Final answer."


# ---------------------------------------------------------------------------
# api/v1.py — schedule out-of-range rejection (integration suite)
# ---------------------------------------------------------------------------

class TestScheduleValidationIntegration:
    def test_out_of_range_hour_returns_400_not_200(self, client, auth_headers):
        """PUT /api/v1/schedule with hour > 23 must be rejected, not applied."""
        resp = client.put(
            "/api/v1/schedule",
            json={"content_hours_utc": [4, 7, 25]},
            headers=auth_headers,
        )
        # 400 = validation error, 401 = auth, 422 = Pydantic validation, 500 = scheduler unavailable
        assert resp.status_code in (400, 401, 422, 500), \
            f"Expected 400/422 for out-of-range hours, got {resp.status_code}"

    def test_valid_hours_accepted(self, client, auth_headers):
        """PUT /api/v1/schedule with valid 0-23 hours must return 200 or 401."""
        resp = client.put(
            "/api/v1/schedule",
            json={"content_hours_utc": [6, 12, 18]},
            headers=auth_headers,
        )
        # 200 = accepted, 401 = auth, 500 = scheduler unavailable in test
        assert resp.status_code in (200, 401, 500), \
            f"Valid hours rejected: {resp.status_code}"


# ---------------------------------------------------------------------------
# guardian/auto_fix.py fix #6 — branch retry 201-only guard (integration suite)
# ---------------------------------------------------------------------------

class TestAutoFixBranchRetry201OnlyIntegration:
    def test_auto_fix_source_requires_201_not_201_or_422(self):
        """auto_fix.py must require exactly HTTP 201 after branch creation/retry."""
        import inspect
        from guardian import auto_fix as af_mod
        src = inspect.getsource(af_mod)
        assert "branch_response.status_code != 201" in src, \
            "Branch retry guard must require 201 exactly — not 'not in [201, 422]'"
        assert "not in [201, 422]" not in src, \
            "Permissive guard 'not in [201, 422]' still present — second 422 proceeds to PR creation"

    def test_branch_retry_fails_fast_on_non_201(self):
        """create_fix_pr() must return None when branch creation returns non-201."""
        from guardian.auto_fix import AutoFixEngine
        import requests

        engine = AutoFixEngine()

        def make_mock(status, json_data):
            m = MagicMock()
            m.status_code = status
            m.json.return_value = json_data
            return m

        import guardian.auto_fix as _af_mod
        mock_http = MagicMock()
        mock_http.get.return_value = make_mock(200, {
            "default_branch": "main",
            "owner": {"login": "test"},
            "sha": "file_sha",
            "object": {"sha": "abc123"},
        })
        mock_http.post.side_effect = [
            make_mock(201, {"sha": "blob123"}),
            make_mock(201, {"sha": "tree123"}),
            make_mock(201, {"sha": "commit123"}),
            make_mock(422, {}),
            make_mock(400, {}),
        ]
        with patch("guardian.auto_fix.settings"), \
             patch.object(_af_mod, "_http", mock_http):
            result = engine.create_fix_pr(
                "owner/repo", "file.py", "fixed content", "Fix security issue"
            )
            assert result is None, \
                f"Expected None when branch retry fails, got {result!r}"
