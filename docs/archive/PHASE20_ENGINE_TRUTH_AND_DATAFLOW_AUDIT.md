# KDO Phase 20 — Engine Truth & End-to-End Dataflow Audit

## Scope
Audited the major operational engines and their wiring rather than relying on health/status claims:

- Director
- Condition Engine
- Meta Engine
- Council
- Autonomous Loop
- EventBus / SharedEventBus
- Guardian / recovery path
- Content Engine / Scheduler hands

## Findings fixed

### 1. Director observer fail-open health
An engine without `get_status`, `health_check`, or `get_stats` was previously reported as `healthy`. This could hide an incomplete/dead engine from the supervisory layer. It now reports `unknown` with `health_contract=missing`.

### 2. Legacy EventConsumer schema mismatch
`SharedEventBus` writes canonical events to `events.event_type`, while the legacy EventConsumer queried `shared_event_bus.event_name`. That consumer therefore could miss all cross-process events if activated. The consumer now reads the canonical `events` table and normalizes the event shape.

### 3. Condition Engine Council bypass
The lifecycle installs `dependency_registry._bind_condition_engine()` as the real Condition Engine executor. That executor previously executed content actions directly, bypassing the Council integration contract. The production binding now calls `deliberate_action()` first and fails closed if the Council gate is unavailable.

### 4. False semantic action implementations
Several Condition Engine actions were advertised with semantics that their executor did not actually perform:

- `change_posting_time` previously triggered a manual post instead of changing posting time. It now performs an actual bounded schedule shift when the scheduler exposes the configuration API.
- `change_content_type` previously called `reduce_content`, which is not a content-type change. It now reports `content_type_change_unavailable` until a real content-type-mix API exists.
- `investigate_decline` previously entered `safe_mode`, which is an intervention rather than an investigation. It now remains observational.

## End-to-end flow traced

### Content strategy
`Observation -> Condition Engine baseline/trend/anomaly -> Decision -> Council -> ActionPlan -> bound executor -> Content/Scheduler -> outcome`

### AI strategy
`strategy_recommendation -> Autonomous Loop intelligence -> strategy_execute -> RequestContext -> canonical action pipeline -> DecisionGate/outbox -> execution`

### Infrastructure recovery
`Meta snapshot -> anomaly/diagnosis -> incident -> recovery decision -> GuardianRecovery -> bounded executor -> health verification -> recovery outcome`

### Learning
`execution/verified outcome -> outcome.recorded -> Autonomous Loop -> intelligence learning signal`

## Remaining architectural limitations

1. Some engine capabilities are still intentionally advisory/observational rather than fully executable.
2. Live provider behavior, real Supabase RLS, Redis behavior, and actual deployment lifecycle still require staging certification.
3. The autonomous learning system is not claimed to be unrestricted reinforcement learning; adaptation remains bounded by policy and evidence.
4. The legacy EventConsumer remains a compatibility component; the canonical in-process path is UnifiedEventBus/AutonomousLoop.

## Verification

- Python compilation: PASS
- Automated suite: **81/81 PASS**
- No regressions introduced by Phase 20 changes.
