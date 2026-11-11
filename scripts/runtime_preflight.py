#!/usr/bin/env python3
"""Fail-fast runtime dependency and application import preflight.

This is intentionally stricter than the unit-test suite: tests may exercise
mocked integrations, while production must be able to import every mandatory
runtime dependency and the canonical ASGI application.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# requirements.txt entries that are mandatory for the shipped full-mode app.
# Names are distribution -> import module because they differ for a few deps.
REQUIRED_DISTRIBUTIONS: dict[str, str] = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "gunicorn": "gunicorn",
    "pydantic": "pydantic",
    "pydantic-settings": "pydantic_settings",
    "httpx": "httpx",
    "supabase": "supabase",
    "redis": "redis",
    "python-socketio": "socketio",
    "python-multipart": "multipart",
    "cryptography": "cryptography",
    "requests": "requests",
    "opentelemetry-api": "opentelemetry",
    "opentelemetry-sdk": "opentelemetry.sdk",
    "websockets": "websockets",
    "aiohttp": "aiohttp",
    "Pillow": "PIL",
    "psutil": "psutil",
    "PyMuPDF": "fitz",
    "PyPDF2": "PyPDF2",
    "pycairo": "cairo",
    "Flask": "flask",
    "requests-oauthlib": "requests_oauthlib",
    "stripe": "stripe",
}


def main() -> int:
    failures: list[str] = []
    print("RUNTIME PREFLIGHT")

    for distribution, module in REQUIRED_DISTRIBUTIONS.items():
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"missing distribution: {distribution}")
            continue

        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - environment-specific
            failures.append(
                f"unimportable module {module} from {distribution}=={version}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        print(f"OK: {distribution}=={version} -> {module}")

    try:
        import main as application_entrypoint

        app = getattr(application_entrypoint, "app", None)
        if app is None:
            failures.append("main module imported but does not expose app")
        else:
            print("OK: main:app import")
    except Exception as exc:  # pragma: no cover - environment-specific
        failures.append(
            f"main:app import failed: {type(exc).__name__}: {exc}"
        )

    if failures:
        print("RUNTIME PREFLIGHT: FAIL")
        for failure in failures:
            print(f"ERROR: {failure}")
        return 1

    print("RUNTIME PREFLIGHT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
