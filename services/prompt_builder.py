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

    def build_post_prompt(
        self, 
        brand: Brand, 
        ai_profile: AIProfile, 
        topic: Optional[str] = None, 
        knowledge_context: Optional[str] = None
    ) -> str:
        """Builds a comprehensive prompt for generating a social media post."""
        
        # Base Persona from AI Profile
        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name, 
            audience=brand.audience_description
        )
        
        # Brand strategy context
        brand_context = f"Brand Tone: {brand.tone}\n"
        brand_context += f"Brand Language: {brand.language}\n"
        if brand.hashtags:
            brand_context += f"Default Hashtags: {brand.formatted_hashtags}\n"
        
        # Topic
        topic_str = f"Topic: {topic}" if topic else "Topic: Please select an engaging and highly relevant topic for the audience."
        
        # Knowledge Context
        knowledge_str = f"\nUse the following knowledge base facts if relevant:\n{knowledge_context}\n" if knowledge_context else ""
        
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
        brand: Brand, 
        ai_profile: AIProfile, 
        user_profile: Dict[str, Any], 
        conversation_history: List[Dict[str, str]]
    ) -> str:
        """Builds a prompt for engaging in conversational chat (e.g., Messenger)."""
        system_prompt = ai_profile.build_system_prompt(
            brand_name=brand.name, 
            audience=brand.audience_description
        )
        
        user_name = user_profile.get('name', 'User')
        
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
