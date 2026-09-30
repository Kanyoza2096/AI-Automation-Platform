#!/usr/bin/env python3
"""Static route contract audit: auth, runtime wiring, persistence, and mutation intent.

No application imports are performed. This is a deterministic architectural audit,
not proof of live runtime/provider/DB behavior.
"""
from __future__ import annotations
import ast, json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
ROUTES = [ROOT/'api', ROOT/'routes', ROOT/'plugins']
METHODS = {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'}
AUTH_TERMS = ('require_auth','require_species','require_admin','require_master','require_workspace_operator','require_workspace_owner','require_workspace_admin','require_auth_context','auth_identity','tenant_guard','assert_workspace_access','_auth_dep','_operator_dep','_owner_dep','_auth')
SERVICE_PREFIXES = ('services.', 'repositories.', 'core.')
MUTATING = {'POST','PUT','PATCH','DELETE'}

def dotted(n):
    if isinstance(n, ast.Name): return n.id
    if isinstance(n, ast.Attribute):
        b=dotted(n.value)
        return f'{b}.{n.attr}' if b else n.attr
    return None

def router_metadata(tree):
    prefix=''
    deps=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and dotted(n.value.func)=='APIRouter':
            for kw in n.value.keywords:
                if kw.arg=='prefix' and isinstance(kw.value,ast.Constant): prefix=kw.value.value or ''
                if kw.arg=='dependencies': deps.append(ast.unparse(kw.value))
    return prefix, deps

def audit_file(path):
    text=path.read_text(errors='ignore')
    tree=ast.parse(text)
    prefix, router_deps=router_metadata(tree)
    router_auth=any(t.lower() in ' '.join(router_deps).lower() for t in AUTH_TERMS)
    rows=[]
    for n in ast.walk(tree):
        if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        for d in n.decorator_list:
            if not isinstance(d,ast.Call) or not isinstance(d.func,ast.Attribute): continue
            if dotted(d.func.value)!='router' or d.func.attr.upper() not in METHODS or not d.args: continue
            router_auth=any(t.lower() in ' '.join(router_deps).lower() for t in AUTH_TERMS)
            if not isinstance(d.args[0],ast.Constant): continue
            method=d.func.attr.upper(); route=(prefix.rstrip('/')+'/'+str(d.args[0].value).lstrip('/')) if prefix else str(d.args[0].value)
            imports=[ast.unparse(x) for x in ast.walk(n) if isinstance(x,(ast.Import,ast.ImportFrom))]
            calls=[dotted(x.func) for x in ast.walk(n) if isinstance(x,ast.Call) and dotted(x.func)]
            src=ast.get_source_segment(text,n) or ''
            low=src.lower()
            decorator_text=' '.join(ast.unparse(dec) for dec in n.decorator_list)
            route_auth=any(t.lower() in (low + ' ' + decorator_text.lower()) for t in AUTH_TERMS)
            auth=route_auth or router_auth or any(
                any(term.lower() in str(kw.value).lower() for term in AUTH_TERMS)
                for dec in n.decorator_list
                if isinstance(dec, ast.Call)
                for kw in dec.keywords
                if kw.arg == 'dependencies'
            )
            service=any(any((' '+p) in (' '+i) or i.startswith(p) for p in SERVICE_PREFIXES) for i in imports)
            service = service or any(any((c or '').startswith(p) for p in SERVICE_PREFIXES) for c in calls)
            db=any(k in low for k in ('supabase','client.table','repository','session','execute()','commit('))
            container=('request.app.state' in src or 'container' in low)
            external=any(k in low for k in ('http://','https://','graph.facebook','sendgrid','webhook'))
            markers=[m for m in ('placeholder','not implemented','todo') if m in low]
            return_shape='unknown'
            if 'raise HTTPException' in src: return_shape='http_exception'
            elif 'return _ok' in src or 'return {' in src: return_shape='structured'
            elif 'return ' in src: return_shape='value'
            rows.append({
                'method':method,'path':route,'module':str(path.relative_to(ROOT)),'handler':n.name,'line':n.lineno,
                'auth_signal':auth,'router_auth_signal':router_auth,'service_signal':service,'database_signal':db,'container_signal':container,
                'external_signal':external,'markers':markers,'return_shape':return_shape,
                'mutation':method in MUTATING,
            })
            break
    return rows

def main():
    rows=[]; errors=[]
    for root in ROUTES:
        if not root.exists(): continue
        for p in root.rglob('*.py'):
            try: rows.extend(audit_file(p))
            except SyntaxError as e: errors.append((str(p.relative_to(ROOT)),str(e)))
    rows.sort(key=lambda r:(r['path'],r['method'],r['module']))
    dup=Counter((r['method'],r['path']) for r in rows)
    duplicate_pairs=[list(k)+[v] for k,v in dup.items() if v>1]
    summary={
      'route_count':len(rows),'parse_errors':errors,'duplicate_pairs':duplicate_pairs,
      'auth_signal_count':sum(r['auth_signal'] for r in rows),
      'service_signal_count':sum(r['service_signal'] for r in rows),
      'database_signal_count':sum(r['database_signal'] for r in rows),
      'external_signal_count':sum(r['external_signal'] for r in rows),
      'mutation_routes':sum(r['mutation'] for r in rows),
      'mutation_without_service_or_db':[r for r in rows if r['mutation'] and not (r['service_signal'] or r['database_signal'] or r['container_signal'])],
      'protected_mutations_without_auth':[r for r in rows if r['mutation'] and not r['auth_signal']],
      'placeholder_routes':[r for r in rows if r['markers']],
      'routes':rows,
    }
    print(json.dumps(summary,indent=2))
    return 1 if errors or duplicate_pairs else 0

if __name__=='__main__': raise SystemExit(main())
