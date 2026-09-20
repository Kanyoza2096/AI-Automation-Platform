#!/usr/bin/env python3
"""Container health probe for non-HTTP KDO processes.

The API has /health/live; CORE and WORKERS do not.  Those processes publish a
small local heartbeat file so the container supervisor can distinguish a live
process from a wedged process without opening a network listener.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

DEFAULT_PATH = Path("/tmp/kdo-runtime-heartbeat.json")
DEFAULT_MAX_AGE = 30.0


def main() -> int:
    path = Path(os.getenv("KDO_HEARTBEAT_FILE", str(DEFAULT_PATH)))
    try:
        max_age = float(os.getenv("KDO_HEARTBEAT_MAX_AGE_SECONDS", DEFAULT_MAX_AGE))
    except ValueError:
        max_age = DEFAULT_MAX_AGE

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(payload["timestamp"])
        pid = int(payload["pid"])
        mode = str(payload["mode"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 1

    age = time.time() - timestamp
    if pid <= 0 or not mode or age < -5 or age > max_age:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
