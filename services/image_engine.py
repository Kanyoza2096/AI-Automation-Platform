"""
Image Engine v3.1 — Comparison Diagrams
Generates professional comparison diagrams using Kroki.io + Mermaid.
Picsum fallback when diagrams fail.
No Pillow dependency.
"""

import logging
import json

from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.image_engine")

KROKI_BASE = "https://kroki.io"

_kroki_http  = HttpClient("kroki",  base_url=KROKI_BASE,                   default_timeout=15)
_picsum_http = HttpClient("picsum", base_url="https://picsum.photos",       default_timeout=15)


def _kroki(diagram_code: str, diagram_type: str = "mermaid") -> bytes:
    """Send diagram code to Kroki, get PNG back. Free, no API key."""
    try:
        url = f"{KROKI_BASE}/{diagram_type}/png"
        response = _kroki_http.post(url, data=diagram_code.encode(), timeout=15)
        if response.status_code == 200:
            logger.info("✅ Diagram generated via Kroki")
            return response.content
        logger.warning(f"Kroki returned HTTP {response.status_code}")
    except Exception as e:
        logger.warning(f"Kroki error: {e}")
    return None


def _picsum(seed: str) -> bytes:
    """Get a Picsum image as fallback. Free, no API key."""
    try:
        clean_seed = seed.replace(' ', '-').replace('?', '').replace(':', '').replace("'", '')[:30]
        url = f"https://picsum.photos/seed/{clean_seed}/1200/630"
        response = _picsum_http.get(url, timeout=15)
        if response.status_code == 200:
            logger.info("✅ Picsum fallback used")
            return response.content
    except Exception as e:
        logger.warning(f"Picsum error: {e}")
    return None


def _extract_from_post(post_content: str) -> dict:
    """Use Gemini to extract comparison data from post content."""
    try:
        from services.gemini import gemini

        prompt = (
            "Extract comparison data from this tech post.\n\n"
            "Return ONLY valid JSON with these fields:\n"
            '- "option_a": Name of first option (1-3 words)\n'
            '- "option_b": Name of second option (1-3 words)\n'
            '- "winner": Which one wins (1-3 words)\n'
            '- "points_a": Array of 3 short pros for option_a (max 6 words each)\n'
            '- "points_b": Array of 3 short pros for option_b (max 6 words each)\n\n'
            f"POST:\n{post_content[:1000]}\n\n"
            'JSON:'
        )

        result = gemini._call(
            prompt=prompt, temperature=0.3, max_tokens=800
        )

        if result:
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1]
                if "```" in result:
                    result = result.split("```")[0]
            return json.loads(result)

    except Exception as e:
        logger.warning(f"Failed to extract from post: {e}")

    return None


def generate_comparison_diagram(post_content: str) -> bytes:
    """Generate a side-by-side comparison diagram."""
    extracted = _extract_from_post(post_content)
    if not extracted:
        logger.warning("Could not extract comparison data")
        return None

    option_a = extracted.get("option_a", "Option A")
    option_b = extracted.get("option_b", "Option B")
    winner = extracted.get("winner", option_a)
    points_a = extracted.get("points_a", ["Feature 1", "Feature 2", "Feature 3"])
    points_b = extracted.get("points_b", ["Feature 1", "Feature 2", "Feature 3"])

    while len(points_a) < 3:
        points_a.append("Reliable")
    while len(points_b) < 3:
        points_b.append("Popular")

    diagram = f"""graph TB
    subgraph A["🅰️ {option_a}"]
    A1["✅ {points_a[0]}"]
    A2["✅ {points_a[1]}"]
    A3["✅ {points_a[2]}"]
    end

    subgraph B["🅱️ {option_b}"]
    B1["✅ {points_b[0]}"]
    B2["✅ {points_b[1]}"]
    B3["✅ {points_b[2]}"]
    end

    W["🏆 Winner: {winner}"]

    style W fill:#4F46E5,stroke:#4F46E5,color:#fff
    style A fill:#1E293B,stroke:#4F46E5,color:#F1F5F9
    style B fill:#1E293B,stroke:#3B82F6,color:#F1F5F9
    """

    return _kroki(diagram, "mermaid")


def generate_best_image(topic: str, post_content: str, template_type: str) -> bytes:
    """
    Smart image generator.
    1. Comparison diagram via Kroki/Mermaid (primary)
    2. Picsum fallback (always works)
    """
    image = generate_comparison_diagram(post_content)
    if image:
        return image

    return _picsum(topic)
