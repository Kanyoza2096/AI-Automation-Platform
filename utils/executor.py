# utils/executor.py
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("kanyoza.utils.executor")

# Shared ThreadPoolExecutor with max 4 workers
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kanyoza-bg-worker")


def submit_bg_task(fn, *args, **kwargs):
    """Submit a task to the shared background executor."""
    try:
        return _executor.submit(fn, *args, **kwargs)
    except Exception as exc:
        logger.error("Failed to submit background task: %s", exc)
        return None


def shutdown_executor(wait=True, timeout=30):
    """Shutdown the background executor gracefully."""
    logger.info("Shutting down background task executor (wait=%s, timeout=%s)...", wait, timeout)
    _executor.shutdown(wait=wait)
