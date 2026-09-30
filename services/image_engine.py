"""
Image Engine v4.0 — High-quality social images with free models.

Priority chain:
  1. Cloudflare Workers AI (FLUX.1-schnell → SDXL Lightning → SDXL)
  2. Hugging Face FLUX.1-schnell (when HF_TOKEN set)
  3. Kroki comparison diagrams (for comparison-style posts)
  4. Picsum seeded stock (last resort)

v4.0: Cloudflare was referenced by API routes but the module was missing;
image path previously fell through to random stock photos — low quality.

v4.1: Added ImageEngineService wrapper class for proper service registration
and health monitoring by the Meta Engine.
v4.2: Made comparison diagram styles configurable and theme-aware.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple

from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.image_engine")

KROKI_BASE = "https://kroki.io"

_kroki_http = HttpClient("kroki", base_url=KROKI_BASE, default_timeout=15)
_picsum_http = HttpClient("picsum", base_url="https://picsum.photos", default_timeout=15)


class DiagramTheme(str, Enum):
    """Available diagram themes for comparison charts."""
    DARK = "dark"
    LIGHT = "light"
    GRADIENT = "gradient"
    MINIMAL = "minimal"
    COLORFUL = "colorful"
    MONOCHROME = "monochrome"


class DiagramType(str, Enum):
    """Supported diagram types."""
    MERMAID = "mermaid"
    GRAPHVIZ = "graphviz"
    BLOCKDIAG = "blockdiag"
    SEQDIAG = "seqdiag"
    ACTDIAG = "actdiag"
    NOMNOML = "nomnoml"
    PLANTUML = "plantuml"
    D2 = "d2"
    DBML = "dbml"


class ComparisonDiagramBuilder:
    """Flexible builder for comparison diagrams with configurable styles."""
    
    def __init__(self, theme: DiagramTheme = DiagramTheme.DARK):
        self.theme = theme
        self._theme_configs = self._load_theme_configs()
    
    def _load_theme_configs(self) -> Dict[str, Dict[str, str]]:
        """Define color schemes for different themes."""
        return {
            DiagramTheme.DARK: {
                "background": "#1E293B",
                "panel_a_bg": "#1E293B",
                "panel_b_bg": "#1E293B",
                "panel_a_border": "#4F46E5",
                "panel_b_border": "#3B82F6",
                "winner_bg": "#4F46E5",
                "winner_border": "#4F46E5",
                "winner_text": "#FFFFFF",
                "panel_text": "#F1F5F9",
                "point_text": "#CBD5E1",
                "title_color": "#F8FAFC",
            },
            DiagramTheme.LIGHT: {
                "background": "#FFFFFF",
                "panel_a_bg": "#F8FAFC",
                "panel_b_bg": "#F8FAFC",
                "panel_a_border": "#6366F1",
                "panel_b_border": "#3B82F6",
                "winner_bg": "#6366F1",
                "winner_border": "#6366F1",
                "winner_text": "#FFFFFF",
                "panel_text": "#1E293B",
                "point_text": "#334155",
                "title_color": "#0F172A",
            },
            DiagramTheme.GRADIENT: {
                "background": "#0F172A",
                "panel_a_bg": "#312E81",
                "panel_b_bg": "#1E3A8A",
                "panel_a_border": "#6366F1",
                "panel_b_border": "#3B82F6",
                "winner_bg": "#7C3AED",
                "winner_border": "#8B5CF6",
                "winner_text": "#FFFFFF",
                "panel_text": "#E2E8F0",
                "point_text": "#CBD5E1",
                "title_color": "#F8FAFC",
            },
            DiagramTheme.MINIMAL: {
                "background": "#FFFFFF",
                "panel_a_bg": "#FFFFFF",
                "panel_b_bg": "#FFFFFF",
                "panel_a_border": "#000000",
                "panel_b_border": "#000000",
                "winner_bg": "#000000",
                "winner_border": "#000000",
                "winner_text": "#FFFFFF",
                "panel_text": "#000000",
                "point_text": "#333333",
                "title_color": "#000000",
            },
            DiagramTheme.COLORFUL: {
                "background": "#FFF7ED",
                "panel_a_bg": "#FEF3C7",
                "panel_b_bg": "#DBEAFE",
                "panel_a_border": "#F59E0B",
                "panel_b_border": "#3B82F6",
                "winner_bg": "#10B981",
                "winner_border": "#059669",
                "winner_text": "#FFFFFF",
                "panel_text": "#1F2937",
                "point_text": "#374151",
                "title_color": "#111827",
            },
            DiagramTheme.MONOCHROME: {
                "background": "#18181B",
                "panel_a_bg": "#27272A",
                "panel_b_bg": "#27272A",
                "panel_a_border": "#52525B",
                "panel_b_border": "#52525B",
                "winner_bg": "#3F3F46",
                "winner_border": "#52525B",
                "winner_text": "#FAFAFA",
                "panel_text": "#E4E4E7",
                "point_text": "#A1A1AA",
                "title_color": "#FAFAFA",
            },
        }
    
    def build_mermaid(
        self,
        option_a: str,
        option_b: str,
        winner: str,
        points_a: List[str],
        points_b: List[str],
        title: Optional[str] = None,
        layout: str = "TB",  # TB (top-bottom), LR (left-right), BT (bottom-top), RL (right-left)
    ) -> str:
        """Build a mermaid diagram with the configured theme."""
        theme = self._theme_configs[self.theme]
        
        # Normalize points to have the same length
        max_points = max(len(points_a), len(points_b))
        points_a = points_a + ["—"] * (max_points - len(points_a))
        points_b = points_b + ["—"] * (max_points - len(points_b))
        
        # Build the diagram
        lines = [f"graph {layout}"]
        
        # Add title if provided
        if title:
            safe_title = title.replace('"', '\\"')
            lines.append(f'    T["📊 {safe_title}"]')
            lines.append(f'    style T fill:{theme["winner_bg"]},stroke:{theme["winner_border"]},color:{theme["winner_text"]},font-weight:bold')
        
        # Panel A
        lines.append(f'    subgraph A["🅰️ {option_a}"]')
        for i, point in enumerate(points_a, 1):
            safe_point = point.replace('"', '\\"')
            lines.append(f'    A{i}["✅ {safe_point}"]')
        lines.append("    end")
        
        # Panel B
        lines.append(f'    subgraph B["🅱️ {option_b}"]')
        for i, point in enumerate(points_b, 1):
            safe_point = point.replace('"', '\\"')
            lines.append(f'    B{i}["✅ {safe_point}"]')
        lines.append("    end")
        
        # Winner
        lines.append(f'    W["🏆 Winner: {winner}"]')
        
        # Apply styles
        lines.extend([
            f'    style W fill:{theme["winner_bg"]},stroke:{theme["winner_border"]},color:{theme["winner_text"]},font-weight:bold',
            f'    style A fill:{theme["panel_a_bg"]},stroke:{theme["panel_a_border"]},color:{theme["panel_text"]}',
            f'    style B fill:{theme["panel_b_bg"]},stroke:{theme["panel_b_border"]},color:{theme["panel_text"]}',
        ])
        
        # Style individual points
        for i in range(1, max_points + 1):
            lines.append(f'    style A{i} fill:{theme["panel_a_bg"]},stroke:{theme["panel_a_border"]},color:{theme["point_text"]}')
            lines.append(f'    style B{i} fill:{theme["panel_b_bg"]},stroke:{theme["panel_b_border"]},color:{theme["point_text"]}')
        
        return "\n".join(lines)
    
    def build_graphviz(
        self,
        option_a: str,
        option_b: str,
        winner: str,
        points_a: List[str],
        points_b: List[str],
        title: Optional[str] = None,
    ) -> str:
        """Build a Graphviz diagram with the configured theme."""
        theme = self._theme_configs[self.theme]
        
        lines = ["digraph comparison {"]
        lines.append(f'    bgcolor="{theme["background"]}"')
        lines.append('    rankdir=TB')
        lines.append('    node [fontname="Arial"]')
        
        # Title
        if title:
            safe_title = title.replace('"', '\\"')
            lines.append(f'    title [label="{safe_title}", shape=plaintext, fontsize=20, fontcolor="{theme["title_color"]}"]')
        
        # Panel A
        lines.append(f'    subgraph cluster_a {{')
        lines.append(f'        label="{option_a}"')
        lines.append(f'        style=filled')
        lines.append(f'        fillcolor="{theme["panel_a_bg"]}"')
        lines.append(f'        color="{theme["panel_a_border"]}"')
        lines.append(f'        fontcolor="{theme["panel_text"]}"')
        for i, point in enumerate(points_a, 1):
            safe_point = point.replace('"', '\\"')
            lines.append(f'        a{i} [label="✅ {safe_point}", fillcolor="{theme["panel_a_bg"]}", color="{theme["panel_a_border"]}", fontcolor="{theme["point_text"]}"]')
        lines.append('    }')
        
        # Panel B
        lines.append(f'    subgraph cluster_b {{')
        lines.append(f'        label="{option_b}"')
        lines.append(f'        style=filled')
        lines.append(f'        fillcolor="{theme["panel_b_bg"]}"')
        lines.append(f'        color="{theme["panel_b_border"]}"')
        lines.append(f'        fontcolor="{theme["panel_text"]}"')
        for i, point in enumerate(points_b, 1):
            safe_point = point.replace('"', '\\"')
            lines.append(f'        b{i} [label="✅ {safe_point}", fillcolor="{theme["panel_b_bg"]}", color="{theme["panel_b_border"]}", fontcolor="{theme["point_text"]}"]')
        lines.append('    }')
        
        # Winner
        lines.append(f'    winner [label="🏆 Winner: {winner}", shape=box, style=filled, fillcolor="{theme["winner_bg"]}", color="{theme["winner_border"]}", fontcolor="{theme["winner_text"]}", fontsize=16]')
        
        # Connect panels to winner
        lines.append('    a1 -> winner [style=dashed, color="#666666"]')
        lines.append('    b1 -> winner [style=dashed, color="#666666"]')
        
        lines.append('}')
        return "\n".join(lines)


def _kroki(diagram_code: str, diagram_type: str = "mermaid") -> Optional[bytes]:
    try:
        url = f"{KROKI_BASE}/{diagram_type}/png"
        response = _kroki_http.post(url, data=diagram_code.encode(), timeout=15)
        if response.status_code == 200:
            logger.info("Diagram generated via Kroki")
            return response.content
        logger.warning("Kroki returned HTTP %s", response.status_code)
    except Exception as e:
        logger.warning("Kroki error: %s", e)
    return None


def _picsum(seed: str) -> Optional[bytes]:
    try:
        clean_seed = (
            seed.replace(" ", "-")
            .replace("?", "")
            .replace(":", "")
            .replace("'", "")[:30]
            or "tech"
        )
        url = f"https://picsum.photos/seed/{clean_seed}/1200/630"
        response = _picsum_http.get(url, timeout=15)
        if response.status_code == 200:
            logger.info("Picsum fallback used (seed=%s)", clean_seed)
            return response.content
    except Exception as e:
        logger.warning("Picsum error: %s", e)
    return None


def _cloudflare_image(topic: str, post_content: str) -> Optional[bytes]:
    try:
        from services.cloudflare_image import generate_for_post, is_configured
        if not is_configured():
            return None
        return generate_for_post(topic=topic, caption=post_content or "")
    except Exception as exc:
        logger.warning("Cloudflare image path failed: %s", exc)
        return None


def _huggingface_flux(topic: str, post_content: str) -> Optional[bytes]:
    try:
        from services.huggingface import huggingface
        if not getattr(huggingface, "is_configured", False):
            return None
        # Prefer content-aware prompt when available
        if post_content and hasattr(huggingface, "generate_thumbnail_from_post"):
            img = huggingface.generate_thumbnail_from_post(post_content)
            if img:
                return img
        if hasattr(huggingface, "generate_thumbnail"):
            return huggingface.generate_thumbnail(topic)
    except Exception as exc:
        logger.warning("HF FLUX path failed: %s", exc)
    return None


def _extract_from_post(post_content: str) -> Optional[dict]:
    try:
        from services.gemini import gemini
        prompt = (
            "Extract comparison data from this tech post.\n\n"
            "Return ONLY valid JSON with these fields:\n"
            '- "option_a": Name of first option (1-3 words)\n'
            '- "option_b": Name of second option (1-3 words)\n'
            '- "winner": Which one wins (1-3 words)\n'
            '- "points_a": Array of 3 short pros for option_a (max 6 words each)\n'
            '- "points_b": Array of 3 short pros for option_b (max 6 words each)\n'
            '- "title": Optional comparison title (3-8 words)\n'
            f"POST:\n{post_content[:1000]}\n\n"
            "JSON:"
        )
        result = gemini._call(prompt=prompt, temperature=0.3, max_tokens=800)
        if result:
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[1]
                if "```" in result:
                    result = result.split("```")[0]
            return json.loads(result)
    except Exception as e:
        logger.warning("Failed to extract comparison data: %s", e)
    return None


def generate_comparison_diagram(
    post_content: str,
    theme: DiagramTheme = DiagramTheme.DARK,
    diagram_type: DiagramType = DiagramType.MERMAID,
    layout: str = "TB",
) -> Optional[bytes]:
    """
    Generate a comparison diagram with configurable style.
    
    Args:
        post_content: The post content containing comparison data
        theme: Visual theme for the diagram (default: DARK)
        diagram_type: Type of diagram to generate (default: MERMAID)
        layout: Layout direction for mermaid diagrams (TB, LR, BT, RL)
    
    Returns:
        Diagram bytes if successful, None otherwise
    """
    extracted = _extract_from_post(post_content)
    if not extracted:
        return None

    option_a = extracted.get("option_a", "Option A")
    option_b = extracted.get("option_b", "Option B")
    winner = extracted.get("winner", option_a)
    title = extracted.get("title", f"{option_a} vs {option_b}")
    points_a = extracted.get("points_a", ["Feature 1", "Feature 2", "Feature 3"])
    points_b = extracted.get("points_b", ["Feature 1", "Feature 2", "Feature 3"])
    
    # Normalize points arrays
    while len(points_a) < 3:
        points_a.append("Reliable")
    while len(points_b) < 3:
        points_b.append("Popular")
    
    # Build the diagram based on type
    builder = ComparisonDiagramBuilder(theme=theme)
    
    if diagram_type == DiagramType.MERMAID:
        diagram_code = builder.build_mermaid(
            option_a=option_a,
            option_b=option_b,
            winner=winner,
            points_a=points_a,
            points_b=points_b,
            title=title,
            layout=layout,
        )
    elif diagram_type == DiagramType.GRAPHVIZ:
        diagram_code = builder.build_graphviz(
            option_a=option_a,
            option_b=option_b,
            winner=winner,
            points_a=points_a,
            points_b=points_b,
            title=title,
        )
    else:
        # Fallback to mermaid for unsupported types
        diagram_code = builder.build_mermaid(
            option_a=option_a,
            option_b=option_b,
            winner=winner,
            points_a=points_a,
            points_b=points_b,
            title=title,
            layout=layout,
        )
    
    return _kroki(diagram_code, diagram_type.value)


def generate_best_image(
    topic: str,
    post_content: str,
    template_type: str = "auto",
    theme: DiagramTheme = DiagramTheme.DARK,
    diagram_type: DiagramType = DiagramType.MERMAID,
) -> Optional[bytes]:
    """
    Smart image generator for social posts.

    1. Cloudflare Workers AI (FLUX / SDXL) — best free quality
    2. Hugging Face FLUX.1-schnell
    3. Kroki comparison diagram (when post is comparative)
    4. Picsum seeded stock (last resort)
    
    Args:
        topic: The topic or title for the image
        post_content: Optional content to inform image generation
        template_type: Type of template to use (default: auto)
        theme: Theme for comparison diagrams (default: DARK)
        diagram_type: Type of diagram for comparisons (default: MERMAID)
    """
    topic = topic or "technology"
    post_content = post_content or ""

    # 1. Cloudflare free models
    image = _cloudflare_image(topic, post_content)
    if image:
        logger.info("Image source=cloudflare topic=%s bytes=%d", topic[:60], len(image))
        return image

    # 2. Hugging Face FLUX
    image = _huggingface_flux(topic, post_content)
    if image:
        logger.info("Image source=huggingface_flux topic=%s bytes=%d", topic[:60], len(image))
        return image

    # 3. Comparison diagram when content looks comparative
    lowered = post_content.lower()
    if any(w in lowered for w in (" vs ", "versus", "compare", "comparison", "better than")):
        image = generate_comparison_diagram(
            post_content,
            theme=theme,
            diagram_type=diagram_type,
        )
        if image:
            logger.info("Image source=kroki_diagram topic=%s theme=%s", topic[:60], theme.value)
            return image

    # 4. Last resort stock
    image = _picsum(topic)
    if image:
        logger.info("Image source=picsum topic=%s", topic[:60])
    return image


class ImageEngineService:
    """
    Service wrapper for the Image Engine module.
    
    Provides a consistent interface for the Meta Engine to register,
    monitor, and interact with the image generation capabilities.
    """
    
    VERSION = "4.2"
    SERVICE_NAME = "image_engine"
    
    def __init__(
        self,
        default_theme: DiagramTheme = DiagramTheme.DARK,
        default_diagram_type: DiagramType = DiagramType.MERMAID,
    ):
        self.default_theme = default_theme
        self.default_diagram_type = default_diagram_type
        self._request_count = 0
        self._error_count = 0
        self._last_error = None
        self._providers_available = {
            "cloudflare": self._check_cloudflare_available(),
            "huggingface": self._check_huggingface_available(),
            "kroki": True,  # Kroki is always available as a public service
            "picsum": True,  # Picsum is always available as a public service
        }
        self._builder = ComparisonDiagramBuilder(theme=default_theme)
        logger.info(
            "Image Engine Service v%s initialized (theme=%s, diagram=%s)",
            self.VERSION,
            default_theme.value,
            default_diagram_type.value,
        )
    
    def _check_cloudflare_available(self) -> bool:
        """Check if Cloudflare image generation is configured."""
        try:
            from services.cloudflare_image import is_configured
            return is_configured()
        except Exception:
            return False
    
    def _check_huggingface_available(self) -> bool:
        """Check if Hugging Face is configured."""
        try:
            from services.huggingface import huggingface
            return getattr(huggingface, "is_configured", False)
        except Exception:
            return False
    
    def check_health(self) -> dict:
        """Health check for the image engine service."""
        available_providers = [name for name, available in self._providers_available.items() if available]
        status = "healthy" if available_providers else "degraded"
        
        return {
            "status": status,
            "available": len(available_providers) > 0,
            "providers": self._providers_available,
            "available_providers": available_providers,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "version": self.VERSION,
            "default_theme": self.default_theme.value,
            "default_diagram_type": self.default_diagram_type.value,
        }
    
    def get_status(self) -> dict:
        """Get current status of the image engine."""
        health = self.check_health()
        return {
            **health,
            "service": self.SERVICE_NAME,
            "last_error": self._last_error,
        }
    
    def generate_image(
        self,
        topic: str,
        post_content: str = "",
        template_type: str = "auto",
        theme: Optional[DiagramTheme] = None,
        diagram_type: Optional[DiagramType] = None,
    ) -> Optional[bytes]:
        """
        Generate an image using the best available provider.
        
        Args:
            topic: The topic or title for the image
            post_content: Optional content to inform image generation
            template_type: Type of template to use (default: auto)
            theme: Override theme for comparison diagrams (optional)
            diagram_type: Override diagram type for comparisons (optional)
            
        Returns:
            Image bytes if successful, None otherwise
        """
        self._request_count += 1
        try:
            result = generate_best_image(
                topic,
                post_content,
                template_type,
                theme=theme or self.default_theme,
                diagram_type=diagram_type or self.default_diagram_type,
            )
            if result is None:
                self._error_count += 1
                self._last_error = "All image providers failed"
            return result
        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            logger.error("Image generation failed: %s", e)
            return None
    
    def generate_comparison(
        self,
        post_content: str,
        theme: Optional[DiagramTheme] = None,
        diagram_type: Optional[DiagramType] = None,
    ) -> Optional[bytes]:
        """
        Generate a comparison diagram from post content.
        
        Args:
            post_content: The post content containing comparison data
            theme: Override theme for the diagram (optional)
            diagram_type: Override diagram type (optional)
            
        Returns:
            Diagram bytes if successful, None otherwise
        """
        self._request_count += 1
        try:
            result = generate_comparison_diagram(
                post_content,
                theme=theme or self.default_theme,
                diagram_type=diagram_type or self.default_diagram_type,
            )
            if result is None:
                self._error_count += 1
                self._last_error = "Failed to generate comparison diagram"
            return result
        except Exception as e:
            self._error_count += 1
            self._last_error = str(e)
            logger.error("Comparison diagram generation failed: %s", e)
            return None
    
    def get_available_themes(self) -> List[str]:
        """Get list of available diagram themes."""
        return [theme.value for theme in DiagramTheme]
    
    def get_available_diagram_types(self) -> List[str]:
        """Get list of available diagram types."""
        return [dtype.value for dtype in DiagramType]
    
    def get_provider_status(self) -> dict:
        """Get detailed status of each image provider."""
        return {
            "cloudflare": {
                "available": self._providers_available.get("cloudflare", False),
                "description": "FLUX.1-schnell / SDXL via Cloudflare Workers AI",
                "quality": "high" if self._providers_available.get("cloudflare", False) else "unavailable",
            },
            "huggingface": {
                "available": self._providers_available.get("huggingface", False),
                "description": "FLUX.1-schnell via Hugging Face",
                "quality": "high" if self._providers_available.get("huggingface", False) else "unavailable",
            },
            "kroki": {
                "available": self._providers_available.get("kroki", False),
                "description": "Comparison diagrams via Kroki.io",
                "quality": "medium",
                "themes": self.get_available_themes(),
                "diagram_types": self.get_available_diagram_types(),
            },
            "picsum": {
                "available": self._providers_available.get("picsum", False),
                "description": "Seeded stock photos via Picsum",
                "quality": "fallback",
            },
        }


# Create the singleton instance for service registration
image_engine = ImageEngineService()


# Update __all__ to include the new exports
__all__ = [
    "generate_best_image",
    "generate_comparison_diagram",
    "ImageEngineService",
    "image_engine",
    "DiagramTheme",
    "DiagramType",
    "ComparisonDiagramBuilder",
]
