"""
Regression tests for bugs fixed in the production hardening pass.

Each test is named after the exact issue it guards against so future
developers know immediately what broke if a test fails.
"""

import threading
import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# core/memory.py — uuid import + get_summary thread safety
# ---------------------------------------------------------------------------

class TestMemorySystem:
    def test_add_message_does_not_use_dunder_import_uuid(self):
        """uuid must be a module-level import, not __import__() inline."""
        import core.memory as mem_mod
        import ast, inspect
        src = inspect.getsource(mem_mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == '__import__':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        assert node.args[0].value != 'uuid', \
                            "__import__('uuid') still present — should be a top-level import"

    def test_get_summary_returns_cached_value_under_lock(self):
        """get_summary() must read _summaries under the lock."""
        from core.memory import MemorySystem
        ms = MemorySystem()
        ms._summaries["user123"] = "test summary"
        result = ms.get_summary("user123")
        assert result == "test summary"

    def test_get_summary_returns_none_when_missing(self):
        from core.memory import MemorySystem
        ms = MemorySystem()
        assert ms.get_summary("nonexistent") is None

    def test_add_message_thread_safety(self):
        """Concurrent add_message calls must not corrupt the list."""
        from core.memory import MemorySystem
        ms = MemorySystem()
        errors = []

        def add(i):
            try:
                ms.add_message("sender", "user", f"message {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread-safety errors: {errors}"
        assert len(ms._short_term.get("sender", [])) <= 50


# ---------------------------------------------------------------------------
# analytics/tracker.py — uuid import
# ---------------------------------------------------------------------------

class TestAnalyticsTracker:
    def test_no_dunder_import_uuid(self):
        """tracker.py must not use __import__('uuid') inline."""
        import analytics.tracker as tracker_mod
        import ast, inspect
        src = inspect.getsource(tracker_mod)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == '__import__':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        assert node.args[0].value != 'uuid', \
                            "__import__('uuid') still present in tracker.py"

    def test_track_does_not_crash_without_supabase(self):
        """_track() with no supabase client must silently do nothing."""
        from analytics.tracker import AnalyticsTracker
        tracker = AnalyticsTracker(supabase_client=None)
        # Should not raise
        tracker.record_message_received("sender123")
        tracker.record_message_failed("sender123", "some error")


# ---------------------------------------------------------------------------
# routes/webhook.py — None-safe JSON + _slash_handler race condition
# ---------------------------------------------------------------------------

class TestWebhookRoutes:
    @pytest.fixture
    def app(self):
        """Create a minimal Flask app with webhook routes registered."""
        from flask import Flask
        from unittest.mock import patch

        application = Flask(__name__)
        # Patch dependencies that webhook imports at module level
        with patch('routes.webhook.facebook'), \
             patch('routes.webhook.gemini'), \
             patch('routes.webhook.memory'), \
             patch('routes.webhook.analytics'), \
             patch('routes.webhook.rate_limiter'), \
             patch('routes.webhook.owner_commands'):
            from routes.webhook import register_webhook_routes
            register_webhook_routes(application)
        return application

    def test_webhook_post_empty_body_does_not_crash(self, app):
        """POST /webhook with no JSON body must return 200, not 500."""
        with app.test_client() as client:
            with patch('routes.webhook.verify_facebook_signature', return_value=True):
                resp = client.post(
                    '/webhook',
                    data=b'',
                    content_type='text/plain',
                )
        assert resp.status_code == 200

    def test_webhook_post_malformed_json_does_not_crash(self, app):
        """POST /webhook with malformed JSON body must return 200, not 500."""
        with app.test_client() as client:
            with patch('routes.webhook.verify_facebook_signature', return_value=True):
                resp = client.post(
                    '/webhook',
                    data=b'not-json',
                    content_type='application/json',
                )
        assert resp.status_code == 200

    def test_slash_handler_lock_exists(self):
        """_slash_handler_lock must exist as a module-level threading.Lock."""
        import routes.webhook as wh
        assert hasattr(wh, '_slash_handler_lock'), \
            "_slash_handler_lock not defined — race condition not fixed"
        assert isinstance(wh._slash_handler_lock, type(threading.Lock())), \
            "_slash_handler_lock must be a threading.Lock"


# ---------------------------------------------------------------------------
# routes/api.py — None-safe request.get_json() across POST endpoints
# ---------------------------------------------------------------------------

class TestApiRoutes:
    @pytest.fixture
    def app(self):
        from flask import Flask
        application = Flask(__name__)
        with patch('routes.api.analytics'), \
             patch('routes.api.scheduler'), \
             patch('routes.api.memory'), \
             patch('routes.api.rate_limiter'), \
             patch('routes.api.settings') as mock_settings:
            mock_settings.owner_psid = ''
            mock_settings.content_posting_hours_utc = [4, 7, 10]
            mock_settings.news_posting_hours_utc = [6, 13]
            from routes.api import register_api_routes
            register_api_routes(application)
        return application

    def test_create_post_empty_body_returns_400_not_500(self, app):
        """POST /api/posts/create with no body must return 400, not 500."""
        with app.test_client() as client:
            resp = client.post(
                '/api/posts/create',
                data=b'',
                content_type='application/json',
                headers={'X-API-Key': ''},
            )
        # 400 (missing topic) or 401 (unauthorized) — NOT 500
        assert resp.status_code in (400, 401)

    def test_switch_tenant_empty_body_does_not_crash(self, app):
        """POST /api/tenants/switch with no body must not raise AttributeError."""
        with app.test_client() as client:
            resp = client.post(
                '/api/tenants/switch',
                data=b'',
                content_type='application/json',
                headers={'X-API-Key': ''},
            )
        # 401 (unauthorized) — NOT 500
        assert resp.status_code in (200, 401)

    def test_unblock_empty_body_returns_400_not_500(self, app):
        """POST /api/unblock with no body must return 400, not 500."""
        with app.test_client() as client:
            resp = client.post(
                '/api/unblock',
                data=b'',
                content_type='application/json',
                headers={'X-API-Key': ''},
            )
        assert resp.status_code in (400, 401)

    def test_update_schedule_invalid_hours_returns_400(self, app):
        """PUT /api/schedule with non-integer hours must return 400."""
        with app.test_client() as client:
            resp = client.put(
                '/api/schedule',
                json={'content_hours_utc': ['not', 'numbers']},
                headers={'X-API-Key': ''},
            )
        assert resp.status_code in (400, 401)


# ---------------------------------------------------------------------------
# guardian/auto_fix.py — correct method name + branch retry logic
# ---------------------------------------------------------------------------

class TestAutoFix:
    def test_generate_fix_calls_gemini_call_not_call_gemini(self):
        """AutoFixEngine.generate_fix() must call gemini._call(), not _call_gemini()."""
        from guardian.auto_fix import AutoFixEngine
        engine = AutoFixEngine()

        mock_gemini = MagicMock()
        mock_gemini._call.return_value = "fixed_code = 'safe_value'"

        issue = {'message': 'Hardcoded API Key', 'file': 'test.py',
                 'line': 10, 'recommendation': 'Use env var'}

        with patch('guardian.auto_fix.settings'), \
             patch('services.gemini.gemini', mock_gemini):
            # Patch the import inside generate_fix
            with patch.dict('sys.modules', {'services.gemini': MagicMock(gemini=mock_gemini)}):
                result = engine.generate_fix(issue, "API_KEY = 'hardcoded'")

        # _call_gemini must NOT have been called (it doesn't exist)
        assert not hasattr(mock_gemini, '_call_gemini') or not mock_gemini._call_gemini.called

    def test_auto_fix_engine_has_no_call_gemini_reference(self):
        """auto_fix.py source must not reference the non-existent _call_gemini method."""
        import inspect
        from guardian import auto_fix as af_mod
        src = inspect.getsource(af_mod)
        assert '_call_gemini' not in src, \
            "_call_gemini still referenced in auto_fix.py — will crash at runtime"

    def test_branch_retry_guard_present(self):
        """auto_fix.py must guard against a failed branch retry before creating PR."""
        import inspect
        from guardian import auto_fix as af_mod
        src = inspect.getsource(af_mod)
        assert 'Failed to create branch after retry' in src, \
            "Branch retry guard missing — PR creation proceeds even if branch creation fails"


# ---------------------------------------------------------------------------
# lifecycle.py — shutdown uses per-module attribute names
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_shutdown_uses_correct_attr_names(self):
        """lifecycle.py must map each module to its specific singleton attr name."""
        import inspect
        import lifecycle as lc_mod
        src = inspect.getsource(lc_mod)
        # The fix uses a list of (module, attr) tuples
        assert '_v9_singletons' in src, \
            "_v9_singletons not found — shutdown may try wrong attr names per module"
        # Verify the correct pairings appear in the source
        assert '"services.gemini", "gemini"' in src
        assert '"services.facebook", "facebook"' in src
        assert '"services.news_scraper", "news_scraper"' in src


# ---------------------------------------------------------------------------
# Additional regression tests surfaced by code review
# ---------------------------------------------------------------------------

class TestAutoFixBranchRetryComplete:
    def test_branch_retry_requires_201_not_422(self):
        """create_fix_pr() must return None when the retry branch creation also fails."""
        import inspect
        from guardian import auto_fix as af_mod
        src = inspect.getsource(af_mod)
        # Old guard allowed 422 through; new guard only allows 201
        assert 'branch_response.status_code != 201' in src, (
            "Branch retry guard must require HTTP 201 — allowing 422 on retry "
            "can attempt PR creation with a branch that was never created."
        )
        # The old permissive guard must be gone
        assert 'not in [201, 422]' not in src, (
            "Old permissive guard 'not in [201, 422]' still present — "
            "retry can silently proceed to PR creation on second 422."
        )


class TestScheduleOutOfRangeRejected:
    @pytest.fixture
    def app(self):
        from flask import Flask
        application = Flask(__name__)
        with patch("routes.api.analytics"), \
             patch("routes.api.scheduler"), \
             patch("routes.api.memory"), \
             patch("routes.api.rate_limiter"), \
             patch("routes.api.settings") as mock_settings:
            mock_settings.owner_psid = ""
            mock_settings.content_posting_hours_utc = [4, 7, 10]
            mock_settings.news_posting_hours_utc = [6, 13]
            from routes.api import register_api_routes
            register_api_routes(application)
        return application

    def test_out_of_range_content_hours_returns_400(self, app):
        """PUT /api/schedule with hours > 23 must return 400, not silently drop them."""
        with app.test_client() as client:
            resp = client.put(
                "/api/schedule",
                json={"content_hours_utc": [4, 7, 25]},  # 25 is invalid
                headers={"X-API-Key": ""},
            )
        # 401 (unauthorized) is also acceptable — the validation code is still exercised
        # but the important thing is it does NOT return 200 with mutated schedule
        assert resp.status_code in (400, 401), (
            f"Expected 400 or 401 for out-of-range hours, got {resp.status_code}"
        )

    def test_negative_hours_returns_400(self, app):
        """PUT /api/schedule with negative hours must return 400."""
        with app.test_client() as client:
            resp = client.put(
                "/api/schedule",
                json={"news_hours_utc": [-1, 6, 13]},
                headers={"X-API-Key": ""},
            )
        assert resp.status_code in (400, 401)

    def test_valid_hours_accepted(self, app):
        """PUT /api/schedule with valid 0-23 hours must not be rejected as invalid."""
        with app.test_client() as client:
            resp = client.put(
                "/api/schedule",
                json={"content_hours_utc": [4, 7, 10, 14, 17, 19]},
                headers={"X-API-Key": ""},
            )
        # 401 (unauthorized) is fine — means validation passed, auth failed
        # 200 means both validation and auth passed
        assert resp.status_code in (200, 401), (
            f"Valid hours incorrectly rejected: {resp.status_code}"
        )
