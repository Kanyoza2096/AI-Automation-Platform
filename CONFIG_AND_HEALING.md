# Config + healing — no longer decoration-only

## 1. Condition / Meta self-heal

| Layer | Behavior |
|-------|----------|
| Condition | Real executor for content boost/reduce/safe_mode; other actions return explicit `no_handler` |
| Meta | `meta_recovery_bindings` registers director restart, condition reset, bus dead-letter clear, loop restart, director hand rebind, plugin ensure |
| Init | `init_meta_engine` still registers generic restart/reset where engines expose them |

Recovery is **real process-level** (stop/start, rebind, clear queues) — not magic infrastructure resurrection without handlers.

## 2. Dreams retrain the mind

Dreams now collect from:
1. Council memory
2. In-process `decision_gate` decisions
3. Bus `post.published` / `content.failed` outcomes

Then:
- Update persona `update_reliability(success_rate)` when available
- Nudge council `approval_threshold` from outcomes
- Publish `dream.insight` on the nerve
- `run_once()` for Director `run_learning_cycle`

## 3. Hardcoded → DB (migration 016)

| Component | Table / source |
|-----------|----------------|
| Plan limits | `plans` (+ runtime_config → metabolism) |
| Personas | `persona_catalog` + `workspace_config.enabled_personas` |
| Pulse thresholds | `workspace_config.pulse_*` |
| Dream hours | `workspace_config.dream_hours_start/end` + timezone |
| Director capabilities | `workspace_config.enabled_capabilities` |
| Brand keywords / topics | `brand_config` |

Code defaults remain if DB empty so offline boot still works.

### Apply

```sql
-- run migrations/016_runtime_config.sql
```

### Example workspace_config row

```sql
INSERT INTO workspace_config (workspace_id, plan, timezone, dream_hours_start, dream_hours_end, enabled_personas, industry_organs)
VALUES (
  'ws_church',
  'pro',
  'Africa/Blantyre',
  2, 5,
  '["community_elder","theologian","customer_voice","business_owner"]',
  '["church_mis"]'
);
```

### Example brand_config

```sql
INSERT INTO brand_config (brand_id, workspace_id, keywords, fallback_topics)
VALUES (
  'brand_1',
  'ws_church',
  '["faith","community","hope"]',
  '["Sunday reflection","Youth outreach","Testimony"]'
);
```


## Content topic resolution (wired)

Priority in `TopicSelector` / `ContentEngine`:

1. Explicit `topic=` argument  
2. Brand `custom_topics`  
3. **`brand_config.fallback_topics` + keywords** (Supabase)  
4. Knowledge Base entries  
5. Emergency `FALLBACK_POSTS` only if everything else fails  

`ContentEngine._build_prompt` appends brand keywords for Gemini.
