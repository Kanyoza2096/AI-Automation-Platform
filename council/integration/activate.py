"""
Activate Council integration across the entire platform.

Call this during startup to wire the Council into all engines.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("kanyoza.integration.activate")


async def activate_council_integration(container) -> None:
    """
    Activate Council integration for all engines.
    
    Called during startup after all services are initialized.
    """
    try:
        from council.integration import council
        logger.info("Council Orchestrator initialized — %d personas", 
                    len(council.get_status().get("persona_names", [])))
    except ImportError:
        logger.warning("Council module not available — skipping integration")
        return
    
    # ── Integrate with Director ───────────────────────────────────
    try:
        from services.director import get_director
        director = await get_director()
        if director:
            from council.integration.director_integration import DirectorCouncilIntegration
            DirectorCouncilIntegration(director)
            logger.info("✅ Director integrated with Council")
    except Exception as exc:
        logger.warning("Director integration failed: %s", exc)
    
    # ── Integrate with Condition Engine ───────────────────────────
    try:
        from services.condition_engine import AutonomousConditionEngine
        from council.integration.condition_engine_integration import bind_condition_engine_with_council
        eng = None
        if container is not None:
            eng = container.try_resolve(AutonomousConditionEngine)
        if eng is None:
            from services.condition_engine import autonomous_engine as eng
        if eng:
            bind_condition_engine_with_council(eng)
            logger.info("✅ Condition Engine integrated with Council")
        else:
            logger.warning("Condition Engine not available for Council integration")
    except Exception as exc:
        logger.warning("Condition Engine integration failed: %s", exc)
    
    # ── Integrate with Scheduler ─────────────────────────────────
    try:
        from scheduler.brand_scheduler import BrandSchedulerManager
        manager = container.try_resolve(BrandSchedulerManager)
        if manager:
            from council.integration.scheduler_integration import bind_scheduler_with_council
            bind_scheduler_with_council(manager)
            logger.info("✅ Scheduler integrated with Council")
    except Exception as exc:
        logger.warning("Scheduler integration failed: %s", exc)
    
    logger.info("=" * 60)
    logger.info("✅ COUNCIL INTEGRATION COMPLETE")
    logger.info("=" * 60)