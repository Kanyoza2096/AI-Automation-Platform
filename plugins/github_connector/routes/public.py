"""
GitHub Connector Plugin Public Routes.
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/github", tags=["github-connector-public"])


@router.post("/webhook")
async def webhook(data: dict):
    """Receive GitHub webhooks."""
    try:
        from plugins.github_connector.plugin import github_connector_plugin
        if github_connector_plugin:
            return github_connector_plugin.handle_webhook(data)
        return {"ok": False, "error": "Plugin not running"}
    except Exception as e:
        raise HTTPException(500, str(e))
