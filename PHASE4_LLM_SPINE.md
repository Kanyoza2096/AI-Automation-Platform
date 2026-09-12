# Phase 4 — LLM intelligence spine (2026-08-28)

## Problem
Split-brain AI: DI built Gemini-only orchestrator; LLMGateway used full create_default_orchestrator.

## Fix
- dependency_registry: create_default_orchestrator only; register AIOrchestrator + LLMGateway
- ai/gateway.py: bind_orchestrator() + evaluate()
- LLM_GATEWAY.md: single path documentation

## Canonical
get_llm_gateway().complete / .reason / .evaluate
Providers: DeepSeek → Gemini → Groq → Mistral
