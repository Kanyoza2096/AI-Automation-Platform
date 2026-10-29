"""Resolve brand keywords and fallback topics from DB (with defaults)."""

from __future__ import annotations

from typing import List, Optional

from core.runtime_config import runtime_config, BrandRuntimeConfig


def get_brand_keywords(brand_id: str, workspace_id: str = "default") -> List[str]:
    cfg = runtime_config.get_brand(brand_id, workspace_id)
    return list(cfg.keywords)


def get_fallback_topics(brand_id: str, workspace_id: str = "default") -> List[str]:
    cfg = runtime_config.get_brand(brand_id, workspace_id)
    if cfg.fallback_topics:
        return list(cfg.fallback_topics)
    # Safe generic defaults only when brand has none in DB
    return [
        "industry insight",
        "customer success story",
        "product tip",
        "behind the scenes",
        "weekly reflection",
    ]


def get_brand_content_config(brand_id: str, workspace_id: str = "default") -> BrandRuntimeConfig:
    return runtime_config.get_brand(brand_id, workspace_id)
