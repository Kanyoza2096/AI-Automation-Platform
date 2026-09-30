"""
Cloudflare Workers AI — free-tier image generation.

Primary models (Workers AI free):
  - @cf/black-forest-labs/flux-1-schnell   (fast, high quality)
  - @cf/stabilityai/stable-diffusion-xl-base-1.0
  - @cf/bytedance/stable-diffusion-xl-lightning

Auth:
  CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN
  (or settings.cloudflare_account_id / cloudflare_api_token)

API:
  POST https://api.cloudflare.com/client/v4/accounts/{id}/ai/run/{model}

V1.6.1 (2026-09-28):
  - Fixed FLUX-1-schnell payload. FLUX uses `steps` (1-8), not `num_steps`.
    Previously sent `num_steps`, causing every FLUX call to return 400 and
    fall through to SDXL-Lightning. Now FLUX succeeds as the primary model.
  - Log response body on non-200 for faster diagnosis.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("kanyoza.services.cloudflare_image")

MODELS = {
    "flux_schnell": "@cf/black-forest-labs/flux-1-schnell",
    "sdxl_base": "@cf/stabilityai/stable-diffusion-xl-base-1.0",
    "sdxl_lightning": "@cf/bytedance/stable-diffusion-xl-lightning",
}

# Prefer quality then speed for social posts
_MODEL_ORDER = (
    MODELS["flux_schnell"],
    MODELS["sdxl_lightning"],
    MODELS["sdxl_base"],
)

_API = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"


def _credentials() -> tuple[str, str]:
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        try:
            from config.settings import settings
            account_id = account_id or (getattr(settings, "cloudflare_account_id", "") or "").strip()
            api_token = api_token or (getattr(settings, "cloudflare_api_token", "") or "").strip()
        except Exception:
            pass
    return account_id, api_token


def is_configured() -> bool:
    account_id, api_token = _credentials()
    return bool(account_id and api_token)


def _build_prompt(topic: str, content: str = "", author: str = "", platform: str = "facebook") -> str:
    """Craft a social-ready image prompt (no embedded text in the image)."""
    topic = (topic or "technology").strip()[:200]
    content = (content or "").strip()[:400]
    base = (
        f"Professional social media illustration for: {topic}. "
        "Modern tech aesthetic, clean composition, high detail, "
        "cinematic lighting, rich color, 4k quality. "
        "No text, no watermarks, no logos, no UI chrome."
    )
    if content:
        # Pull a short topical hint without dumping the whole post
        hint = re.sub(r"\s+", " ", content)[:180]
        base += f" Context: {hint}."
    if platform:
        base += f" Optimized for {platform} feed."
    if author:
        base += f" Tone aligned with {author}."
    return base


def _log_response_body(mid: str, body: bytes) -> None:
    """Log a truncated, redacted response body for non-200 responses."""
    try:
        snippet = body[:400].decode("utf-8", errors="replace")
        logger.warning("Cloudflare model %s response: %s", mid, snippet)
    except Exception:
        pass


def generate_image_bytes(
    prompt: str,
    *,
    model: Optional[str] = None,
    width: int = 1024,
    height: int = 1024,
    steps: Optional[int] = None,
    timeout: float = 60.0,
) -> Optional[bytes]:
    """
    Call Cloudflare Workers AI and return raw image bytes (PNG/JPEG).
    Tries preferred models in order until one succeeds.
    """
    account_id, api_token = _credentials()
    if not account_id or not api_token:
        logger.warning("Cloudflare Workers AI not configured (missing account id or token)")
        return None

    models = [model] if model else list(_MODEL_ORDER)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    try:
        from utils.http_client import HttpClient
        http = HttpClient("cloudflare_ai", default_timeout=int(timeout))
    except Exception:
        import requests as http  # type: ignore
        http = None

    for mid in models:
        if not mid:
            continue
        url = _API.format(account_id=account_id, model=mid)

        # ═══════════════════════════════════════════════════════════════════
        # ✅ V1.6.1 — Per-model payload schema.
        #
        # FLUX-1-schnell:   { prompt, steps (1-8), seed? }
        # SDXL family:      { prompt, width, height, num_steps (20-50) }
        # SDXL-Lightning:   { prompt, width, height, num_steps (1-8) }
        # ═══════════════════════════════════════════════════════════════════
        payload: Dict[str, Any] = {"prompt": prompt}

        if "flux" in mid:
            # FLUX uses `steps`, NOT `num_steps`. This was the 400 cause.
            payload["steps"] = max(1, min(int(steps or 4), 8))
        elif "lightning" in mid:
            payload["width"] = width
            payload["height"] = height
            payload["num_steps"] = max(1, min(int(steps or 4), 8))
        elif "stable-diffusion" in mid:
            payload["width"] = width
            payload["height"] = height
            payload["num_steps"] = max(20, min(int(steps or 20), 50))
        else:
            # Unknown model — send conservative defaults
            payload["num_steps"] = steps or 4

        try:
            if http is not None and hasattr(http, "post"):
                resp = http.post(url, headers=headers, json=payload, timeout=timeout)
                status = getattr(resp, "status_code", None)
                body = resp.content if hasattr(resp, "content") else b""
                data = None
                try:
                    data = resp.json() if hasattr(resp, "json") else None
                except Exception:
                    data = None
            else:
                import requests
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                status = resp.status_code
                body = resp.content
                data = None
                try:
                    data = resp.json()
                except Exception:
                    data = None

            if status != 200:
                logger.warning("Cloudflare model %s HTTP %s", mid, status)
                _log_response_body(mid, body)
                continue

            # Workers AI may return:
            #  - raw image bytes (content-type image/*)
            #  - JSON {"result": {"image": "<base64>"}} or {"result": "<base64>"}
            content_type = ""
            if hasattr(resp, "headers"):
                content_type = (resp.headers.get("content-type") or "").lower()

            if content_type.startswith("image/") and body:
                logger.info("Cloudflare image OK model=%s bytes=%d", mid, len(body))
                return body

            if data is None and body:
                import json
                try:
                    data = json.loads(body.decode("utf-8", errors="replace"))
                except Exception:
                    # Might already be raw image
                    if body[:8] == b"\x89PNG\r\n\x1a\n" or body[:2] == b"\xff\xd8":
                        return body
                    continue

            if isinstance(data, dict):
                result = data.get("result", data)
                b64 = None
                if isinstance(result, str):
                    b64 = result
                elif isinstance(result, dict):
                    b64 = result.get("image") or result.get("image_b64")
                if b64:
                    raw = base64.b64decode(b64)
                    logger.info("Cloudflare image OK model=%s bytes=%d", mid, len(raw))
                    return raw

            logger.warning("Cloudflare model %s: unrecognized response shape", mid)
        except Exception as exc:
            logger.warning("Cloudflare model %s failed: %s", mid, exc)
            continue

    return None


def generate_trend_image(trend: Dict[str, Any], supabase_client=None) -> Optional[str]:
    """
    Generate an image for a trend/post payload and optionally upload to storage.

    Returns a public URL when upload succeeds, otherwise None.
    (Callers that need bytes should use generate_image_bytes.)
    """
    topic = str(trend.get("title") or trend.get("topic") or "technology")
    content = str(trend.get("content") or trend.get("summary") or "")
    author = str(trend.get("author") or "")
    platform = str(trend.get("platform") or "facebook")

    prompt = _build_prompt(topic, content, author, platform)
    image_bytes = generate_image_bytes(prompt)
    if not image_bytes:
        return None

    # Optional upload to Supabase storage
    if supabase_client is not None:
        try:
            path = f"ai-images/{platform}/{re.sub(r'[^a-zA-Z0-9_-]+', '-', topic.lower())[:60]}.png"
            bucket = "media"
            supabase_client.storage.from_(bucket).upload(
                path,
                image_bytes,
                file_options={"content-type": "image/png", "upsert": "true"},
            )
            public = supabase_client.storage.from_(bucket).get_public_url(path)
            if isinstance(public, str) and public.startswith("http"):
                return public
            if isinstance(public, dict):
                return public.get("publicUrl") or public.get("public_url")
        except Exception as exc:
            logger.warning("Supabase upload failed (returning no URL): %s", exc)

    # No storage — encode data URL so API still returns something usable
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def generate_for_post(topic: str, caption: str = "", brand_name: str = "") -> Optional[bytes]:
    """Convenience for content_engine / image_engine."""
    prompt = _build_prompt(topic, caption, author=brand_name)
    return generate_image_bytes(prompt)


__all__ = [
    "MODELS",
    "is_configured",
    "generate_image_bytes",
    "generate_trend_image",
    "generate_for_post",
]
