"""
Hugging Face AI Service v6.4
Free models for fallback and media generation.
Hybrid thumbnail generation with Picsum fallback (guaranteed to work).
"""

import re
import logging
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.huggingface")

# Shared HTTP client — auth token passed per-request via self.headers
_http = HttpClient("huggingface", default_timeout=30)

API_BASE = "https://api-inference.huggingface.co/models"

MODELS = {
    "chat": "mistralai/Mistral-7B-Instruct-v0.3",
    "sentiment": "cardiffnlp/twitter-roberta-base-sentiment-latest",
    "thumbnail": "black-forest-labs/FLUX.1-schnell",
}


class HuggingFaceService:
    """Free AI service using Hugging Face Inference API."""

    def __init__(self):
        self.token = settings.hf_token
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    def chat(self, prompt: str) -> Optional[str]:
        """Generate a chat response using Mistral (free fallback)."""
        if not self.is_configured:
            return None

        try:
            url = f"{API_BASE}/{MODELS['chat']}"
            payload = {
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 300,
                    "temperature": 0.7,
                    "return_full_text": False,
                }
            }
            response = _http.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code == 200:
                result = response.json()
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("generated_text", "").strip()
            else:
                logger.warning(f"HF chat failed: {response.status_code}")

        except Exception as e:
            logger.error(f"HF chat error: {e}")

        return None

    def analyze_sentiment(self, text: str) -> str:
        """Detect sentiment: positive, neutral, or negative."""
        if not self.is_configured:
            return "neutral"

        try:
            url = f"{API_BASE}/{MODELS['sentiment']}"
            response = _http.post(
                url, headers=self.headers,
                json={"inputs": text[:500]}, timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if isinstance(result, list) and len(result) > 0:
                    if isinstance(result[0], list):
                        label = result[0][0].get("label", "neutral")
                    else:
                        label = result[0].get("label", "neutral")
                    return label.lower()

        except Exception as e:
            logger.error(f"HF sentiment error: {e}")

        return "neutral"

    def _sanitize_seed(self, text: str) -> str:
        """Generate a safe seed string for Picsum."""
        seed = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower())
        return seed.strip("-")[:40]

    def _generate_flux_image(self, prompt: str) -> Optional[bytes]:
        """Generate an image using the FLUX model on Hugging Face."""
        if not self.is_configured:
            return None

        url = f"{API_BASE}/{MODELS['thumbnail']}"

        try:
            response = _http.post(url, headers=self.headers, json={"inputs": prompt}, timeout=90)

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if content_type.startswith("image/"):
                    logger.info("✅ FLUX thumbnail generated successfully.")
                    return response.content
                logger.warning("FLUX returned HTTP 200 but not an image.")
            elif response.status_code == 503:
                logger.warning("FLUX model is loading.")
            elif response.status_code == 429:
                logger.warning("FLUX rate limit exceeded.")
            else:
                logger.warning(f"FLUX returned HTTP {response.status_code}")

        except Exception:  # Timeout
            logger.warning("FLUX request timed out.")
        except Exception:  # ConnectionError
            logger.warning("Unable to connect to Hugging Face.")
        except Exception as exc:  # RequestException
            logger.error(f"FLUX request failed: {exc}")

        return None

    def _picsum_fallback(self, seed_text: str) -> Optional[bytes]:
        """Download a deterministic placeholder image from Picsum."""
        seed = self._sanitize_seed(seed_text)
        url = f"https://picsum.photos/seed/{seed}/1200/630"

        try:
            response = _http.get(url, timeout=15)
            if response.status_code == 200:
                logger.info("✅ Using Picsum fallback image.")
                return response.content
            logger.warning(f"Picsum returned HTTP {response.status_code}")
        except Exception:  # Timeout
            logger.warning("Picsum timeout.")
        except Exception:  # ConnectionError
            logger.warning("Unable to connect to Picsum.")
        except Exception as exc:  # RequestException
            logger.error(f"Picsum request failed: {exc}")

        return None

    def generate_thumbnail(self, topic: str) -> Optional[bytes]:
        """Generate a thumbnail from a topic. Flow: FLUX → Picsum."""
        prompt = (
            f"A clean, modern tech illustration representing {topic}. "
            "Dark theme with neon blue and purple accents. "
            "Professional developer aesthetic. "
            "Minimalist. No text. High quality. "
            "Suitable for a technology blog thumbnail."
        )
        return self._generate_flux_image(prompt) or self._picsum_fallback(topic)

    def generate_thumbnail_from_post(self, post_content: str) -> Optional[bytes]:
        """Generate a thumbnail from post content. Flow: Gemini → FLUX → Picsum."""
        image_prompt = self._generate_image_prompt(post_content)
        if image_prompt:
            image = self._generate_flux_image(image_prompt)
            if image:
                return image
        return self._picsum_fallback(post_content)

    def _generate_image_prompt(self, post_content: str) -> Optional[str]:
        """Use Gemini to create an optimized image prompt from the post."""
        try:
            from services.gemini import gemini

            prompt = (
                "You are an expert at creating image generation prompts. "
                "Read this Facebook post and create a short, detailed image prompt "
                "for an AI image model (FLUX). The image should be:\n"
                "- A clean, modern tech illustration\n"
                "- Dark theme with neon blue and purple accents\n"
                "- Professional developer aesthetic\n"
                "- Minimalist, no text, no words\n"
                "- Directly relevant to the post topic\n\n"
                f"POST CONTENT:\n{post_content[:1500]}\n\n"
                "Image prompt (one detailed sentence describing the visual scene):"
            )

            image_prompt = gemini.generate_raw(prompt, temperature=0.7)

            if image_prompt:
                image_prompt = image_prompt.strip().strip('"').strip("'")
                if len(image_prompt) > 10:
                    return image_prompt
            return None

        except Exception as e:
            logger.error(f"Failed to generate image prompt: {e}")
            return None

    def generate_thumbnail_from_article(self, title: str, category: str) -> Optional[bytes]:
        """Generate a thumbnail for a news article. Flow: FLUX → Picsum."""
        prompt = (
            f"A professional tech news thumbnail for: {title}. "
            f"Category: {category}. "
            f"Modern, clean design with tech elements. "
            f"Dark background with subtle gradients. No text."
        )
        return self._generate_flux_image(prompt) or self._picsum_fallback(f"{category}-{title}")


huggingface = HuggingFaceService()
