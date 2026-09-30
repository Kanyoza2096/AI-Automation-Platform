"""Production CORE process entrypoint.

Runs the lifecycle CORE boot graph (Director, Condition Engine, Meta Engine,
AI orchestration and Autonomous Loop) without pretending to be an HTTP API.
"""
from __future__ import annotations

import asyncio
from .runtime import run_service


def main() -> None:
    asyncio.run(run_service("core"))


if __name__ == "__main__":
    main()
