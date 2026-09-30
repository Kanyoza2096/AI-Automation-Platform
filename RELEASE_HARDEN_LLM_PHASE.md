# Omnexa / Kanyoza — HARDEN + LLM Phase Closeout
**Date:** 2026-08-27  
**Package:** Kanyoza-AI-Automation-Platform-FIXED-2026-08-27.zip

## Product rules (frozen)

1. LLMs **enhance**; they never own the critical path.
2. Deterministic software provides **truth**; policy provides **authority**.
3. No fake success, no fake intelligence scores, no accept-all webhooks in production.
4. `X-Workspace-ID` is **not** authority for normal principals.
5. Architecture follows the Omnexa report ladder: HARDEN → gateway → controlled cognition.

---

## Code delivered in this package

### Publish / Council (runtime blockers from logs)
- Persona `_is_distracted`, CouncilDecision dissent safety
- Publish gate `decision_id` wiring
- DFMO / monitor_checks row shape, notifications, alert timeouts, topic fallbacks

### HARDEN foundation
| Area | Artifact |
|------|----------|
| Queue truthful accept/reject | `workers/task_queue.py` |
| Distributed lock honesty | `core/distributed_lock.py` + scheduler |
| Workspace fail-closed | `core/workspace_context.py`, middleware, `core/security/deps.py` |
| RLS defense-in-depth SQL | `migrations/018_rls_defense_in_depth.sql` |
| Webhooks HMAC + idempotency | `core/webhook_security.py`, GitHub + Facebook routes |
| Tenant list leaks | `api/v1/brands.py`, `workspaces.py` |
| SSRF monitor | `core/ssrf_guard.py`, monitor plugin |
| Director no fake success | `services/director.py`, `director_bindings.py` |
| Health dimensions | `main.py` `/health/live` `/ready` `/detailed` |
| Event workspace_id | `core/nerve.py`, `unified_bus.py` |
| HTTP client policy | `HttpClient` / `AsyncHttpClient`, telegram + health sample |

### LLM phase
| Area | Artifact |
|------|----------|
| Gateway + modes | `ai/gateway.py` |
| Boot step | `lifecycle.py` → `LLMGateway` |
| Content / tool_agent | degraded paths |
| Trend semantic + briefing | gateway |
| Meta optional narrative | structured via gateway |
| Council optional summary | `council/intelligence/llm_enrichment.py` (default LLM-free) |
| Structured JSON API | `complete_structured` |
| Budgets Redis/memory | `llm:budget:{date}:{workspace}` |
| Prompt sanitize | PII redact + injection prefix |

Docs in tree: `LLM_GATEWAY.md`, `LLM_PHASE_*.md`, `HARDEN_P0_*.md`

---

## Operator checklist (required outside the ZIP)

1. **Deploy** this package to Render (or target host).
2. **Supabase:** run `migrations/018_rls_defense_in_depth.sql`.
3. **Secrets:**
   - `GITHUB_WEBHOOK_SECRET`
   - `APP_SECRET` / Facebook app secret
   - `GEMINI_KEY` (optional for full intelligence mode)
   - `MASTER_API_TOKEN` / auth as already used
4. **Redis** recommended; set `REQUIRE_DISTRIBUTED_LOCK=true` for multi-instance publish safety.
5. **Verify:**
   - `GET /health/live` → alive  
   - `GET /health/ready` → ready or ready_degraded  
   - `GET /health/detailed` (auth) → intelligence + coordination dimensions  
   - Facebook/GitHub webhooks reject bad signatures in production  
   - Content still generates when Gemini is down (degraded caption path)

Optional LLM flags: `TREND_SEMANTIC_SCORE`, `TREND_LLM_BRIEFING`, `META_LLM_NARRATIVE`, `COUNCIL_LLM_SUMMARY`.

---

## Explicitly out of scope for this closeout

- Full rewrite to microservices  
- Complete connector migration of every `requests` call  
- Full PII/compliance program  
- Per-operation cost ledger in Postgres  
- Frontend Next.js product shell  
- Formal organism autonomy LEVEL contracts platform-wide  

Those follow the same report sequence after this foundation is live.

---

## Definition of done (this phase)

- [x] Repository package compiles key patched modules  
- [x] No queue fake success  
- [x] No Director placeholder success  
- [x] Webhooks fail-closed in production without secrets  
- [x] LLM optional with explicit modes  
- [x] Tenant header not trusted alone  
- [ ] RLS 018 applied in **your** Supabase (ops)  
- [ ] Secrets + Redis configured in **your** environment (ops)  
- [ ] Post-deploy log verification (ops)

**Engineering closeout for HARDEN + LLM gateway phase: complete.**  
Remaining work is **ops apply + observe**, then next report steps (formal organisms, Meta learning loop, frontend).
