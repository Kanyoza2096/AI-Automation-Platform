"""Sector-pack contract for Kanyoza industry/domain plugins.

The original :class:`IndustryPlugin` API remains supported.  The richer
``SectorPackManifest`` is additive: it describes the domain surface without
moving execution authority into the plugin.  Consequential external actions
must still flow through the platform Action Kernel.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class SectorPackManifest:
    """Stable metadata describing a sector/domain pack."""

    pack_id: str
    version: str
    display_name: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    resources: tuple[str, ...] = field(default_factory=tuple)
    policies: tuple[str, ...] = field(default_factory=tuple)
    workflows: tuple[str, ...] = field(default_factory=tuple)
    events: tuple[str, ...] = field(default_factory=tuple)
    webhooks: tuple[str, ...] = field(default_factory=tuple)
    external_actions: tuple[str, ...] = field(default_factory=tuple)


class IndustryPlugin(ABC):
    """Contract that every Kanyoza sector/domain plugin must fulfill.

    ``get_commands()``, ``execute()`` and ``get_webhooks()`` are retained as
    the compatibility execution surface.  New integrations should consume
    ``get_manifest()`` first and use the platform Action Kernel for any
    consequential external side effect.
    """

    # Additive metadata contract. Legacy plugins can inherit the defaults.
    pack_id = ""
    pack_version = "1.0"
    display_name = ""
    capabilities: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    external_actions: tuple[str, ...] = ()

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client

    def resolve_workspace(self, client_workspace_id: Optional[str] = None) -> str:
        """Resolve execution tenancy through the platform security boundary."""
        from core.plugin_execution import resolve_plugin_workspace

        return resolve_plugin_workspace(client_workspace_id)

    def get_manifest(self) -> SectorPackManifest:
        """Return the formal sector-pack manifest.

        Defaults are intentionally derived from the existing command/webhook
        API so legacy plugins can adopt the contract without a rewrite.
        """
        commands = self.get_commands() or []
        webhooks = self.get_webhooks() or []
        inferred_capabilities = tuple(
            dict.fromkeys(
                str(item.get("intent"))
                for item in commands
                if isinstance(item, dict) and item.get("intent")
            )
        )
        inferred_webhooks = tuple(
            dict.fromkeys(
                str(item.get("path"))
                for item in webhooks
                if isinstance(item, dict) and item.get("path")
            )
        )
        return SectorPackManifest(
            pack_id=self.pack_id or self.name.lower(),
            version=self.pack_version,
            display_name=self.display_name or self.name,
            capabilities=self.capabilities or inferred_capabilities,
            resources=self.resources,
            policies=self.policies,
            workflows=self.workflows,
            events=self.events,
            webhooks=inferred_webhooks,
            external_actions=self.external_actions,
        )

    def get_capabilities(self) -> tuple[str, ...]:
        """Return declared domain capabilities without granting execution authority."""
        return self.get_manifest().capabilities

    def get_resources(self) -> tuple[str, ...]:
        """Return domain resource types owned by this pack."""
        return self.get_manifest().resources

    def get_policies(self) -> tuple[str, ...]:
        """Return policy identifiers owned by this pack."""
        return self.get_manifest().policies

    def get_workflows(self) -> tuple[str, ...]:
        """Return workflow identifiers owned by this pack."""
        return self.get_manifest().workflows

    def get_events(self) -> tuple[str, ...]:
        """Return domain event identifiers owned by this pack."""
        return self.get_manifest().events

    def get_external_actions(self) -> tuple[str, ...]:
        """Return external action types requiring platform execution semantics."""
        return self.get_manifest().external_actions

    @abstractmethod
    def get_commands(self) -> list[dict]:
        """Return all natural-language/domain commands this plugin supports."""
        ...

    @abstractmethod
    def execute(self, command: str, params: dict) -> dict:
        """Execute a domain command within the authenticated workspace."""
        ...

    @abstractmethod
    def get_webhooks(self) -> list[dict]:
        """Return webhook handlers this plugin registers."""
        ...

    @property
    def name(self) -> str:
        """Human-readable plugin name."""
        return self.__class__.__name__.replace("Plugin", "")
