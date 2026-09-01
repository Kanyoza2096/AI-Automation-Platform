# Operator Runbook — AI-Automation-Platform

**Target Audience:** Platform operators, on-call engineers, DevOps teams

---

## Quick Reference

### Health Status Levels

| Status | Color | Meaning | Action |
|--------|-------|---------|--------|
| **NOMINAL** | 🟢 Green | All systems healthy | Monitor only |
| **DEGRADED** | 🟡 Yellow | Some issues but operational | Investigate soon |
| **STALE** | 🟠 Orange | No recent activity | Check if paused or stuck |
| **OFFLINE** | 🔴 Red | Engine unavailable | Emergency recovery |
| **CRITICAL** | 🚨 Critical | System failure imminent | Page on-call |

---

## Common Alerts & Response

### Alert: "content_engine DEGRADED - High error rate"

**Severity:** 🟡 Yellow (Warning)  
**Root Cause Signs:**
- Gemini API timeouts
- Redis connection failures
- Database query timeout

**Investigation Steps:**
1. Check ContentEngine logs: `docker logs <container> | grep ERROR`
2. Verify Gemini API status: Check https://status.google.com
3. Check Redis connection: `redis-cli ping`
4. Check Supabase status: Dashboard → Health

**Recovery Actions:**
```bash
# Option 1: Restart ContentEngine
docker restart content-engine

# Option 2: Clear Redis cache (if memory pressure)
redis-cli FLUSHDB

# Option 3: Scale up ContentEngine if CPU high
kubectl scale deployment content-engine --replicas=3
```

**Escalation:** If error rate stays > 50% for 15 min, escalate to platform team.

---

### Alert: "condition_engine OFFLINE - No health check response"

**Severity:** 🔴 Red (Critical)  
**Root Cause Signs:**
- Process crashed
- Deadlock in decision loop
- Out of memory

**Investigation Steps:**
1. Check if process running: `ps aux | grep condition_engine`
2. Check memory usage: `docker stats condition-engine`
3. Review recent logs: `docker logs --tail 100 condition-engine`

**Recovery Actions:**
```bash
# Step 1: Force restart
docker restart condition-engine

# Step 2: Wait for health check (30s)
sleep 30

# Step 3: Verify via API
curl http://localhost:8000/health/condition-engine

# Step 4: If still fails, rollback to previous version
docker stop condition-engine
docker run -d --name condition-engine image:previous-tag
```

**Escalation:** Page on-call if not recovered in 5 minutes.

---

### Alert: "meta_engine detected CASCADING FAILURE"

**Severity:** 🚨 Critical  
**What It Means:**  
One engine failure is propagating to dependent engines.

**Example Chain:**
```
content_engine OFFLINE
  ↓ (depends on)
ai_orchestrator OFFLINE
  ↓ (affects)
gemini API wrapper DEGRADED
  ↓ (cascades to)
brand_scheduler unable to generate content
```

**Investigation Steps:**
1. Identify root engine: `meta_engine.get_root_cause()`
2. Check dependency chain: `meta_engine.get_cascading_predictions()`
3. Review root engine logs

**Recovery Actions:**
```bash
# Step 1: Stabilize root engine FIRST
docker restart ai_orchestrator

# Step 2: Verify it's healthy
curl http://localhost:8000/health/ai_orchestrator

# Step 3: Let dependent engines recover
sleep 60

# Step 4: Monitor cascade resolution
watch -n 5 'curl http://localhost:8000/api/meta/status | jq .overall_status'
```

**Prevention:** Configure health check timeouts more conservatively.

---

### Alert: "incident_escalation - CRITICAL incident unresolved for 30 minutes"

**Severity:** 🚨 Critical  
**What It Means:**  
An incident has been in "INVESTIGATING" or "HEALING" state too long.

**Investigation Steps:**
1. Get incident details: `meta_engine.get_incidents()`
2. Review affected engines
3. Check attempted recovery actions
4. Review logs from 30 minutes ago

**Recovery Actions:**
```bash
# Option 1: Retry automatic recovery
meta_engine.initiate_recovery(incident_id)

# Option 2: Manual intervention
# - SSH to affected service
# - Check process status
# - Review resource usage (CPU, memory, disk)
# - Manually restart if needed

# Option 3: Circuit breaker reset
# Force circuit from OPEN → HALF_OPEN → CLOSED
meta_engine.reset_circuit(engine_name)
```

**Escalation:** If escalated incident + no progress in 5 min, page platform lead.

---

## Performance Baselines

### Normal Operating Ranges

**Event Processing:**
- Events/second: 50-200 (per 100 brands)
- Event latency (p95): < 100ms
- Event latency (p99): < 500ms

**Engine Health Scores:**
- NOMINAL: Health score > 75
- DEGRADED: Health score 25-75
- OFFLINE: Health score < 25

**Database:**
- Query latency (p95): < 50ms
- Connection pool exhaustion: < 5% of pool
- Dead connections: 0

**Memory:**
- Per engine: < 500MB (normal), < 800MB (degraded)
- Total system: < 8GB (normal), < 12GB (degraded)

**Incident Metrics:**
- Avg time to resolution: < 5 minutes
- Incident recurrence (same root cause): < 3 per day
- False positive rate: < 5%

---

## Dashboard Overview

### MetaEngine Status Dashboard

**Key Panels:**
1. **System Overview** — Overall status, health score, risk score
2. **Engine Status Grid** — 3×4 grid of all engines with status lights
3. **Recent Incidents** — Last 20 incidents with status
4. **Alert Timeline** — Events from last 2 hours
5. **Resource Utilization** — CPU, Memory, Disk trending
6. **Dependency Graph Visualization** — Interactive topology

### Condition Engine Learning Dashboard

**Key Panels:**
1. **Strategy Effectiveness** — Win rate per strategy
2. **Decision Confidence Trend** — Avg confidence over time
3. **Council Vote Acceptance Rate** — % approved by council
4. **Top Performing Strategies** — Ranked by engagement lift
5. **Learning Progress** — Entropy reduction over time

### ContentEngine Performance Dashboard

**Key Panels:**
1. **Generation Success Rate** — % posts rendered successfully
2. **Rendering Time Distribution** — p50, p95, p99 latencies
3. **Visual Strategy Effectiveness** — Card vs AI Image performance
4. **Theme Distribution** — What themes generated recently
5. **Error Rate by Renderer** — CardRenderer vs RenderQueue vs ImageEngine

---

## Maintenance Windows

### Rolling Updates (Zero-Downtime)

```bash
# 1. Update one replica at a time
for service in content-engine condition-engine meta-engine director; do
  # Stop replica 1
  docker stop ${service}-1
  
  # Wait for traffic to redirect
  sleep 30
  
  # Pull new image
  docker pull image:new-version
  
  # Start with new version
  docker run -d --name ${service}-1 image:new-version
  
  # Wait for health check
  sleep 30
done
```

### Database Migrations

```bash
# 1. Back up current state
supabse db dump --file backup_before_migration.sql

# 2. Run migration in dry-run mode
psql -h localhost -f migration.sql --echo-all

# 3. Review changes
# 4. Run actual migration
psql -h localhost -f migration.sql

# 5. Verify data integrity
select count(*) from autonomous_decisions where created_at > now() - interval '1 hour';
```

---

## Troubleshooting Decision Tree

```
Is overall system status RED?
  ├─ YES: Run `meta_engine.get_root_cause()` → Follow Alert recovery steps
  └─ NO: Is a specific engine RED?
         ├─ YES: Check that engine's logs, restart it
         └─ NO: Check event throughput via dashboard
              ├─ LOW: Investigate integration layer
              ├─ HIGH but alerts: Memory/CPU pressure?
              │       └─ Scale horizontally
              └─ NORMAL: False alarm, continue monitoring
```

---

## Contact & Escalation

**Tier 1 (Operations):** @ops-team in Slack  
**Tier 2 (Platform Engineers):** @platform-team  
**Tier 3 (Architecture):** @kanyoza2096  

**On-Call Schedule:** See PagerDuty rotation  
**Incident Severity SLOs:**
- 🔴 Red/Critical: 5 min response, 30 min resolution target
- 🟠 Orange/Stale: 15 min response, 2 hour resolution target
- 🟡 Yellow/Degraded: 30 min response, 4 hour resolution target
