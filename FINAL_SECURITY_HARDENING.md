# KDO Final Security Hardening — Pass 2

Applied to the Phase 13 / 2.29.0 source tree:

- Privileged legacy routers now use canonical `require_auth_context` at router level.
- GrowthBridge public team mutations require authenticated context.
- GrowthBridge public team responses are explicitly projected and only expose active public profiles.
- GrowthBridge admin token validation now fails closed and uses constant-time comparison.
- Trend admin authentication fails closed when MASTER_API_TOKEN is missing.
- Director authentication bypass environment switch removed. Missing master token denies access.
- Production multi-process compose publishes the API only on loopback; core/workers remain internal.
- Web Intelligence outbound fetch now validates DNS-resolved addresses and blocks localhost/private/link-local/multicast/reserved targets, rejects redirects, and caps response size.
- Director web-fetch binding uses the same SSRF guard.
- GitHub admin and Web Intelligence HTTP surfaces are protected by canonical auth dependencies.
- Python source compiles successfully with `python -m compileall`.

Remaining release gates require a network-enabled dependency install and a real Supabase/Postgres environment for full tests.
