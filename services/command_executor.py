"""
Command Executor — Natural Language to API Action.
Receives user messages, parses intent via Gemini, executes API calls.
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

Examples:
"create a workspace called TechHub Malawi" → {"action": "create_workspace", "params": {"name": "TechHub Malawi"}}
"make a workspace named Gospel Ministry with pro plan" → {"action": "create_workspace", "params": {"name": "Gospel Ministry", "plan": "pro"}}
"show all workspaces" → {"action": "list_workspaces", "params": {}}
"what's the system health" → {"action": "check_health", "params": {}}
"pause all posting" → {"action": "pause_scheduler", "params": {}}
"post something now" → {"action": "force_post_now", "params": {}}
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
        "success_msg": lambda r: f"Found {r.get('workspaces', {}).get('length', len(r.get('workspaces', [])))} workspace(s).",
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

    def __init__(self, gemini_service, master_token: str, base_url: str):
        self._gemini = gemini_service
        self._token = master_token
        self._base = base_url.rstrip("/")

    def parse_intent(self, user_message: str) -> Optional[Dict[str, Any]]:
        """Use Gemini to determine if this is a command and extract params."""
        prompt = INTENT_PROMPT + f"\n\nUser message: {user_message}\n\nJSON response:"
        
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

    def execute(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the parsed intent against the API."""
        action_name = intent.get("action")
        params = intent.get("params", {})
        
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
