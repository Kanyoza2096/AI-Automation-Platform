"""
Environment enumeration for Kanyoza Systems AI Platform v10.
Strongly typed — never compare against raw strings in application code.
"""

from enum import Enum


class Environment(str, Enum):
    """Runtime environment identifier."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"

    @classmethod
    def from_string(cls, value: str) -> "Environment":
        """
        Parse a string into an Environment, accepting common aliases.
        Defaults to PRODUCTION for unrecognised values (fail-safe).
        """
        normalized = value.strip().lower()
        aliases = {
            "dev": cls.DEVELOPMENT,
            "development": cls.DEVELOPMENT,
            "local": cls.DEVELOPMENT,
            "test": cls.TESTING,
            "testing": cls.TESTING,
            "prod": cls.PRODUCTION,
            "production": cls.PRODUCTION,
        }
        return aliases.get(normalized, cls.PRODUCTION)

    @property
    def is_development(self) -> bool:
        return self == Environment.DEVELOPMENT

    @property
    def is_testing(self) -> bool:
        return self == Environment.TESTING

    @property
    def is_production(self) -> bool:
        return self == Environment.PRODUCTION
