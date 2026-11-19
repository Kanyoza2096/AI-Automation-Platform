"""
Environment enumeration for Kanyoza Systems AI Platform v10.
Strongly typed — never compare against raw strings in application code.
"""

from enum import Enum


class Environment(str, Enum):
    """Runtime environment identifier."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"

    @classmethod
    def from_string(cls, value: str) -> "Environment":
        """
        Parse a string into an Environment, accepting common aliases.

        Unknown values are rejected rather than silently treated as production.
        This prevents misspelled or unsupported deployment labels from
        accidentally enabling production-only behavior.
        """
        normalized = value.strip().lower()
        aliases = {
            "dev": cls.DEVELOPMENT,
            "development": cls.DEVELOPMENT,
            "local": cls.DEVELOPMENT,
            "test": cls.TESTING,
            "testing": cls.TESTING,
            "stage": cls.STAGING,
            "staging": cls.STAGING,
            "prod": cls.PRODUCTION,
            "production": cls.PRODUCTION,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(f"Unsupported environment: {value!r}") from exc

    @property
    def is_development(self) -> bool:
        return self == Environment.DEVELOPMENT

    @property
    def is_testing(self) -> bool:
        return self == Environment.TESTING

    @property
    def is_staging(self) -> bool:
        return self == Environment.STAGING

    @property
    def is_production(self) -> bool:
        return self == Environment.PRODUCTION
