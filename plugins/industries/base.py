"""
Abstract base class for all industry plugins.

Every plugin must implement:
  - get_commands() → list of NL commands this plugin handles
  - execute()      → run a command with params
  - get_webhooks() → webhook handlers this plugin registers
"""

from abc import ABC, abstractmethod
from typing import Any


class IndustryPlugin(ABC):
    """Contract that every Kanyoza industry plugin must fulfill."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client

    @abstractmethod
    def get_commands(self) -> list[dict]:
        """
        Return all natural-language commands this plugin supports.

        Each command dict:
          {
            "intent": "add_member",
            "description": "Register a new church member",
            "params": ["name", "phone", "email"],
            "example": "Add member John Doe phone 0888123456 email john@example.com",
            "category": "members",
          }
        """
        ...

    @abstractmethod
    def execute(self, command: str, params: dict) -> dict:
        """
        Execute a plugin command.

        Returns:
          {"ok": True, "data": {...}, "message": "..."}
          {"ok": False, "error": "..."}
        """
        ...

    @abstractmethod
    def get_webhooks(self) -> list[dict]:
        """
        Return webhook handlers this plugin registers.

        Each webhook dict:
          {
            "path": "/webhooks/church/member",
            "method": "POST",
            "handler": self.handle_member_webhook,
            "description": "Member registration webhook",
          }
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable plugin name."""
        return self.__class__.__name__.replace("Plugin", "")
