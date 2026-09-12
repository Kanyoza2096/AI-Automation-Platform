
"""Web Intelligence HTTP surface."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from typing import Optional

from core.security.deps import require_auth_context

router = APIRouter(prefix="/api/web", tags=["web-intelligence"], dependencies=[Depends(require_auth_context)])


@router.get("/search")
async def web_search(
    q: str = Query(..., min_length=1),
    max_results: int = Query(5, ge=1, le=20),
    provider: Optional[str] = None,
):
    from services.web_intelligence import get_web_intelligence, init_web_intelligence
    gw = get_web_intelligence() or init_web_intelligence()
    results = await gw.search_async(q, max_results=max_results, provider=provider)
    return {
        "ok": True,
        "query": q,
        "count": len(results),
        "results": [r.to_dict() for r in results],
        "providers": gw.list_providers(),
    }


@router.get("/providers")
async def list_providers():
    from services.web_intelligence import get_web_intelligence, init_web_intelligence
    gw = get_web_intelligence() or init_web_intelligence()
    return {"providers": gw.list_providers()}


@router.post("/ask")
async def tool_ask(body: dict):
    """LLM tool-using loop: may call web_search / web_fetch then answer."""
    question = (body.get("question") or body.get("q") or body.get("prompt") or "").strip()
    if not question:
        return {"ok": False, "error": "question required"}
    try:
        max_steps = int(body.get("max_steps") or 4)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_steps must be an integer"}
    max_steps = max(1, min(max_steps, 12))
    context = body.get("context")
    from services.tool_agent import run_tool_agent
    result = await run_tool_agent(question, max_steps=max_steps, context=context)
    return result
