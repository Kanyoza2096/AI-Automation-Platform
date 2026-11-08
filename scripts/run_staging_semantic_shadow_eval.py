"""Operator-triggered, staging-only semantic shadow evaluation.

Run from the repository root in the staging runtime:
    python scripts/run_staging_semantic_shadow_eval.py

This script performs at most five reviewer calls. It does not publish,
persist evaluation results, schedule work, or call social-platform APIs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Executing this file by path puts scripts/ (not the repository root) on
# sys.path. Add the project root so top-level packages such as config resolve
# consistently in local and GitHub Actions execution.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from config.settings import settings
from models.brand import Brand
from repositories.brand_repository import BrandRepository
from repositories.knowledge_repository import KnowledgeRepository
from services.content_semantic_shadow_runner import evaluate_batch
from services.supabase import aclose_all, get_service_client_from_settings

STAGING_HOST = "kkzshurcmcptleyqjgzo.supabase.co"
STAGING_WORKSPACE_ID = "kdo-engineering-staging"
STAGING_BRAND_ID = "brand-kdo-semantic-shadow-staging"

CASES = [
    {
        "topic": "canary deployments limited traffic exposure blast radius rollback",
        "caption": (
            "A canary limits how much traffic initially sees a new release, "
            "but 5% exposure does not guarantee a 95% reduction in risk. "
            "Representativeness, shared dependencies, data writes, and clear "
            "rollback criteria still determine whether the rollout is safe."
        ),
    },
    {
        "topic": "canary deployments limited traffic exposure risk",
        "caption": (
            "A 5% canary guarantees exactly 95% less blast radius and removes "
            "the need for rollback planning."
        ),
    },
    {
        "topic": "observability metrics logs traces actionable alerts",
        "caption": (
            "Software reliability is important. Teams should test their code, "
            "monitor their systems, and always follow best practices."
        ),
    },
    {
        "topic": "retry strategies exponential backoff jitter transient failures",
        "caption": (
            "A healthy garden starts with suitable soil, regular watering, "
            "and enough sunlight for each plant."
        ),
    },
    {
        "topic": "canary deployments limited traffic exposure database migrations",
        "caption": (
            "Our production canary exposed 5% of traffic, detected 99.9% of "
            "database defects in 12 seconds, and prevented every customer impact."
        ),
    },
]


def _load_staging_brand(brand_repository: BrandRepository) -> Brand:
    """Resolve the evaluation brand only inside its canonical workspace."""
    staging_brands = brand_repository.find_by_workspace(STAGING_WORKSPACE_ID)
    brand = next(
        (candidate for candidate in staging_brands if candidate.id == STAGING_BRAND_ID),
        None,
    )
    if not isinstance(brand, Brand):
        raise RuntimeError("The approved staging evaluation brand could not be loaded")
    if brand.workspace_id != STAGING_WORKSPACE_ID:
        raise RuntimeError("Loaded brand does not belong to the canonical staging workspace")
    return brand


def _assert_operator_boundary() -> None:
    effective_environment = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("KDO_ENVIRONMENT")
        or settings.environment
        or ""
    ).strip().lower()
    if effective_environment != "staging":
        raise RuntimeError("Refusing to run: effective environment is not staging")
    if settings.environment.strip().lower() != "staging":
        raise RuntimeError("Refusing to run: application settings are not staging")
    if os.getenv("KDO_SEMANTIC_SHADOW_EVAL_ENABLED", "").strip().lower() not in {
        "1", "true", "yes"
    }:
        raise RuntimeError("Refusing to run: explicit semantic shadow flag is disabled")
    if urlparse(settings.supabase_url).hostname != STAGING_HOST:
        raise RuntimeError("Refusing to run: configured Supabase URL is not KDO staging")
    if not settings.supabase_service_role_key:
        raise RuntimeError("Refusing to run: staging service database credential is missing")


async def _run() -> int:
    client = None
    try:
        _assert_operator_boundary()
        client = get_service_client_from_settings()
        if client is None:
            raise RuntimeError("Staging Supabase service client could not be created")
        brand_repository = BrandRepository(client)
        brand = _load_staging_brand(brand_repository)

        knowledge_repository = KnowledgeRepository(client)
        results = await evaluate_batch(
            cases=CASES,
            brand=brand,
            repository=knowledge_repository,
        )
        print(json.dumps({
            "evaluation_type": "staging_semantic_shadow",
            "workspace_id": STAGING_WORKSPACE_ID,
            "brand_id": STAGING_BRAND_ID,
            "case_count": len(results),
            "published": False,
            "persisted": False,
            "results": results,
        }, indent=2, default=str))
        return 0
    finally:
        await aclose_all()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_run()))
    except Exception as exc:
        print(
            json.dumps({
                "error": type(exc).__name__,
                "message": str(exc),
                "published": False,
                "persisted": False,
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
