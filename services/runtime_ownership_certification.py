"""Runtime ownership certification for the canonical KDO execution plane.

This is deliberately an audit/certification helper, not another execution
engine. It verifies source-level invariants that protect the Action Kernel
from legacy command surfaces and records the canonical route anchors.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import hashlib
import json
from typing import Any


CANONICAL_ROUTE_ANCHORS = {
    "api_v1": "api/v1.py",
    "action_intent_bridge": "api/action_intent_routes.py",
    "connector_fabric": "routes/connector_fabric_routes.py",
    "production_runtime": "routes/production_runtime_routes.py",
    "legacy_authority_fence": "routes/legacy_runtime_fencing_routes.py",
    "backend_contract": "routes/backend_contract_routes.py",
}


@dataclass(frozen=True)
class RuntimeOwnershipFinding:
    check: str
    status: str
    detail: str


def _has_call(source: str, module: str, name: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != name:
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id == module:
                return True
    return False


def certify(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    executor = root / "services" / "command_executor.py"
    main = root / "main.py"
    findings: list[RuntimeOwnershipFinding] = []

    executor_text = executor.read_text(encoding="utf-8")
    main_text = main.read_text(encoding="utf-8")

    findings.append(RuntimeOwnershipFinding(
        "legacy_executor_no_post_transport",
        "PASS" if not _has_call(executor_text, "requests", "post") else "FAIL",
        "CommandExecutor contains no requests.post mutation transport.",
    ))
    findings.append(RuntimeOwnershipFinding(
        "legacy_executor_declares_non_authoritative",
        "PASS" if "legacy_executor\": \"non_authoritative\"" in executor_text else "FAIL",
        "Consequential built-in commands identify Action Kernel as execution authority.",
    ))
    findings.append(RuntimeOwnershipFinding(
        "canonical_action_intent_bridge_mounted",
        "PASS" if "api.action_intent_routes" in main_text else "FAIL",
        "The canonical Action Intent bridge is mounted by the application composition root.",
    ))

    missing = []
    for name, rel in CANONICAL_ROUTE_ANCHORS.items():
        if not (root / rel).exists():
            missing.append(f"{name}:{rel}")
    findings.append(RuntimeOwnershipFinding(
        "canonical_route_anchors_present",
        "PASS" if not missing else "FAIL",
        "All canonical route anchors are present." if not missing else "Missing: " + ", ".join(missing),
    ))

    digest_material = json.dumps([asdict(x) for x in findings], sort_keys=True, separators=(",", ":"))
    return {
        "version": "r4-runtime-ownership-v1",
        "canonical_execution": "ActionIntent -> Governance -> Authorization -> Action Kernel -> Connector -> Provider -> Receipt -> Verification -> Reconciliation",
        "findings": [asdict(x) for x in findings],
        "status": "PASS" if all(x.status == "PASS" for x in findings) else "FAIL",
        "fingerprint": hashlib.sha256(digest_material.encode()).hexdigest(),
    }
