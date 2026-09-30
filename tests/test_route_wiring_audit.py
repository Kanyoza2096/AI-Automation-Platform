from pathlib import Path
import ast
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]


def _router_prefix(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter":
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        return kw.value.value
    return ""


def _routes(path):
    tree = ast.parse(path.read_text())
    prefix = _router_prefix(tree).rstrip("/")
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            if dec.func.attr.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path_value = dec.args[0].value
            full = (prefix + "/" + path_value.lstrip("/")) if prefix else path_value
            out.append((dec.func.attr.upper(), full, path.name, node.name))
    return out


def test_route_inventory_has_no_duplicate_method_paths():
    routes = []
    for root in (ROOT / "api", ROOT / "routes", ROOT / "plugins"):
        routes.extend(r for p in root.rglob("*.py") for r in _routes(p))
    grouped = defaultdict(list)
    for row in routes:
        grouped[row[:2]].append(row)
    duplicates = {k: v for k, v in grouped.items() if len(v) > 1}
    assert not duplicates, duplicates


def test_requested_backend_contract_is_present_in_authoritative_or_existing_routes():
    routes = []
    for root in (ROOT / "api", ROOT / "routes", ROOT / "plugins"):
        routes.extend(r for p in root.rglob("*.py") for r in _routes(p))
    keys = {(m, p) for m, p, _, _ in routes}
    required = {
        ("GET", "/api/v1/engines"), ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/health/live"), ("GET", "/api/v1/diagnoses"),
        ("GET", "/api/v1/dependency-graph"), ("POST", "/api/v1/diagnostics"),
        ("GET", "/api/v1/healing-history"), ("GET", "/api/v1/system-health"),
        ("GET", "/api/v1/alerts"), ("GET", "/api/v1/health/deep"),
        ("GET", "/api/v1/keys"), ("GET", "/api/v1/rate-limits"),
        ("GET", "/api/v1/services"), ("GET", "/api/v1/topology"),
        ("GET", "/api/v1/plugins"), ("GET", "/api/v1/integrations"),
        ("GET", "/api/v1/integrations/status"), ("GET", "/api/v1/events"),
        ("GET", "/api/v1/logs/recent"), ("GET", "/api/v1/audit-logs"),
        ("GET", "/api/v1/predictions/forecast"), ("GET", "/api/v1/predictions/confidence"),
        ("GET", "/api/v1/competitors/list"), ("GET", "/api/v1/competitors/gaps"),
        ("GET", "/api/v1/competitors/opportunities"), ("GET", "/api/v1/quality/history"),
        ("GET", "/api/v1/posts"), ("GET", "/api/v1/posts/{slug}"),
        ("GET", "/api/v1/workflow/history"), ("GET", "/api/v1/decisions"),
        ("GET", "/api/v1/decisions/{decision_id}"),
        ("GET", "/api/v1/decisions/{decision_id}/votes"),
        ("GET", "/api/v1/decisions/{decision_id}/risk"),
        ("GET", "/api/v1/decisions/{decision_id}/dissent"),
        ("GET", "/api/v1/organism/body"), ("GET", "/api/v1/organism/mind"),
        ("GET", "/api/v1/organism/vitals"), ("GET", "/api/v1/slo/platform"),
        ("GET", "/api/v1/monitoring/stats"), ("GET", "/api/v1/metabolism/status"),
        ("GET", "/api/v1/metabolism/plans"), ("GET", "/api/v1/timeline/correlation/{correlation_id}"),
        ("GET", "/api/v1/timeline/decision/{decision_id}"),
        ("GET", "/api/v1/timeline/workspace/{workspace_id}/decisions"),
    }
    # Timeline router is mounted by main.py with an /api/v1 prefix.
    timeline = {_route for _route in _routes(ROOT / "api" / "timeline_routes.py")}
    timeline_keys = {(m, "/api/v1" + p) for m, p, _, _ in timeline}
    assert required <= (keys | timeline_keys)


def test_all_router_modules_are_accounted_for_by_main_bootstrap():
    """Every APIRouter module must be mounted or intentionally dynamically registered."""
    import ast
    import scripts.audit_route_wiring as audit

    router_modules = []
    for root in audit.ROUTE_ROOTS:
        for path in root.rglob("*.py"):
            if audit.module_has_router(path):
                router_modules.append(str(path.relative_to(audit.ROOT)).replace("/", ".")[:-3])

    mounts = audit.main_mounts()
    source = (audit.ROOT / "main.py").read_text(errors="ignore")
    for m in (
        "routes.event_api", "routes.code_intel_routes", "routes.backend_contract_routes", "routes.cp_gap_routes",
        "api.v1", "routes.rule_routes", "routes.meta_routes", "routes.email_routes", "routes.webhook",
        "plugins.trend_intelligence.routes.public", "plugins.trend_intelligence.routes.admin",
        "plugins.backup.routes.public", "plugins.backup.routes.admin",
        "api.timeline_routes", "api.e2e_routes", "api.action_intent_routes", "api.metabolism_routes", "api.organism_body_routes",
        "api.outbox_routes", "api.report_routes", "api.slo_routes", "api.observability_routes", "api.web_intelligence",
        "api.health_unified", "api.content_intelligence_automation", "routes.council_routes", "routes.circulation_routes", "routes.pulse_routes",
        "routes.sensor_routes", "routes.production_runtime_routes", "routes.autonomous_workforce_bridge_routes", "routes.runtime_wiring_routes",
        "routes.legacy_runtime_fencing_routes", "routes.workforce_execution_routes", "routes.business_onboarding_routes", "routes.command_center_routes",
        "api.website_integrations",
    ):
        if m in source:
            mounts.add(m)

    orphaned = []
    for mod in sorted(set(router_modules)):
        if mod in mounts:
            continue
        if mod.startswith("plugins.") and mod.split(".")[1] in {"growthbridge", "github_connector", "monitor"} and "_mount_plugin" in source:
            continue
        orphaned.append(mod)
    assert orphaned == []


def test_route_wiring_audit_script_has_no_parse_errors():
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "scripts/audit_route_wiring.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["parse_errors"] == []
    assert report["orphan_router_modules"] == []
    assert report["route_count"] == 385
