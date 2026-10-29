
## KCPG Realtime/Event Bridge

For browser realtime through KCPG, configure the backend with:

- `PYTHON_SERVICE_TOKEN` — same secret configured on KCPG.
- `KCPG_INTERNAL_EVENTS_URL` — `https://<gateway>/api/v1/internal/events`.
- `KCPG_EVENT_SIGNING_SECRET` — same value as KCPG `PYTHON_EVENT_SIGNING_SECRET`.

The backend publishes authoritative UnifiedEventBus events to the authenticated KCPG event ingress. Browsers never connect directly to the Python event bus.
