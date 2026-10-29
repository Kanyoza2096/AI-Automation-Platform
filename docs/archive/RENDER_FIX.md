# Render connectivity fix (ENGINE OFFLINE)

## Root causes addressed in this patch set

1. **PORT binding** — `settings.port` and `main.py` now honor `os.environ["PORT"]` (Render requirement).
2. **Telegram never awaited** — health alerts use `create_task` / `asyncio.run` instead of bare coroutine.
3. **Missing CP probe** — `GET /api/v1/integrations` (+ `/status`) so the control plane does not hang on a missing route after connectivity returns.

## Required Render start command

Prefer **one** of:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

or:

```bash
python main.py
```

(with the patched `main.py` that reads `$PORT`).

**Do not** hard-code `--port 5000` on Render.

## Free-tier overload (still required ops change)

Logs show continuous `workspaces` polls (~8s), DFMO, reliability, trend storms (402/429/403), and ~10s card renders on the **same** process that serves HTTP.

Until load is reduced, control plane may still **time out** even when HTTP is bound:

1. Set `SERVICE_MODE=api` (or equivalent) if supported to skip heavy workers on the web dyno.
2. Or split **Web Service** (API only) + **Background Worker** (Director/DFMO/trends).
3. Slow trend / monitor intervals; fix external API keys (Twitter 402, NewsAPI 429, Reddit 403).
4. Increase control-plane client timeout to 60s for free cold starts.

## Verify after deploy

```bash
curl -sS -w "\n%{http_code} %{time_total}s\n" --max-time 30 \
  https://kdo-gjrx.onrender.com/health

curl -sS -w "\n%{http_code} %{time_total}s\n" --max-time 30 \
  -H "Authorization: Bearer $MASTER_API_TOKEN" \
  https://kdo-gjrx.onrender.com/api/v1/integrations
```

Expect **HTTP 200** in under a few seconds (or up to ~50s on first cold start).

## Control plane Settings

- REST: `https://kdo-gjrx.onrender.com/api/v1`
- Socket.IO base: `https://kdo-gjrx.onrender.com` (path `/socket.io/`)
- Token: same as backend `MASTER_API_TOKEN`
