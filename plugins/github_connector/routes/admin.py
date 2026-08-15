"""
GitHub Connector Plugin Admin Routes.
"""

from fastapi import APIRouter, HTTPException, Depends, Request

router = APIRouter(prefix="/api/v1/github", tags=["github-connector"])


def _get_plugin():
    try:
        from plugins.github_connector.plugin import github_connector_plugin
        return github_connector_plugin
    except Exception:
        return None


@router.get("/status")
async def get_status():
    plugin = _get_plugin()
    if not plugin:
        return {"status": "not_initialized"}
    return plugin.health_check()


@router.post("/review-pr")
async def review_pr(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin._handle_review_pr(data)


@router.post("/generate-description")
async def generate_description(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin._handle_generate_pr_desc(data)


@router.post("/analyze-repo")
async def analyze_repo(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin._handle_analyze_repo(data)


@router.post("/suggest-fix")
async def suggest_fix(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin._handle_suggest_fix(data)


@router.post("/generate-docs")
async def generate_docs(data: dict):
    plugin = _get_plugin()
    if not plugin:
        raise HTTPException(503, "Plugin not running")
    return plugin._handle_generate_docs(data)
