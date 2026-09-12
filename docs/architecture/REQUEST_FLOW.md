# Canonical Request Flow (implemented)

```
HTTP REQUEST
     │
     ▼
API ROUTER
     │
     ▼
AUTHENTICATION          ← SpeciesAuth (middleware)
     │
     ▼
REQUEST CONTEXT         ← core.request_context + middleware
     │
     ├─ Workspace
     └─ Principal
     │
     ▼
AUTHORIZATION           ← RequestContext.has_permission
     │
     ▼
GUARDIAN                ← core.pipeline._guardian_check
     │
     ▼
ORGANISM (workspace)
     │
     ├─ Memory
     ├─ Metabolism       ← core.pipeline._metabolism_check
     └─ Council          ← core.pipeline._council_advise
              │
              ▼
           Director      ← core.pipeline._director_decide
              │
              ▼
          Decision
         /        \
      BLOCK      ALLOW
                   │
                   ▼
            DOMAIN SERVICE (optional hook)
                   │
                   ▼
              JOB / OUTBOX   ← for publish / external effects
                   │
                   ▼
                 WORKER → PLUGIN → PLATFORM
                   │
                   ▼
                RESULT → Event / Analytics / Memory → Observability
```

## Entry point

`POST /api/v1/pipeline/action`

```json
{
  "action": "publish",
  "payload": { "content": "..." },
  "permission": "write",
  "enqueue": true
}
```

Requires `Authorization: Bearer <token>`.
Species operators may set `X-Workspace-ID`.
