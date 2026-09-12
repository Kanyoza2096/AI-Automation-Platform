import concurrent.futures
import threading

# A singleton ThreadPoolExecutor used for background tasks.
# Render free tier is limited to a single vCPU, so we keep max_workers low (default 2).
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# A simple thread‑local flag to indicate if we are inside a background task.
_task_local = threading.local()

def submit_task(fn, *args, **kwargs):
    """Submit a callable to be executed in the background thread pool.

    Returns a ``concurrent.futures.Future`` that can be awaited or ignored.
    ``fn`` should be a regular function (not async). Exceptions are logged by the executor.
    """
    return _executor.submit(_run_wrapper, fn, *args, **kwargs)

def _run_wrapper(fn, *args, **kwargs):
    # Mark this thread as a background task for any downstream checks.
    _task_local.is_background = True
    try:
        return fn(*args, **kwargs)
    finally:
        # Cleanup flag.
        _task_local.is_background = False
