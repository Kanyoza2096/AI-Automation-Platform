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
