"""
HTML Card Engine v1.1
Generates beautiful tech cards from post content.
Converts HTML → PNG via HCTI API.
Verbose logging for debugging.
"""

import logging
import json
import requests

from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.html_card_engine")

HCTI_API = "https://hcti.io/v1/image"

_http = HttpClient("hcti", default_timeout=10)


def _get_hcti_credentials():
    """Get HCTI credentials from settings."""
    from config.settings import settings
    user_id = getattr(settings, 'hcti_user_id', '')
    api_key = getattr(settings, 'hcti_api_key', '')
    return user_id, api_key


def is_configured() -> bool:
    """Check if HCTI API is configured."""
    user_id, api_key = _get_hcti_credentials()
    configured = bool(user_id and api_key)
    if not configured:
        logger.warning("HCTI: Not configured — missing user_id or api_key in settings")
    else:
        logger.info(f"HCTI: Configured — user_id={'***' if user_id else 'MISSING'}, api_key={'***' if api_key else 'MISSING'}")
    return configured


def _html_to_png(html: str) -> bytes:
    """Convert HTML to PNG using HCTI API."""
    user_id, api_key = _get_hcti_credentials()

    if not user_id:
        logger.error("HCTI: user_id is empty — check HCTI_USER_ID env var")
        return None

    if not api_key:
        logger.error("HCTI: api_key is empty — check HCTI_API_KEY env var")
        return None

    logger.info(f"HCTI: Sending request... (HTML length: {len(html)} chars)")

    try:
        response = _http.post(
            HCTI_API,
            data={"html": html},
            auth=(user_id, api_key),
            timeout=10
        )

        logger.info(f"HCTI: Response status = {response.status_code}")

        if response.status_code == 200:
            image_size = len(response.content)
            logger.info(f"HCTI: ✅ Success! Image size: {image_size} bytes")
            return response.content

        elif response.status_code == 401:
            logger.error("HCTI: ❌ Authentication failed — check your HCTI_USER_ID and HCTI_API_KEY")
            logger.error(f"HCTI: Response body: {response.text[:300]}")

        elif response.status_code == 429:
            logger.error("HCTI: ❌ Rate limited — you've exceeded the free tier limit (100 images/month)")

        elif response.status_code == 400:
            logger.error("HCTI: ❌ Bad request — HTML may be invalid")
            logger.error(f"HCTI: Response body: {response.text[:300]}")

        elif response.status_code >= 500:
            logger.error(f"HCTI: ❌ Server error ({response.status_code}) — HCTI may be down")

        else:
            logger.error(f"HCTI: ❌ Unexpected status {response.status_code}")
            logger.error(f"HCTI: Response body: {response.text[:300]}")

        return None

    except requests.exceptions.Timeout:
        logger.error("HCTI: ❌ Timeout — request took longer than 10 seconds")
        return None

    except requests.exceptions.ConnectionError as e:
        logger.error(f"HCTI: ❌ Connection error — cannot reach hcti.io: {e}")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"HCTI: ❌ Request failed: {e}")
        return None

    except Exception as e:
        logger.error(f"HCTI: ❌ Unexpected error: {type(e).__name__}: {e}")
        return None


def generate_card(title: str, points: list) -> bytes:
    """Generate a professional tech card image."""
    if not is_configured():
        return None

    points = points[:5]
    logger.info(f"Card: Generating card — title='{title[:60]}', points={len(points)}")

    list_items = ""
    for point in points:
        list_items += (
            '<li style="padding:14px 0; color:#E2E8F0; font-size:30px; '
            'list-style:none; border-bottom:1px solid #334155;">'
            f'▸  {point}</li>'
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800&display=swap" rel="stylesheet">
</head>
<body style="background:#0F172A; width:1080px; height:1080px; display:flex; align-items:center; justify-content:center; margin:0; font-family:'Inter',sans-serif;">
    <div style="background:#1E293B; width:920px; height:920px; border-radius:40px; padding:72px; box-sizing:border-box; display:flex; flex-direction:column; justify-content:space-between; border:1px solid #334155; box-shadow:0 20px 60px rgba(0,0,0,0.5);">
        <div>
            <div style="margin-bottom:16px;">
                <span style="background:#4F46E5; color:#fff; padding:8px 20px; border-radius:20px; font-size:22px; font-weight:700;">Comparison</span>
            </div>
            <h1 style="color:#F1F5F9; font-size:52px; margin:24px 0 48px 0; font-weight:800; line-height:1.2;">{title}</h1>
            <ul style="padding:0; margin:0;">{list_items}</ul>
        </div>
        <div style="border-top:2px solid #334155; padding-top:24px; display:flex; justify-content:space-between; align-items:center;">
            <span style="color:#64748B; font-size:22px; font-weight:600;">Kanyoza Systems</span>
            <span style="color:#4F46E5; font-size:20px; font-weight:700;">Daily Tech Insights</span>
        </div>
    </div>
</body>
</html>"""

    return _html_to_png(html)


def generate_comparison_card(option_a: str, option_b: str,
                              winner: str, pros_a: list, pros_b: list) -> bytes:
    """Generate a side-by-side comparison card."""
    if not is_configured():
        return None

    logger.info(f"Comparison Card: {option_a} vs {option_b}, winner={winner}")

    pros_a_html = "".join([
        f'<li style="color:#10B981; font-size:22px; padding:4px 0;">✅ {p}</li>'
        for p in pros_a[:3]
    ])
    pros_b_html = "".join([
        f'<li style="color:#10B981; font-size:22px; padding:4px 0;">✅ {p}</li>'
        for p in pros_b[:3]
    ])

    html = f"""<!DOCTYPE html>
<html>
<head>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
</head>
<body style="background:#0F172A; width:1200px; height:630px; display:flex; align-items:center; justify-content:center; margin:0; font-family:'Inter',sans-serif;">
    <div style="background:#1E293B; width:1140px; height:570px; border-radius:32px; padding:56px; box-sizing:border-box; display:flex; flex-direction:column; justify-content:space-between; border:1px solid #334155;">
        <div>
            <h1 style="color:#F1F5F9; font-size:44px; margin:0 0 32px 0; font-weight:800; text-align:center;">{option_a} vs {option_b}</h1>
            <div style="display:flex; gap:32px;">
                <div style="flex:1; background:#0F172A; border-radius:20px; padding:28px; border:1px solid #4F46E5;">
                    <h2 style="color:#4F46E5; font-size:28px; margin:0 0 16px 0; font-weight:700;">{option_a}</h2>
                    <ul style="padding:0; margin:0; list-style:none;">{pros_a_html}</ul>
                </div>
                <div style="flex:1; background:#0F172A; border-radius:20px; padding:28px; border:1px solid:#3B82F6;">
                    <h2 style="color:#3B82F6; font-size:28px; margin:0 0 16px 0; font-weight:700;">{option_b}</h2>
                    <ul style="padding:0; margin:0; list-style:none;">{pros_b_html}</ul>
                </div>
            </div>
        </div>
        <div style="text-align:center; margin-top:20px;">
            <span style="background:#4F46E5; color:#fff; padding:12px 32px; border-radius:24px; font-size:24px; font-weight:700;">🏆 Winner: {winner}</span>
        </div>
        <div style="border-top:1px solid #334155; padding-top:16px; display:flex; justify-content:space-between;">
            <span style="color:#64748B; font-size:18px; font-weight:600;">Kanyoza Systems</span>
            <span style="color:#4F46E5; font-size:16px;">Daily Tech Insights</span>
        </div>
    </div>
</body>
</html>"""

    return _html_to_png(html)


def generate_card_from_post(post_content: str) -> bytes:
    """
    Extract data from post and generate the best card.
    Uses Gemini to extract title and points.
    """
    logger.info("CardFromPost: Starting extraction from post...")

    try:
        from services.gemini import gemini
        prompt = (
            "Extract as JSON (keep it SHORT):\n"
            '"option_a": name (1-2 words)\n'
            '"option_b": name (1-2 words)\n'
            '"winner": which wins (1-2 words)\n'
            '"points_a": 2 short pros (max 4 words each)\n'
            '"points_b": 2 short pros (max 4 words each)\n\n'
            f"POST:\n{post_content[:600]}\n\n"
            "Return ONLY valid JSON. No other text."
        )

        logger.info("CardFromPost: Calling Gemini for extraction...")
        result = gemini._call(
            prompt=prompt, temperature=0.3, max_tokens=1200
        )

        if not result:
            logger.error("CardFromPost: ❌ Gemini returned empty result")
            return None

        logger.info(f"CardFromPost: Gemini returned {len(result)} chars")

        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].split("```")[0]

        try:
            data = json.loads(result)
            logger.info(f"CardFromPost: ✅ JSON parsed — keys: {list(data.keys())}")
        except json.JSONDecodeError as e:
            logger.error(f"CardFromPost: ❌ JSON parse failed: {e}")
            logger.error(f"CardFromPost: Raw result: {result[:300]}")
            return None

        if data.get("option_a") and data.get("option_b"):
            logger.info(f"CardFromPost: Generating comparison card — {data['option_a']} vs {data['option_b']}")
            return generate_comparison_card(
                option_a=data["option_a"],
                option_b=data["option_b"],
                winner=data.get("winner", data["option_a"]),
                pros_a=data.get("pros_a", []),
                pros_b=data.get("pros_b", []),
            )

        logger.info(f"CardFromPost: Generating standard card — '{data.get('title', 'N/A')[:60]}'")
        return generate_card(
            title=data.get("title", "Tech Insights"),
            points=data.get("points", []),
        )

    except Exception as e:
        logger.error(f"CardFromPost: ❌ Exception: {type(e).__name__}: {e}")
        return None
