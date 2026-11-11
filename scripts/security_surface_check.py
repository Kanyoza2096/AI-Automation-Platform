from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks = []

def must_contain(path, needle):
    text = (ROOT / path).read_text()
    ok = needle in text
    checks.append((ok, f"{path}: contains {needle}"))

def must_not_contain(path, needle):
    text = (ROOT / path).read_text()
    ok = needle not in text
    checks.append((ok, f"{path}: does not contain {needle}"))

def must_not_exist(path):
    ok = not (ROOT / path).exists()
    checks.append((ok, f"{path}: is absent"))

must_contain('plugins/github_connector/routes/admin.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/rule_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/meta_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/pulse_routes.py', 'dependencies=[Depends(require_auth_context)]')
must_contain('routes/circulation_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/cp_gap_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('api/web_intelligence.py', 'dependencies=[Depends(require_auth_context)]')
must_not_contain('services/director.py', 'DISABLE_DIRECTOR_AUTH')
must_contain('services/health_monitor_service.py', 'class HealthMonitorService')
must_contain('main.py', 'routes.code_intel_routes')
for retired_path in ('plugins/monitor/plugin.py', 'plugins/backup/plugin.py', 'plugins/trend_intelligence/plugin.py', 'plugins/growthbridge/plugin.py', 'routes/affiliate_routes.py', 'services/affiliate_service.py'):
    must_not_exist(retired_path)

failed = [msg for ok, msg in checks if not ok]
for ok, msg in checks:
    print(('PASS' if ok else 'FAIL') + ': ' + msg)
if failed:
    raise SystemExit(1)
print(f"Security surface checks passed ({len(checks)} checks).")
