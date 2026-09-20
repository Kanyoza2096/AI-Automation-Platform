# Capability status (honest)

## Fully wired (engineering)

| Area | Status |
|------|--------|
| Boot / lifecycle | Meta sync-await fixed; Director soft-fail |
| Director non-critical hands | Bound + async invoke + execute_capability |
| Trend Intelligence v2.4 | Staged events, adapters, dedup, workspace, publish request |
| Trend → Facebook | publish_scheduled_content uses trend payload |
| Web Intelligence | Gateway + providers + API |
| Tool agent | LLM chooses web_search/fetch |
| Messenger routing | Current-fact → tool agent |
| DFMO v1.4 | Probes, auto-heal, retention, path probes, Reliability handoff |
| Reliability Engine | Pulse → diagnose → plan → policy → execute → memory |
| Unified health | `/api/health/unified` multi-dimension score |
| SQL migrations | trend multitenant + DFMO retention (must run in Supabase) |

## Still ops-dependent (not code gaps)

| Area | Requirement |
|------|-------------|
| Non-zero trend posts | NEWSAPI_KEY / TWITTER / sources rows / page token |
| High-quality search | TAVILY_API_KEY or BRAVE_API_KEY |
| High-quality images | Cloudflare Workers AI credentials |
| Multi-tenant RLS enforced for clients | Run migration 002; JWT workspace claim |

## Intentionally bounded

| Area | Why |
|------|-----|
| Critical Director actions | Human approval required |
| LLM free-form production heal | Policy/playbook only |
| Live Facebook canary posts | dry_run path probes only by default |
