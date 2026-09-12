#!/usr/bin/env python3
"""Static route inventory and wiring audit for Kanyoza.

Scans API/route/plugin modules without importing the application, so the audit can
run even when optional runtime dependencies are unavailable.
"""
from __future__ import annotations
import ast, json
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCAN_ROOTS=[ROOT/'api', ROOT/'routes', ROOT/'plugins']

def const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value,str): return node.value
    return None

def router_prefixes(tree):
    out={}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(isinstance(t,ast.Name) and t.id=='router' for t in n.targets):
            if isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id=='APIRouter':
                for kw in n.value.keywords:
                    if kw.arg=='prefix':
                        out['router']=const(kw.value) or ''
    return out.get('router','')

def route_defs(path):
    text=path.read_text(errors='ignore')
    try: tree=ast.parse(text)
    except SyntaxError as e: return [], f"syntax:{e}"
    prefix=router_prefixes(tree); rows=[]
    for n in ast.walk(tree):
        if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        for d in n.decorator_list:
            if not (isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute)): continue
            if not (isinstance(d.func.value,ast.Name) and d.func.value.id=='router'): continue
            method=d.func.attr.upper()
            if method not in {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'}: continue
            p=const(d.args[0]) if d.args else None
            if p is None: continue
            full=(prefix.rstrip('/')+'/'+p.lstrip('/')) if p else (prefix or '/')
            deps=' '.join(ast.unparse(x) for x in d.keywords if x.arg=='dependencies')
            body=ast.get_source_segment(text,n) or ''
            markers=[]
            low=body.lower()
            for m in ('placeholder','not implemented','source: "none"','source="none"','return []','return {}'):
                if m in low: markers.append(m)
            rows.append({'method':method,'path':full,'module':str(path.relative_to(ROOT)),'handler':n.name,'line':n.lineno,'markers':markers,'dependencies':deps})
    return rows,None

def main():
    rows=[]; errors=[]
    for root in SCAN_ROOTS:
        if not root.exists(): continue
        for path in root.rglob('*.py'):
            r,e=route_defs(path); rows.extend(r)
            if e: errors.append((str(path.relative_to(ROOT)),e))
    key=defaultdict(list)
    for r in rows: key[(r['method'],r['path'])].append(r)
    duplicates={f'{m} {p}':v for (m,p),v in key.items() if len(v)>1}
    marked=[r for r in rows if r['markers']]
    report={'route_count':len(rows),'duplicate_count':len(duplicates),'duplicates':duplicates,'marker_count':len(marked),'marked_routes':marked,'parse_errors':errors,'routes':sorted(rows,key=lambda x:(x['path'],x['method'],x['module']))}
    print(json.dumps(report,indent=2,default=str))
    return 1 if errors else 0
if __name__=='__main__': raise SystemExit(main())
