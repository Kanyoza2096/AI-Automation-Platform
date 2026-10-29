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

must_contain('plugins/github_connector/routes/admin.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/rule_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/meta_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/pulse_routes.py', 'dependencies=[Depends(require_auth_context)]')
must_contain('routes/circulation_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('routes/cp_gap_routes.py', 'dependencies=[Depends(require_species_privileges)]')
must_contain('api/web_intelligence.py', 'dependencies=[Depends(require_auth_context)]')
must_contain('plugins/growthbridge/routes/public.py', 'Depends(require_auth_context)')
must_not_contain('services/director.py', 'DISABLE_DIRECTOR_AUTH')
must_not_contain('plugins/trend_intelligence/routes/admin.py', 'admin API is UNAUTHENTICATED')
must_not_contain('plugins/growthbridge/routes/public.py', 'growthbridge_talent").select("*")')

failed = [msg for ok, msg in checks if not ok]
for ok, msg in checks:
    print(('PASS' if ok else 'FAIL') + ': ' + msg)
if failed:
    raise SystemExit(1)
print(f"Security surface checks passed ({len(checks)} checks).")
