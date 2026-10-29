import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _routes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prefix = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter":
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = kw.value.value.rstrip("/")
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            if dec.func.attr.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"} and dec.args and isinstance(dec.args[0], ast.Constant):
                found.append((dec.func.attr.upper(), prefix + ("/" + str(dec.args[0].value).lstrip("/") if str(dec.args[0].value) else "")))
    return found


def test_sector_pack_routes_are_read_only():
    routes = _routes(ROOT / "routes" / "sector_pack_routes.py")
    assert ("GET", "/api/v1/sector-packs") in routes
    assert ("GET", "/api/v1/sector-packs/{pack_id}") in routes
    assert not any(method != "GET" for method, _path in routes)


def test_sector_pack_portfolio_is_exactly_the_certified_thirteen():
    source = (ROOT / "routes" / "sector_pack_routes.py").read_text(encoding="utf-8")
    expected = {
        "marketing_media", "commerce", "it_operations", "cybersecurity", "crm",
        "education", "healthcare", "finance", "supply_chain", "manufacturing",
        "energy", "government", "agriculture",
    }
    found = {line.split('"')[1] for line in source.splitlines() if line.startswith('    ("') and 'plugins.industries' in line}
    assert found == expected
