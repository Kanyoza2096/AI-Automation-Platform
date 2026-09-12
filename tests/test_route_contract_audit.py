import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _audit():
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_route_contracts.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(p.stdout)


def test_route_contract_inventory_is_structurally_clean():
    report = _audit()
    assert report["route_count"] == 357
    assert report["parse_errors"] == []
    assert report["duplicate_pairs"] == []


def test_mutation_auth_exceptions_are_explicitly_bounded():
    report = _audit()
    actual = {(r["method"], r["path"]) for r in report["protected_mutations_without_auth"]}
    expected = {
        ("POST", "/api/v1/github/webhook"),
        ("POST", "/api/v1/growthbridge/applications"),
        ("POST", "/api/v1/growthbridge/contact"),
        ("POST", "/api/v1/growthbridge/partners/apply"),
        ("POST", "/api/v1/growthbridge/partnerships"),
        ("POST", "/api/v1/growthbridge/talent/apply"),
        ("POST", "/api/v1/metabolism/webhook/stripe"),
        ("POST", "/api/v1/webhooks/linkedin"),
        ("POST", "/api/v1/webhooks/twitter"),
        ("POST", "/webhook"),
            ("POST", "/webhook/whatsapp"),
    }
    assert actual == expected
