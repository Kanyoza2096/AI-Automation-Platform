"""Shared, side-effect-free Director signal contract helpers.

Publishers must supply recommendations only when backed by concrete evidence.
An empty list is an explicit statement that this observation has no current
recommendations; it is not a claim that the engine is healthy.
"""
from __future__ import annotations


def director_signal_metrics(signals=None) -> dict:
    """Return the stable metrics envelope consumed by Director observers."""
    if signals is None:
        signals = []
    if not isinstance(signals, list):
        raise TypeError("director signals must be a list")
    return {"director_signals": signals, "director_signal_contract": "metrics.director_signals/v1"}
