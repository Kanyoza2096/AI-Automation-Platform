"""
Prompt Builder Service.
Assembles complete LLM prompts using Brand and AIProfile contexts.
"""
from typing import Optional, List, Dict, Any

from models.brand import Brand
from models.ai_profile import AIProfile

class PromptBuilder:
    """Service responsible for generating context-aware prompts."""

    def __init__(self):
        # We can add an LRU cache here later for performance
        pass

    @staticmethod
    def _default_brand() -> "Brand":
        from models.brand import Brand as _Brand
        return _Brand.create_default("default")

    @staticmethod
    def _default_ai_profile() -> "AIProfile":
        from models.ai_profile import AIProfile as _AIProfile
        return _AIProfile.create_default("default")

    def build_post_prompt(
        self,
        brand: Optional["Brand"] = None,
        ai_profile: Optional["AIProfile"] = None,
        topic: Optional[str] = None,
        knowledge_context: Optional[str] = None
    ) -> str:
        """Builds a comprehensive prompt for generating a social media post.

        Both brand and ai_profile are optional — when None, canonical defaults
        are used so this method never crashes on the legacy single-tenant path.
        """
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()

        # Base Persona from AI Profile
        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        # Brand strategy context
        brand_context = f"Brand Tone: {brand.tone}\n"
        brand_context += f"Brand Language: {brand.language}\n"
        if getattr(brand, "hashtags", None):
            brand_context += f"Default Hashtags: {brand.formatted_hashtags}\n"

        # Topic
        topic_str = (
            f"Topic: {topic}"
            if topic
            else "Topic: Please select an engaging and highly relevant topic for the audience."
        )

        # Knowledge Context
        knowledge_str = (
            f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n"
            if knowledge_context
            else ""
        )

        # Actionable Task
        task = f"""
Task:
Write a highly engaging social media post for {brand.name}.
{topic_str}

Guidelines:
1. Write in the exact tone and style of the persona defined above.
2. Structure the content beautifully (paragraphs, lists if necessary).
3. Follow the requested emoji density strictly.
4. Include a clear call to action (CTA) matching the '{ai_profile.cta_style}' style.
5. End with appropriate hashtags.

{brand_context}
{knowledge_str}
"""
        return f"{system_prompt}\n\n{task}"

    def build_chat_prompt(
        self,
        brand: Optional["Brand"] = None,
        ai_profile: Optional["AIProfile"] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Builds a prompt for engaging in conversational chat (e.g., Messenger).

        Both brand and ai_profile are optional — when None, canonical defaults
        are used so this method never crashes on the legacy single-tenant path.
        """
        if brand is None:
            brand = self._default_brand()
        if ai_profile is None:
            ai_profile = self._default_ai_profile()
        if user_profile is None:
            user_profile = {}

        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name,
            audience=brand.audience_description,
        )

        user_name = user_profile.get("name", "User")

        chat_context = f"""
Task Context:
You are conversing directly with {user_name} in a chat interface.
Brand Language: {brand.language}

Guidelines:
1. Respond naturally, concisely, and consistently with your persona.
2. Keep responses brief since this is a chat interface.
3. Address the user's questions or comments thoughtfully.
4. Do NOT sound like a generic AI assistant. Maintain the {ai_profile.name} identity.
"""
        return f"{system_prompt}\n\n{chat_context}"

# Singleton
prompt_builder = PromptBuilder()
