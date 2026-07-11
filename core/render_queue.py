"""
RenderQueue — placeholder for async card rendering.
Currently unused. Card rendering runs synchronously via CardRenderer.
"""

import logging

logger = logging.getLogger("kanyoza.render_queue")


class RenderQueue:
    """Placeholder — will queue Playwright card renders in future milestones."""

    def __init__(self):
        logger.info("RenderQueue initialized (stub — sync rendering active)")

    def start(self):
        pass

    def stop(self):
        pass
