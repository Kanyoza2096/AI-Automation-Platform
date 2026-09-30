"""Organism director (authority)."""
from __future__ import annotations
try:
    from services.director import get_director  # type: ignore
except ImportError:
    get_director = None  # type: ignore
__all__ = ["get_director"]
