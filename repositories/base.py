from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Repository(ABC, Generic[T]):
    """Abstract repository. Concrete implementations hide all Supabase (or
    other storage) details behind a clean, domain-friendly interface."""

    @abstractmethod
    def find_by_id(self, record_id: str) -> Optional[T]: ...

    @abstractmethod
    def save(self, entity: T) -> T: ...

    @abstractmethod
    def delete(self, record_id: str) -> bool: ...

    def _safe_call(self, fn, *args, default=None, **kwargs):
        """Run a storage call and return *default* on any exception."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.error("%s._safe_call: %s", self.__class__.__name__, exc, exc_info=True)
            return default
