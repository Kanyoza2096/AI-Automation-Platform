"""Sector boundary certification tests."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECTORS = ["crm", "school_mis", "education", "hospital", "church_mis", "commerce"]
FORBIDDEN_EXTERNAL_MODULES = {"facebook", "instagram", "twitter", "linkedin", "whatsapp"}


def test_sector_plugins_resolve_workspace_through_platform_boundary():
    for sector in SECTORS:
        source = (ROOT / "plugins" / "industries" / sector / "plugin.py").read_text()
        assert "resolve_workspace(" in source
        assert "workspace_id = self.resolve_workspace(workspace_id)" in source


def test_sector_plugins_strip_client_workspace_before_domain_execution():
    for sector in SECTORS:
        source = (ROOT / "plugins" / "industries" / sector / "plugin.py").read_text()
        assert 'if k != "workspace_id"' in source


def test_sector_code_has_no_direct_social_provider_imports():
    for sector in SECTORS:
        for path in (ROOT / "plugins" / "industries" / sector).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0].lower() for a in node.names]
                    assert not (set(names) & FORBIDDEN_EXTERNAL_MODULES), path
                elif isinstance(node, ast.ImportFrom):
                    top = (node.module or "").split(".")[0].lower()
                    assert top not in FORBIDDEN_EXTERNAL_MODULES, path


def test_sector_external_messaging_is_connector_deferred():
    for sector in ("crm", "school_mis", "education", "hospital"):
        source = (ROOT / "plugins" / "industries" / sector / "commands.py").read_text()
        assert "will be implemented in the connector layer" in source
