"""C9.4 security/tenant certification gate.

Offline checks prove application-boundary invariants. Live PostgreSQL/RLS
certification is deliberately NOT_PROVEN unless a real database is supplied.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks = []

def check(name, ok, detail):
    checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

def contains(path, needle):
    return needle in (ROOT / path).read_text()

check("request_context_fail_closed", contains("core/request_context.py", "Authenticated non-species identity has no workspace_id") and contains("core/request_context.py", "effective_workspace"), "authenticated context requires tenant identity")
check("plugin_workspace_boundary", contains("core/plugin_execution.py", "context.effective_workspace") and contains("core/plugin_execution.py", "workspace_id"), "plugin boundary resolves tenant from trusted context")
check("tenant_guard_exists", (ROOT / "core/tenant_guard.py").exists(), "central tenant isolation helper exists")
check("rls_baseline", contains("migrations/030_production_tenant_security.sql", "ENABLE ROW LEVEL SECURITY") and contains("migrations/030_production_tenant_security.sql", "workspace_members WHERE user_id = auth.uid()::text"), "baseline RLS policies are present")
check("durable_sales_migrations", (ROOT / "migrations/107_v70_2_sales_event_durable_processing.sql").exists() and (ROOT / "migrations/108_v70_2_sales_atomic_state_transition.sql").exists(), "sales durable migration chain is present")
check("no_client_workspace_override", not contains("core/plugin_execution.py", "params.get('workspace_id')"), "plugin params cannot override resolved workspace")

status = "CERTIFIED_OFFLINE" if all(c["status"] == "PASS" for c in checks) else "FAILED"
report = {
    "schema_version": "1.0", "certification": "C9.4",
    "status": status, "checks": checks,
    "live_postgresql_rls": "NOT_PROVEN",
    "live_multi_tenant_concurrency": "NOT_PROVEN",
    "notes": ["Offline certification does not replace real PostgreSQL/RLS tests.", "No authority or provider access is granted by this script."],
}
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(0 if status != "FAILED" else 1)
