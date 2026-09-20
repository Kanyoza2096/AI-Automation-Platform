"""Static audit for consequential business-action execution boundaries.

The audit is intentionally conservative: business code may read/write its own
state, but outbound provider mutations must terminate in an explicitly
recognized execution boundary. Generic plugin execution is not one.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    detail: str
    severity: str


ALLOWED_DIRECT_PROVIDER_FILES = {
    "services/action_kernel.py",
    "services/external_action_executor.py",
    "services/customer_communication.py",  # transport only; kernel-owned caller
    "services/publish_outbox.py",           # durable provider worker boundary
    "services/publish_orchestration.py",
    "services/whatsApp.py",                # legacy transport; audited separately
}


def _files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        if any(part in {"tests", "__pycache__"} for part in p.parts):
            continue
        yield p


def audit(root: str | Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name = node.func.attr
            # Generic plugin dispatch is an execution-plane bypass unless the
            # caller itself is the governed Action Kernel adapter.
            if name == "execute" and isinstance(node.func.value, ast.Name) and node.func.value.id == "plugin":
                # command_executor is allowed to dispatch explicitly declared
                # read_only commands; consequential commands are routed to the
                # Action Kernel before this branch can execute.
                if rel not in {"services/action_kernel.py", "services/command_executor.py"}:
                    findings.append(Finding(rel, node.lineno, "plugin_execute", "direct plugin.execute() call", "high"))
            # Direct connector/provider mutation from arbitrary business code.
            if name in {"post", "put", "patch", "delete"} and isinstance(node.func.value, ast.Attribute):
                owner = node.func.value
                if owner.attr in {"_session", "_http", "session"} and rel not in ALLOWED_DIRECT_PROVIDER_FILES:
                    findings.append(Finding(rel, node.lineno, "raw_provider_http", f"direct {owner.attr}.{name}() call", "high"))
    return findings


def to_dict(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
