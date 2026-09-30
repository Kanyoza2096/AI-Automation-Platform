from pathlib import Path
import ast


REQUIRED = {
    ("GET", "/engines"), ("GET", "/tasks"),
    ("GET", "/health/live"), ("GET", "/diagnoses"), ("GET", "/dependency-graph"),
    ("POST", "/diagnostics"), ("GET", "/healing-history"),
    ("GET", "/system-health"),
    ("GET", "/services"), ("GET", "/topology"), ("GET", "/events"),
    ("GET", "/integrations/status"),
    ("GET", "/predictions/forecast"), ("GET", "/predictions/confidence"),
    ("GET", "/competitors/list"), ("GET", "/competitors/gaps"),
    ("GET", "/competitors/opportunities"), ("GET", "/quality/history"),
    ("GET", "/posts/{slug}"),
    ("GET", "/decisions"), ("GET", "/decisions/{decision_id}"),
    ("GET", "/decisions/{decision_id}/votes"), ("GET", "/decisions/{decision_id}/risk"),
    ("GET", "/decisions/{decision_id}/dissent"),
    ("GET", "/decisions/{decision_id}/execution-evidence"),
    ("GET", "/control-plane/action-intents/{intent_id}/evidence"),
}


def test_backend_contract_routes_are_declared():
    tree = ast.parse(Path("routes/backend_contract_routes.py").read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr not in {"get", "post"} or not dec.args:
                continue
            path = dec.args[0].value
            found.add((dec.func.attr.upper(), path))
    assert REQUIRED <= found
