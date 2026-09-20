"""
Tool-using agent loop — LLM decides when to call Web Intelligence (and other tools).

Pattern (bounded ReAct / JSON tool calls):
  User question
    → LLM (system: tools available)
    → either final answer OR {"tool": "...", "args": {...}}
    → execute tool
    → feed result back
    → repeat until answer or max_steps
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("kanyoza.tool_agent")

MAX_STEPS_DEFAULT = 4

TOOL_SPECS = [
    {
        "name": "web_search",
        "description": "Search the live web for current information. Use when the question needs up-to-date facts, news, or verification.",
        "parameters": {
            "query": "search query string",
            "max_results": "optional int 1-10, default 5",
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch and extract text from a specific URL.",
        "parameters": {
            "url": "https URL to fetch",
        },
    },
]


def _tool_catalog_prompt() -> str:
    lines = [
        "You are a helpful assistant with access to tools.",
        "When you need current information from the internet, call a tool.",
        "When you can answer from reasoning alone, answer directly.",
        "",
        "Tools:",
    ]
    for t in TOOL_SPECS:
        lines.append(f"- {t['name']}: {t['description']}")
        lines.append(f"  params: {t['parameters']}")
    lines.extend(
        [
            "",
            "To call a tool, reply with ONLY a JSON object on one line:",
            '{"tool": "web_search", "args": {"query": "..."}}',
            "",
            "To give a final answer, reply with ONLY:",
            '{"answer": "your full answer here"}',
            "",
            "Do not mix prose with the JSON. No markdown fences.",
        ]
    )
    return "\n".join(lines)


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    raw = text.strip()
    # Strip markdown fences if model ignores instructions
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # First JSON object in text
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # try line by line
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


async def _run_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    args = args or {}
    if name == "web_search":
        from services.web_intelligence import get_web_intelligence, init_web_intelligence

        gw = get_web_intelligence() or init_web_intelligence()
        query = (args.get("query") or args.get("q") or "").strip()
        max_results = int(args.get("max_results") or 5)
        results = await gw.search_async(query, max_results=max_results)
        return {
            "ok": bool(results),
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }
    if name == "web_fetch":
        from services.web_intelligence import get_web_intelligence, init_web_intelligence

        gw = get_web_intelligence() or init_web_intelligence()
        url = (args.get("url") or "").strip()
        page = await gw.fetch_async(url)
        return page.to_dict()
    return {"ok": False, "error": f"unknown_tool:{name}"}


def _llm_complete(prompt: str, temperature: float = 0.2, max_tokens: int = 1200) -> str:
    """Complete via LLM Gateway — never raises; empty string means degraded."""
    try:
        from ai.gateway import get_llm_gateway
        result = get_llm_gateway().complete(
            prompt,
            workspace_id="platform",
            capability="chat",
            temperature=temperature,
            max_tokens=max_tokens,
            caller="tool_agent",
        )
        if result.ok and result.text:
            return result.text
        logger.info(
            "tool_agent LLM degraded mode=%s reason=%s",
            result.mode.value,
            result.reason,
        )
        return ""
    except Exception as exc:
        logger.debug("tool_agent gateway path: %s", exc)

    # Legacy fallbacks (best-effort) if gateway import fails entirely
    try:
        from services.gemini import gemini
        out = gemini._call(prompt=prompt, temperature=temperature, max_tokens=max_tokens)
        if out:
            return str(out)
    except Exception as exc:
        logger.debug("gemini path: %s", exc)
    return ""



async def run_tool_agent(
    question: str,
    *,
    max_steps: int = MAX_STEPS_DEFAULT,
    context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run bounded tool-using loop.

    Returns:
      {
        "ok": bool,
        "answer": str,
        "steps": [...],
        "tools_used": [...],
      }
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "answer": "", "error": "empty_question", "steps": [], "tools_used": []}

    system = _tool_catalog_prompt()
    history: List[str] = []
    if context:
        history.append(f"Context:\n{context[:1500]}")

    steps: List[Dict[str, Any]] = []
    tools_used: List[str] = []
    final_answer = ""
    intelligence_mode = "full"

    for step in range(max_steps):
        user_block = f"Question: {question}\n"
        if history:
            user_block += "\n--- Tool / prior results ---\n" + "\n".join(history[-6:]) + "\n"
        user_block += "\nRespond with JSON tool call or JSON answer only."

        prompt = f"{system}\n\n{user_block}"
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _llm_complete(prompt)
        )
        if not (raw or "").strip() and step == 0:
            intelligence_mode = "unavailable"
            return {
                "ok": False,
                "answer": (
                    "AI assistance is temporarily unavailable. "
                    "Core platform features (scheduling, publishing, monitoring) "
                    "continue to work without the language model."
                ),
                "error": "llm_unavailable",
                "intelligence_mode": intelligence_mode,
                "steps": steps,
                "tools_used": tools_used,
            }
        parsed = _parse_llm_json(raw)

        if not parsed:
            # Model returned prose — treat as final answer if substantial
            if raw and len(raw.strip()) > 20 and "tool" not in raw.lower()[:40]:
                final_answer = raw.strip()
                steps.append({"step": step, "type": "prose_answer", "preview": final_answer[:200]})
                break
            steps.append({"step": step, "type": "parse_fail", "raw": (raw or "")[:300]})
            # One retry with stricter reminder
            history.append("System: Invalid format. Reply with {\"tool\":...} or {\"answer\":...} only.")
            continue

        if "answer" in parsed and parsed.get("answer"):
            final_answer = str(parsed["answer"]).strip()
            steps.append({"step": step, "type": "answer"})
            break

        tool_name = (parsed.get("tool") or "").strip()
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        if not tool_name:
            # maybe {"name": "web_search", ...}
            tool_name = (parsed.get("name") or "").strip()
            if not args and isinstance(parsed.get("parameters"), dict):
                args = parsed["parameters"]

        if not tool_name:
            steps.append({"step": step, "type": "no_tool_or_answer", "parsed": parsed})
            continue

        tool_result = await _run_tool(tool_name, args)
        tools_used.append(tool_name)
        steps.append(
            {
                "step": step,
                "type": "tool",
                "tool": tool_name,
                "args": args,
                "result_preview": str(tool_result)[:400],
            }
        )
        history.append(
            f"Tool {tool_name}({json.dumps(args)[:200]}) → {json.dumps(tool_result)[:1500]}"
        )

    if not final_answer:
        # Force synthesis from history
        synth_prompt = (
            f"{system}\n\nQuestion: {question}\n"
            f"Tool results:\n" + "\n".join(history[-8:]) + "\n\n"
            'Provide the final answer as {"answer": "..."} only.'
        )
        raw = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _llm_complete(synth_prompt)
        )
        parsed = _parse_llm_json(raw)
        if parsed and parsed.get("answer"):
            final_answer = str(parsed["answer"]).strip()
        elif raw:
            final_answer = raw.strip()
        else:
            final_answer = (
                "I could not produce a complete answer. "
                "Tool results may be insufficient or the model was unavailable."
            )
        steps.append({"step": "final_synth", "type": "synthesis"})

    return {
        "ok": bool(final_answer),
        "answer": final_answer,
        "steps": steps,
        "tools_used": tools_used,
        "max_steps": max_steps,
    }


# Sync wrapper for simple callers
def run_tool_agent_sync(question: str, **kwargs) -> Dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # schedule in new loop is messy; run coroutine in thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, run_tool_agent(question, **kwargs))
                return fut.result(timeout=120)
        return loop.run_until_complete(run_tool_agent(question, **kwargs))
    except RuntimeError:
        return asyncio.run(run_tool_agent(question, **kwargs))
