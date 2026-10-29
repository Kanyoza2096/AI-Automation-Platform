"""
Command Executor — Natural Language to API Action.
Receives user messages, parses intent via Gemini, executes API calls.
Supports built-in API actions AND external industry plugin commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from config.settings import settings
from core.request_context import require_request_context

logger = logging.getLogger("kanyoza.services.command_executor")

# ── Intent parsing prompt ─────────────────────────────────────────────────

INTENT_PROMPT = """You are a command parser for the Kanyoza AI Platform. Given a user message, determine if it's a command (an action to perform) or a chat message (just conversation).

If it's a COMMAND, return JSON with the action and extracted parameters.
If it's a CHAT message, return {"action": null}.

Available actions and their parameters:

- create_workspace: name (required), plan (optional: free/pro/business), owner_email (optional)
- list_workspaces: no params
- create_brand: workspace_id (must come from authenticated context), name (required), tone (optional), language (optional)
- list_brands: workspace_id (must come from authenticated context)
- create_ai_profile: workspace_id (must come from authenticated context), name (required), expertise (optional), tone (optional)
- schedule_post: caption (required), category (optional)
- force_post_now: no params
- pause_scheduler: no params
- resume_scheduler: no params
- check_health: no params
- get_stats: no params
- list_social_accounts: workspace_id (must come from authenticated context)
- generate_api_key: label (optional)

Plugin commands (use plugin_execute action with plugin name):
- plugin_execute: plugin (required — one of: {plugin_names}), command (required), params (object)

Examples:
"create a workspace called TechHub Malawi" → {"action": "create_workspace", "params": {"name": "TechHub Malawi"}}
"make a workspace named Gospel Ministry with pro plan" → {"action": "create_workspace", "params": {"name": "Gospel Ministry", "plan": "pro"}}
"show all workspaces" → {"action": "list_workspaces", "params": {}}
"what's the system health" → {"action": "check_health", "params": {}}
"pause all posting" → {"action": "pause_scheduler", "params": {}}
"post something now" → {"action": "force_post_now", "params": {}}
"add member John Doe phone 0888123456" → {"action": "plugin_execute", "params": {"plugin": "church_mis", "command": "add_member", "params": {"name": "John Doe", "phone": "0888123456"}}}
"record attendance for Sunday service John Mary Peter" → {"action": "plugin_execute", "params": {"plugin": "church_mis", "command": "record_attendance", "params": {"service_date": "Sunday", "members": "John, Mary, Peter"}}}
"add donation 50000 from John Doe purpose tithe" → {"action": "plugin_execute", "params": {"plugin": "church_mis", "command": "add_donation", "params": {"amount": 50000, "donor": "John Doe", "purpose": "tithe"}}}
"show all choir members" → {"action": "plugin_execute", "params": {"plugin": "church_mis", "command": "get_members", "params": {"department": "choir"}}}
"hello how are you" → {"action": null}
"what is python" → {"action": null}

Return ONLY valid JSON. No markdown, no explanation."""


# ── API action registry ───────────────────────────────────────────────────

API_ACTIONS: Dict[str, Dict[str, Any]] = {
    "create_workspace": {
        "method": "POST",
        "path": "/workspaces",
        "body": lambda p: {"name": p["name"], "plan": p.get("plan", "free"), "owner_email": p.get("owner_email", "")},
        "success_msg": lambda r: f"Workspace '{r.get('workspace', {}).get('name', 'unknown')}' created.",
    },
    "list_workspaces": {
        "method": "GET",
        "path": "/workspaces",
        "body": None,
        "success_msg": lambda r: f"Found {len(r.get('workspaces', []))} workspace(s).",
    },
    "create_brand": {
        "method": "POST",
        "path": lambda p: f"/workspaces/{p['workspace_id']}/brands",
        "body": lambda p: {"name": p["name"], "tone": p.get("tone", ""), "language": p.get("language", "en")},
        "success_msg": lambda r: f"Brand '{r.get('brand', {}).get('name', 'unknown')}' created.",
    },
    "check_health": {
        "method": "GET",
        "path": "/health/deep",
        "body": None,
        "success_msg": lambda r: f"System status: {r.get('status', 'unknown')}. {len(r.get('services', {}))} services monitored.",
    },
    "pause_scheduler": {
        "method": "POST",
        "path": "/workflow/pause",
        "body": None,
        "success_msg": lambda r: "Scheduler paused.",
    },
    "resume_scheduler": {
        "method": "POST",
        "path": "/workflow/resume",
        "body": None,
        "success_msg": lambda r: "Scheduler resumed.",
    },
    "force_post_now": {
        "method": "POST",
        "path": "/bot/post",
        "body": None,
        "success_msg": lambda r: "Post queued for publishing." if r.get("queued") else "Queue full — try again shortly.",
    },
    "get_stats": {
        "method": "GET",
        "path": "/dashboard/live",
        "body": None,
        "success_msg": lambda r: f"Stats: {r.get('messages_today', 0)} messages, {r.get('posts_published', 0)} posts today.",
    },
    "list_social_accounts": {
        "method": "GET",
        "path": lambda p: f"/workspaces/{p['workspace_id']}/social-accounts",
        "body": None,
        "success_msg": lambda r: f"Found {len(r.get('social_accounts', []))} social account(s).",
    },
    "generate_api_key": {
        "method": "POST",
        "path": "/keys/generate",
        "body": lambda p: {"label": p.get("label", "api-key")},
        "success_msg": lambda r: f"API key generated: {r.get('prefix', '****')}",
    },
}





class CommandExecutor:
    """Parses natural language and executes commands against the Kanyoza API."""
    
    
    # ── External plugin command registry ──────────────────────────────────────
    _external_commands: Dict[str, Dict[str, Any]] = {}
    
    def __init__(self, gemini_service, master_token: str = "", base_url: str = "", container=None, credential_token: str = ""):
        self._gemini = gemini_service
        # Never use a platform/master credential for a user-originated command.
        # ``master_token`` remains accepted for compatibility but is deliberately
        # not selected when a request context exists.
        self._master_token = master_token
        self._credential_token = credential_token.strip()
        self._token = self._credential_token or self._master_token
        self._base = base_url.rstrip("/")
        self._container = container  # FastAPI service container — replaces Flask current_app

    # ── Plugin Command Registration (Class Methods) ──────────────────────

    @classmethod
    def register_external_command(cls, plugin_name: str, command: dict) -> None:
        if not isinstance(command, dict):
            return
        if "intent" not in command and "name" in command:
            command = {**command, "intent": command["name"]}
        """
        Register a command from an industry plugin.

        Called by lifecycle.py during boot for each plugin command.
        Example:
            CommandExecutor.register_external_command("church_mis", {
                "intent": "add_member",
                "description": "Register a new church member",
                "params": ["name", "phone", "email"],
                "example": "Add member John Doe phone 0888123456",
            })
        """
        key = f"{plugin_name}:{command.get('intent') or command.get('name') or 'unknown'}"
        cls._external_commands[key] = {
            "plugin": plugin_name,
            **command,
        }
        logger.debug("Registered external command: %s", key)

    @classmethod
    def get_external_commands(cls) -> Dict[str, Dict[str, Any]]:
        """Return all registered external plugin commands."""
        return cls._external_commands

    @classmethod
    def get_plugin_names(cls) -> list:
        """Return list of registered plugin names."""
        names = set()
        for cmd in cls._external_commands.values():
            names.add(cmd.get("plugin", "unknown"))
        return sorted(names)

    # ── Intent Parsing ───────────────────────────────────────────────────

    def parse_intent(self, user_message: str) -> Optional[Dict[str, Any]]:
        """Use Gemini to determine if this is a command and extract params."""
        # Build prompt with current plugin names
        plugin_names = self.get_plugin_names()
        plugin_list = ", ".join(plugin_names) if plugin_names else "none registered"
        prompt = INTENT_PROMPT.replace("{plugin_names}", plugin_list)
        prompt += f"\n\nUser message: {user_message}\n\nJSON response:"

        try:
            raw = self._gemini._call(prompt, temperature=0.1, max_tokens=500)
            # Extract JSON
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return None
            data = json.loads(raw[start:end + 1])

            if data.get("action") is None:
                return None  # Not a command
            return data
        except Exception as e:
            logger.warning("Intent parsing failed: %s", e)
            return None

    # ── Command Execution ────────────────────────────────────────────────

    def execute(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the parsed intent — API action or plugin command."""
        action_name = intent.get("action")
        params = intent.get("params", {})

        # ── Plugin execution ─────────────────────────────────────────────
        if action_name == "plugin_execute":
            return self._execute_plugin(params)

        # ── Built-in API action ──────────────────────────────────────────
        action = API_ACTIONS.get(action_name)
        if not action:
            return {"ok": False, "message": f"Unknown action: {action_name}"}

        # Built-in mutations are intentionally not executable from this legacy
        # natural-language surface. They must be compiled into an ActionIntent
        # and admitted by the canonical Action Kernel. Read-only GET actions may
        # remain as compatibility reads because they do not create side effects.
        if str(action.get("method", "")).upper() != "GET":
            return {
                "ok": False,
                "status": "governance_required",
                "message": "This command must enter the governed Action Kernel before execution.",
                "action": action_name,
                "execution_authority": "action_kernel",
                "legacy_executor": "non_authoritative",
                "canonical_next_path": "ActionIntent -> Governance -> Authorization -> Action Kernel -> Connector -> Provider -> Receipt -> Verification -> Reconciliation",
            }

        import requests

        # Resolve trusted workspace before constructing any tenant-scoped URL.
        # Client workspace hints are ignored for ordinary principals.
        try:
            ctx = require_request_context()
            if "workspace_id" in params:
                requested_ws = str(params.get("workspace_id") or "").strip()
                params = dict(params)
                params["workspace_id"] = ctx.effective_workspace(requested_ws) if requested_ws else ctx.workspace_id
            if not ctx.species_privileges and not ctx.workspace_id:
                return {"ok": False, "message": "Authenticated request has no workspace scope"}
        except RuntimeError:
            # Background callers must explicitly provide their own trusted
            # credential/context; do not silently select the default tenant.
            return {"ok": False, "message": "Command execution requires an authenticated request context"}

        # Resolve path
        path = action["path"](params) if callable(action["path"]) else action["path"]
        url = f"{self._base}{path}"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        body = None
        if action["body"]:
            body = action["body"](params) if callable(action["body"]) else action["body"]

        try:
            if action["method"] == "GET":
                resp = requests.get(url, headers=headers, timeout=15)
            else:
                return {"ok": False, "message": f"Unsupported method: {action['method']}"}

            if resp.ok:
                data = resp.json()
                msg = action["success_msg"](data) if callable(action["success_msg"]) else action["success_msg"]
                return {"ok": True, "message": msg, "action": action_name, "data": data}
            else:
                return {"ok": False, "message": f"API error: {resp.status_code} — {resp.text[:200]}"}
        except Exception:
            logger.exception("Command execution failed")
            return {"ok": False, "message": "Execution failed; see server logs for details."}

    # ── Plugin Execution ─────────────────────────────────────────────────

    def _execute_plugin(self, params: dict) -> Dict[str, Any]:
        """
        Route execution to the correct industry plugin.

        Plugin commands bypass the HTTP API — they call the plugin directly
        via the DI container for lower latency and direct DB access.
        """
        plugin_name = params.get("plugin", "").strip()
        command = params.get("command", "").strip()
        command_params = params.get("params", {})

        if not plugin_name:
            return {"ok": False, "message": "Plugin name is required. Say 'church_mis' or ask what plugins are available."}

        if not command:
            return {"ok": False, "message": "Command is required. Try 'add_member', 'record_attendance', etc."}

        # Resolve plugin from FastAPI DI container (passed at construction — no Flask)
        try:
            container = self._container
            if not container:
                return {"ok": False, "message": "Plugin system is not available — service container not found."}

            plugins = getattr(container, "industry_plugins", {})
            plugin = plugins.get(plugin_name)

            if not plugin:
                available = list(plugins.keys()) if plugins else []
                return {
                    "ok": False,
                    "message": f"Plugin '{plugin_name}' not found. Available: {available or 'none registered'}.",
                }

            # Generic plugin execution is NOT an execution authority. A plugin
            # command must explicitly declare either read_only or an Action Kernel
            # contract. This prevents the AI command surface from becoming a
            # hidden second mutation plane.
            command_spec = next(
                (c for c in plugin.get_commands()
                 if str(c.get("intent") or c.get("name") or "") == command),
                None,
            )
            if not command_spec:
                return {"ok": False, "message": "Plugin command is not registered for governed execution."}

            execution_mode = str(command_spec.get("execution_mode") or "").strip().lower()
            from core.plugin_execution import resolve_plugin_workspace
            requested_ws = command_params.get("workspace_id")
            workspace_id = resolve_plugin_workspace(requested_ws)
            safe_params = {k: v for k, v in command_params.items() if k != "workspace_id"}

            if execution_mode == "read_only":
                result = plugin.execute(command, safe_params, workspace_id)
                return result

            if execution_mode != "action_kernel" or not command_spec.get("governed_action_type"):
                logger.warning(
                    "Blocked ungoverned plugin mutation: %s/%s", plugin_name, command
                )
                return {
                    "ok": False,
                    "status": "governance_required",
                    "message": "This plugin action is not yet registered with the governed Action Kernel.",
                }

            from services.action_kernel import build_default_action_kernel
            from services.supabase import get_service_client_from_settings
            db = None
            try:
                from supabase import Client as SupabaseClient
                db = container.try_resolve(SupabaseClient) if container else None
            except Exception:
                db = None
            if db is None:
                db = get_service_client_from_settings()
            kernel = build_default_action_kernel(db, allow_in_memory=False)
            action_type = str(command_spec["governed_action_type"])
            # Natural-language commands do not mint approval. High/critical
            # contracts therefore remain approval_required until an explicit
            # governed approval is supplied elsewhere.
            result = asyncio.run(kernel.execute(
                workspace_id=workspace_id,
                action_type=action_type,
                parameters=safe_params,
                risk_level=str(command_spec.get("risk_level") or "medium"),
                source=f"command_executor:{plugin_name}",
            ))
            return result

        except Exception as e:
            logger.error("Plugin execution failed for %s/%s: %s", plugin_name, command, e)
            return {"ok": False, "message": f"Plugin execution failed: {str(e)}"}
