"""Production WORKERS process entrypoint.

Runs TaskQueue, durable outbox draining and the multi-tenant brand scheduler.
"""
from __future__ import annotations

import asyncio
from .runtime import run_service


def main() -> None:
    asyncio.run(run_service("workers"))


if __name__ == "__main__":
    main()
