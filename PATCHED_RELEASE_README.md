# Kanyoza AI Automation Platform — Full Patched Release
**Date:** 2026-08-28

Complete tree with all remediation phases and hotfixes.

## Included
- Critical: API v1 mount / social plugins
- Phase 1: Publish path, Decision Gate strict, species_override, DI
- Phase 2: Schema 019, trend→outbox, service-mode matrix, workspace filters
- Phase 3: DFMO sensors (db_ok fixed), health truth, auto-heal, locks
- Phase 4: LLM spine
- Hotfixes: DeepSeek ABC, auth/OpenAPI, Gemini primary + LLM allowlist, control-plane uplink

## Control plane uplink
- `GET /api/v1/status` and `/api/v1/health` are **public** (no auth)
- CORS allows `X-Workspace-ID`, `X-Brand-ID`
- REST base: `https://omnexa-autonomy.onrender.com`
- WebSocket: Socket.IO on same host path `/socket.io`
- `ALLOWED_ORIGINS` must list every frontend origin
- `MASTER_API_TOKEN` must match control-plane token exactly

## LLM policy (default)
Allowed callers: `content`, `content_engine`, `director`  
Env: `LLM_ALLOWED_CALLERS=content,director,content_engine`

## Deploy
```bash
unzip Kanyoza-AI-Automation-Platform-PATCHED-FULL-2026-08-28.zip
cd AI-Automation-Platform-latest
# set Render env, run migrations/019 if needed
# push to GitHub so Render builds from this tree
```
