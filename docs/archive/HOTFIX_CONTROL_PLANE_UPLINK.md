# Hotfix — Control plane uplink (status + CORS)

## Symptoms
- UPLINK PARTIAL / BACKEND REQUEST TIMED OUT: /api/v1/status
- CONNECTION FAILED: signal timed out
- REST API: NO DATA

## Causes
1. **Free Render spin-down** — first request can take 50–90s; UI often times out at 10–15s.
2. **`/api/v1/status` required auth** — middleware treated all `/api/*` as private; connection test failed without/before token.
3. **CORS** — missing `X-Workspace-ID` / `X-Brand-ID` in allow_headers blocked browser calls.

## Fixes
- Public: `/api/v1/status`, `/api/v1/health`
- CORS allow/expose workspace headers
- Richer public status payload (alive, version 16.1.0, websocket path)

## Frontend settings
- REST API BASE: `https://omnexa-autonomy.onrender.com`
- WebSocket: same origin + `/socket.io` (Socket.IO)
- MASTER_API_TOKEN: must match backend env exactly
- ALLOWED_ORIGINS on backend must include your Pages/Vercel origins

## After deploy
1. Open backend URL once in browser (wake free tier)
2. Wait until /health returns JSON
3. Click Test Connection in control plane
