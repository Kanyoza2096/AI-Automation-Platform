"""
Command Executor — Natural Language to API Action.
Receives user messages, parses intent via Gemini, executes API calls.
Supports built-in API actions AND external industry plugin commands.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from config.settings import settings

logger = logging.getLogger("kanyoza.services.command_executor")

# ── Intent parsing prompt ─────────────────────────────────────────────────

INTENT_PROMPT = """You are a command parser for the Kanyoza AI Platform. Given a user message, determine if it's a command (an action to perform) or a chat message (just conversation).

If it's a COMMAND, return JSON with the action and extracted parameters.
If it's a CHAT message, return {"action": null}.

Available actions and their parameters:

- create_workspace: name (required), plan (optional: free/pro/business), owner_email (optional)
- list_workspaces: no params
- create_brand: workspace_id or "default", name (required), tone (optional), language (optional)
- list_brands: workspace_id or "default"
- create_ai_profile: workspace_id or "default", name (required), expertise (optional), tone (optional)
- schedule_post: caption (required), category (optional)
- force_post_now: no params
- pause_scheduler: no params
- resume_scheduler: no params
- check_health: no params
- get_stats: no params
- list_social_accounts: workspace_id or "default"
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
        "path": lambda p: f"/workspaces/{p.get('workspace_id', 'default')}/brands",
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
        "path": lambda p: f"/workspaces/{p.get('workspace_id', 'default')}/social-accounts",
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
    
    def __init__(self, gemini_service, master_token: str, base_url: str, container=None):
        self._gemini = gemini_service
        self._token = master_token
        self._base = base_url.rstrip("/")
        self._container = container  # FastAPI service container — replaces Flask current_app

    # ── Plugin Command Registration (Class Methods) ──────────────────────

    @classmethod
    def register_external_command(cls, plugin_name: str, command: dict) -> None:
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
        key = f"{plugin_name}:{command['intent']}"
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

        import requests

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
            elif action["method"] == "POST":
                resp = requests.post(url, headers=headers, json=body, timeout=15)
            else:
                return {"ok": False, "message": f"Unsupported method: {action['method']}"}

            if resp.ok:
                data = resp.json()
                msg = action["success_msg"](data) if callable(action["success_msg"]) else action["success_msg"]
                return {"ok": True, "message": msg, "action": action_name, "data": data}
            else:
                return {"ok": False, "message": f"API error: {resp.status_code} — {resp.text[:200]}"}
        except Exception as e:
            logger.error("Command execution failed: %s", e)
            return {"ok": False, "message": f"Execution failed: {str(e)}"}

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

            # Execute the plugin command
            workspace_id = command_params.pop("workspace_id", "default")
            result = plugin.execute(command, command_params, workspace_id)

            return result

        except Exception as e:
            logger.error("Plugin execution failed for %s/%s: %s", plugin_name, command, e)
            return {"ok": False, "message": f"Plugin execution failed: {str(e)}"}
