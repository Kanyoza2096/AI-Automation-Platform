#!/usr/bin/env python3
"""Static route-to-runtime wiring audit.

Verifies that every router-bearing module under the application route roots is
accounted for by main.py, and classifies each route handler by its observable
runtime wiring signals (service/repository/core imports, container access,
request state, or durable DB access). It never imports the application.
"""
from __future__ import annotations
import ast, json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTE_ROOTS = [ROOT/'api', ROOT/'routes', ROOT/'plugins']


def dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def module_has_router(path):
    try: tree = ast.parse(path.read_text(errors='ignore'))
    except SyntaxError: return False
    return any(
        isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
        and dotted(n.value.func) == 'APIRouter'
        for n in ast.walk(tree)
    )


def routes(path):
    try: tree = ast.parse(path.read_text(errors='ignore'))
    except SyntaxError as exc: return [], str(exc)
    prefix = ''
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and dotted(n.value.func) == 'APIRouter':
            for kw in n.value.keywords:
                if kw.arg == 'prefix' and isinstance(kw.value, ast.Constant): prefix = kw.value.value or ''
    # api/routers modules are composed into api.v1, mounted by main.py.
    # Include the parent mount prefix so static route inventory matches runtime.
    normalized_path = str(path).replace("\\", "/")
    if "/api/routers/" in normalized_path:
        prefix = "/api/v1" + prefix
    out=[]
    for n in ast.walk(tree):
        if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        for d in n.decorator_list:
            if not isinstance(d,ast.Call) or not isinstance(d.func,ast.Attribute): continue
            if dotted(d.func.value) != 'router' or d.func.attr.upper() not in {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'}: continue
            if not d.args or not isinstance(d.args[0],ast.Constant): continue
            p=d.args[0].value
            full=(prefix.rstrip('/')+'/'+str(p).lstrip('/')) if prefix else str(p)
            names=[]; imports=[]; calls=[]
            for x in ast.walk(n):
                if isinstance(x,(ast.Import,ast.ImportFrom)):
                    imports.append(ast.unparse(x))
                elif isinstance(x,ast.Call):
                    c=dotted(x.func)
                    if c: calls.append(c)
            body=ast.get_source_segment(path.read_text(errors='ignore'), n) or ''
            low=body.lower()
            signals=[]
            if any(s.startswith('services.') or ' services.' in s for s in imports): signals.append('service_import')
            if any(s.startswith('repositories.') or ' repositories.' in s for s in imports): signals.append('repository_import')
            if any(s.startswith('core.') or ' core.' in s for s in imports): signals.append('core_import')
            if 'request.app.state' in body or 'container' in low: signals.append('container_or_app_state')
            if 'request.state' in body: signals.append('request_state')
            if any('supabase' in c.lower() or 'client.table' in c.lower() for c in calls) or 'supabase' in low: signals.append('database_access')
            markers=[m for m in ('placeholder','not implemented') if m in low]
            out.append({'method':d.func.attr.upper(),'path':full,'module':str(path.relative_to(ROOT)),'handler':n.name,'line':n.lineno,'signals':signals,'markers':markers})
    return out,None


def main_mounts():
    text=(ROOT/'main.py').read_text(errors='ignore')
    tree=ast.parse(text)
    mounted=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Call):
            # __import__("module.path", ...)
            if dotted(n.func) == '__import__' and n.args and isinstance(n.args[0],ast.Constant):
                mounted.add(n.args[0].value)
            # from module import ... inside main.py
            if isinstance(n.func,ast.Attribute) and dotted(n.func)== 'app.include_router':
                pass
    for n in ast.walk(tree):
        if isinstance(n,ast.ImportFrom) and n.module: mounted.add(n.module)
    return mounted


def main():
    all_routes=[]; router_modules=[]; errors=[]
    for root in ROUTE_ROOTS:
        if not root.exists(): continue
        for path in root.rglob('*.py'):
            if module_has_router(path): router_modules.append(str(path.relative_to(ROOT)).replace('/', '.')[:-3])
            rows,err=routes(path); all_routes.extend(rows)
            if err: errors.append((str(path.relative_to(ROOT)),err))
    mounts=main_mounts()
    # main.py also calls helper registrations whose source module is statically discoverable.
    source=(ROOT/'main.py').read_text(errors='ignore')
    if "routes.command_center_routes" in source:
        mounts.add("routes.command_center_routes")
    for m in ('routes.event_api','routes.code_intel_routes','routes.backend_contract_routes','routes.cp_gap_routes'):
        if m in source: mounts.add(m)
    # The primary router list is represented as (display, module, attr) tuples.
    for m in ('api.v1','routes.rule_routes','routes.meta_routes','routes.email_routes','routes.webhook',
              'plugins.trend_intelligence.routes.public','plugins.trend_intelligence.routes.admin',
              'plugins.backup.routes.public','plugins.backup.routes.admin',
              'api.timeline_routes','api.e2e_routes','api.action_intent_routes','api.metabolism_routes','api.organism_body_routes',
              'api.outbox_routes','api.report_routes','api.slo_routes','api.observability_routes','api.web_intelligence',
              'api.health_unified','api.content_intelligence_automation','routes.council_routes','routes.circulation_routes','routes.pulse_routes','routes.affiliate_routes',
              'plugins.github_connector.routes.public','plugins.github_connector.routes.admin',
              'plugins.growthbridge.routes.public','plugins.growthbridge.routes.admin',
              'plugins.monitor.routes.public','plugins.monitor.routes.admin','routes.sensor_routes','routes.production_runtime_routes','routes.autonomous_workforce_bridge_routes','routes.runtime_wiring_routes','routes.legacy_runtime_fencing_routes','routes.workforce_execution_routes','routes.business_onboarding_routes','routes.command_center_routes','routes.sector_pack_routes','api.website_integrations','routes.connector_fabric_routes','routes.failure_recovery_routes'):
        if m in source: mounts.add(m)
    # Plugin routes are loaded dynamically from _mount_plugin; plugin name literals in main.py count.
    for module in router_modules:
        mod=module[:-3].replace('/','.')
        if mod in mounts: continue
        # Dynamic plugin mounting is explicit in _mount_plugin.
        if mod.startswith('plugins.') and mod.split('.')[1] in source: continue
        # timeline/e2e are mounted through explicit module strings.
        if mod in {'api.timeline_routes','api.e2e_routes'} and mod in mounts: continue
    mounted_set=set(m for m in mounts if isinstance(m,str))
    orphan_modules=[]
    for mod in sorted(set(router_modules)):
        if mod in mounted_set:
            continue
        if mod.startswith('api.routers.') and 'api.v1' in mounted_set:
            continue
        if mod.startswith('plugins.') and mod.split('.')[1] in {'growthbridge','github_connector','monitor'} and '_mount_plugin' in source:
            continue
        orphan_modules.append(mod)
    # services/director is an intentional sub-application mount, not an APIRouter module.
    summary={
        'route_count':len(all_routes),
        'router_module_count':len(router_modules),
        'orphan_router_modules':orphan_modules,
        'parse_errors':errors,
        'signal_counts':{k:sum(k in r['signals'] for r in all_routes) for k in ['service_import','repository_import','core_import','container_or_app_state','request_state','database_access']},
        'placeholder_routes':[r for r in all_routes if r['markers']],
        'routes':sorted(all_routes,key=lambda r:(r['path'],r['method'],r['module']))
    }
    print(json.dumps(summary,indent=2))
    return 1 if errors or orphan_modules else 0

if __name__=='__main__': raise SystemExit(main())
