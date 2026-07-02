"""
Google Gemini AI Service v8.0
Handles chat responses, content post generation, comment replies,
image prompt crafting, and conversation summarization.
- Quality-validated chat responses
- Multi-template post generation with word count enforcement
- Identity leak prevention
- Paragraph break enforcement
- Hugging Face fallback support
"""

import logging
import re
import time
from typing import Optional, List, Dict

import requests

from config.settings import settings
from core.memory import memory
from utils.retry import smart_retry

logger = logging.getLogger("kanyoza.services.gemini")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_REQUEST_TIMEOUT = 45
RATE_LIMIT_BACKOFF_SECONDS = 30

TECH_KEYWORDS = [
    "python", "react", "database", "api", "linux", "server",
    "supabase", "javascript", "bug", "error", "deploy", "code",
    "function", "query", "sql", "git", "docker", "node", "typescript",
    "how to", "how do i", "what is", "explain", "debug", "fix",
    "install", "setup", "configure", "connect"
]

_BAD_PHRASES_PATTERN = re.compile(
    r"(as an ai|i'd be happy to help|i am madalitso|my name is madalitso|"
    r"certainly|here's a detailed explanation|let me explain|i'm here to help|"
    r"how can i assist|i understand your concern|thank you for reaching out|"
    r"sorry, i got a bit distracted|can you say that again|i got distracted|"
    r"i was distracted|i zoned out|my mind wandered|i lost my train of thought)",
    flags=re.IGNORECASE
)

MIN_POST_WORDS = 60
MAX_QUALITY_RETRIES = 2
MIN_PARAGRAPHS = 1
MAX_USER_NAME_LENGTH = 80
MAX_COMMENT_LENGTH = 500
MAX_MESSAGE_HISTORY_CHARS = 2000

_SAFETY_LEVELS = {
    "off": "BLOCK_NONE",
    "low": "BLOCK_ONLY_HIGH",
    "medium": "BLOCK_MEDIUM_AND_ABOVE",
    "high": "BLOCK_LOW_AND_ABOVE",
}

COMMON_POST_RULES = """
CRITICAL RULES:
- Write 80-120 words. Comparison posts are concise.
- Be specific with numbers and facts.
- Pick a clear winner — don't hedge.
- Write in flawless, professional English.
- Never use filler phrases.
- Never introduce yourself or refer to yourself as an AI.
- DO NOT include section labels like "SECTION 1" or "TITLE:" in the output.
- In ALL code examples, NEVER use hardcoded credentials.
- The post should be a natural paragraph, not labeled sections.
"""
TEMPLATES = {
    "deep_dive": """
You are writing a comparison-style tech post. Compare two approaches to this topic.

TOPIC: {topic}

Create a comparison with these sections:

TITLE: "[Option A] vs [Option B]: What You Should Use"

THE SETUP: 1 sentence.

COMPARISON (3-4 points with specific metrics):
| Aspect | Option A | Option B |
| Speed | [specific] | [specific] |
| Cost | [specific] | [specific] |
| Scale | [specific] | [specific] |

WINNER: Pick one with a specific reason. Be decisive.

QUESTION: "Which do you use — Option A or Option B?"

RULES:
- Use specific numbers
- Pick a clear winner
- Under 120 words
- No section labels like "SECTION 1" in output
- No AI introductions
""",

    "comparison": """
You are writing a technical comparison post.

TOPIC: {topic}

TITLE: "[Option A] vs [Option B]: Which One Wins?"

SETUP: 1 sentence.

COMPARISON (3-4 points with metrics).

WINNER: One clear winner with reason.

QUESTION: "Option A or Option B? What's your pick?"

RULES:
- Be specific with numbers
- Pick a winner
- Under 120 words
- No section labels
""",

    "refactoring": """
Compare old way vs new way for this topic.

TOPIC: {topic}

TITLE: "The Old Way vs The Right Way: [Topic]"

SETUP: 1 sentence on why the old way fails.

COMPARISON:
| Aspect | Old Way | New Way |
| Speed | [before] | [after] |
| Lines | [before] | [after] |
| Bugs | [before] | [after] |

WINNER: The new way. 1 sentence why.

QUESTION: "Still using the old way? What's holding you back?"

RULES:
- Show specific metrics
- Under 100 words
- No section labels
""",

    "tutorial": """
Compare two ways to achieve this.

TOPIC: {topic}

TITLE: "[Topic]: Simple Way vs Pro Way"

SETUP: 1 sentence.

COMPARISON (3 points):
| Aspect | Simple Way | Pro Way |
| Time | [time] | [time] |
| Features | [what] | [what] |
| Best For | [who] | [who] |

WINNER: 1 sentence.

QUESTION: "Which approach do you use?"

RULES:
- Specific comparisons
- Under 100 words
- No section labels
""",

    "quick_tip": """
Compare wrong way vs right way.

TOPIC: {topic}

TITLE: "Stop Doing [Wrong]. Do [Right] Instead."

SETUP: 1 sentence.

THE FIX: 1 sentence with the right approach.

CODE: Show old vs new (max 3 lines each).

QUESTION: "Were you doing it the old way?"

RULES:
- Under 80 words
- Code shows both old and new
- No section labels
""",
}

class GeminiServiceError(Exception):
    pass


class GeminiRateLimited(Exception):
    pass


class GeminiService:
    """Production-grade Gemini API client."""

    def __init__(self):
        self.api_key = settings.gemini_key
        self.model = settings.gemini_model
        self._chat_temp = settings.gemini_chat_temperature or 0.7
        self._post_temp = settings.gemini_post_temperature or 0.65
        self._safety = _SAFETY_LEVELS.get(settings.gemini_safety_level, "BLOCK_MEDIUM_AND_ABOVE")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._last_rate_limit_time: float = 0.0

    def _safety_settings(self):
        return [{"category": cat, "threshold": self._safety} for cat in [
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]]

    def _call(self, prompt: str, temperature: float, max_tokens: int) -> str:
        if time.time() - self._last_rate_limit_time < RATE_LIMIT_BACKOFF_SECONDS:
            raise GeminiRateLimited("Preemptive backoff")

        url = f"{GEMINI_BASE}/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}], "role": "user"}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens, "topP": 0.92},
            "safetySettings": self._safety_settings(),
        }

        response = self._session.post(url, json=payload, timeout=_REQUEST_TIMEOUT)

        if response.status_code == 429:
            self._last_rate_limit_time = time.time()
            raise GeminiRateLimited("Rate limited")
        if response.status_code != 200:
            raise GeminiServiceError(f"HTTP {response.status_code}")

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise GeminiServiceError(f"Blocked: {data.get('promptFeedback', {}).get('blockReason', 'unknown')}")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts or "text" not in parts[0]:
            raise GeminiServiceError("Missing text")

        return parts[0]["text"].strip()

    def _strip_bad_phrases(self, text: str) -> str:
        return ' '.join(_BAD_PHRASES_PATTERN.sub("", text).split()).strip()

    def _sanitize(self, value, max_length: int) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("\r", " ").strip()[:max_length]

    def _build_context(self, messages: list) -> str:
        if not messages:
            return ""
        lines, total = [], 0
        for msg in reversed(messages):
            role = "Friend" if msg['role'] == 'user' else "You"
            text = str(msg.get('text', ''))[:200]
            line = f"{role}: {text}"
            if total + len(line) > MAX_MESSAGE_HISTORY_CHARS:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(reversed(lines))

    def _fallback_response(self, language: str) -> str:
        if language == "chichewa":
            return "Pepani, netiweki ikuvuta pang'ono pano. Tiyezanso pakadali pano."
        return "Ah, my connection is acting up. Give me a sec to retry?"

    def _validate_chat_quality(self, reply: str, language: str) -> str:
        patterns = [
            r"sorry.*distracted", r"can you say that again",
            r"i (got|was|am) (distracted|confused|lost)",
            r"my mind wandered", r"i zoned out",
            r"i lost (my |the )?train of thought",
        ]
        reply_lower = reply.lower().strip()
        for p in patterns:
            if re.search(p, reply_lower):
                return self._fallback_response(language)
        if len(reply.split()) < 3:
            return self._fallback_response(language)
        return reply

    def _strip_section_headers(self, text: str) -> str:
        patterns = [
            r'###?\s*SECTION\s*\d+\s*[-—–]\s*[A-Z\s]+\n?',
            r'###?\s*THE\s+(?:HOOK|DILEMMA|CODE\s*SMELL|REFACTOR|INSIGHT|RESULT|FIX|METRICS|GOLDEN\s*RULE|VERDICT)\s*\n?',
            r'###?\s*(?:HOW\s+IT\s+WORKS|WHY\s+IT\s+(?:FAILS|WORKS)|WHAT\s+YOU\'?LL\s+BUILD|HEAD-TO-HEAD\s+COMPARISON|CODE\s+(?:DEMONSTRATION|COMPARISON)|STEP-BY-STEP|DISCUSSION\s+QUESTION|NEXT\s+STEPS|PREREQUISITES)\s*\n?',
        ]
        for p in patterns:
            text = re.sub(p, '', text, flags=re.IGNORECASE)
        return text.strip()

    def _ensure_paragraph_breaks(self, text: str) -> str:
        paragraphs = [p for p in text.split('\n\n') if p.strip()]
        if len(paragraphs) < MIN_PARAGRAPHS:
            sentences = re.split(r'(?<=[.!?])\s+', text)
            new_paragraphs = []
            current = []
            for s in sentences:
                current.append(s)
                if len(current) >= 2:
                    new_paragraphs.append(' '.join(current))
                    current = []
            if current:
                new_paragraphs.append(' '.join(current))
            if len(new_paragraphs) > 1:
                text = '\n\n'.join(new_paragraphs)
        return text

    def _strengthen_prompt(self, original: str, prev_words: int) -> str:
        return original + f"""

!!!!! QUALITY REJECTION !!!!!
Previous response: {prev_words} words. Minimum: {MIN_POST_WORDS}.
Expand every section. Add depth. Write at least {MIN_POST_WORDS + 50} words.
Use blank lines between paragraphs. Never hardcode credentials.
Generate the COMPLETE post now:
"""

    def _build_system_prompt(self, lang: str, is_technical: bool) -> str:
        return (
            "You are a confident, knowledgeable software engineer chatting on Messenger. "
            "You are NOT an AI. NOT a bot. You're a real person.\n"
            f"{lang}\n"
            f"{'Give substantive answers with code if helpful.' if is_technical else 'Reply warmly and naturally.'}\n"
            'NEVER say: "Sorry I got distracted", "I\'d be happy to help", "Certainly!", "As an AI"'
        )

    def _build_chat_prompt(self, system_prompt: str, user_name: str, user_interests: list,
                           conversation_context: str, user_message: str) -> str:
        parts = [system_prompt]
        if user_name:
            parts.append(f"Talking to: {user_name}")
        if user_interests:
            parts.append(f"Their interests: {', '.join(user_interests) if isinstance(user_interests, list) else user_interests}")
        if conversation_context:
            parts.append(f"Recent:\n{conversation_context}")
        parts.append(f"User: {user_message}")
        parts.append("Your reply:")
        return "\n".join(parts)

    # ==================================================================
    # PUBLIC API
    # ==================================================================

    @smart_retry(max_retries=3, base_delay=1.5)
    def generate_chat_response(self, sender_id: str, user_message: str,
                                language: str = "english", sentiment: str = "neutral") -> str:
        if not self.api_key:
            return "Having some technical issues — try again in a moment!"

        profile = memory.get_user_profile(sender_id)
        user_name = self._sanitize(profile.get('name'), MAX_USER_NAME_LENGTH)
        user_interests = profile.get('interests', [])
        recent_messages = memory.get_recent_messages(sender_id, limit=6)
        conversation_context = self._build_context(recent_messages)

        lang_instruction = {
            "chichewa": "Reply entirely in Chichewa.",
            "english": "Reply entirely in natural English.",
            "mixed": "Reply in a natural mix of Chichewa and English."
        }.get(language, "Reply in the user's language.")

        is_technical = any(kw in user_message.lower() for kw in TECH_KEYWORDS)
        system_prompt = self._build_system_prompt(lang_instruction, is_technical)

        prompt = self._build_chat_prompt(
            system_prompt=system_prompt, user_name=user_name,
            user_interests=user_interests, conversation_context=conversation_context,
            user_message=user_message,
        )

        try:
            reply = self._call(prompt, temperature=self._chat_temp, max_tokens=500)
            reply = self._strip_bad_phrases(reply)
            return self._validate_chat_quality(reply, language)
        except GeminiRateLimited:
            return self._fallback_response(language)
        except Exception:
            return self._fallback_response(language)

    @smart_retry(max_retries=2, base_delay=2.0)
    def generate_content_post(self, topic: str, template_type: str = "deep_dive") -> Optional[str]:
        if not self.api_key:
            return None

        template = TEMPLATES.get(template_type, TEMPLATES["deep_dive"])
        prompt = template.format(topic=topic)

        for attempt in range(1 + MAX_QUALITY_RETRIES):
            try:
                raw = self._call(prompt, temperature=self._post_temp, max_tokens=2500)
                post = self._strip_bad_phrases(raw)
                post = self._strip_section_headers(post)
                post = self._ensure_paragraph_breaks(post)

                if len(post.split()) >= MIN_POST_WORDS:
                    return post

                logger.warning(f"Post too short ({len(post.split())} words). Attempt {attempt + 1}")
                if attempt < MAX_QUALITY_RETRIES:
                    prompt = self._strengthen_prompt(prompt, len(post.split()))
                elif len(post.split()) >= MIN_POST_WORDS * 0.6:
                    return post
            except (GeminiRateLimited, GeminiServiceError):
                if attempt >= MAX_QUALITY_RETRIES:
                    return None
            except Exception:
                return None
        return None

    def generate_news_post(self, prompt: str) -> Optional[str]:
        if not self.api_key:
            return None
        try:
            post = self._call(prompt + "\n\nMinimum 150 words. Use paragraph breaks.", temperature=0.75, max_tokens=1200)
            post = self._strip_bad_phrases(post)
            return self._ensure_paragraph_breaks(post)
        except Exception:
            return None

    def generate_comment_reply(self, comment: str, username: str) -> Optional[str]:
        if not self.api_key:
            return None
        safe_comment = self._sanitize(comment, MAX_COMMENT_LENGTH)
        safe_username = self._sanitize(username, MAX_USER_NAME_LENGTH)

        prompt = (
            "You are a friendly software engineer replying to a comment. You are NOT an AI.\n"
            "Reply in 1-2 short, warm sentences. Use their name. Never say 'I'd be happy to help' or 'Certainly!'.\n\n"
            f"COMMENTER: {safe_username}\nMESSAGE: {safe_comment}\n\nYOUR REPLY:"
        )
        try:
            reply = self._call(prompt, temperature=0.8, max_tokens=100)
            return self._strip_bad_phrases(reply)
        except Exception:
            return None

    def generate_image_prompt(self, topic: str, post_text: str = "") -> Optional[str]:
        try:
            prompt = (
                f"Create a detailed image generation prompt for a tech thumbnail about: {topic}\n"
                f"Dark theme, neon blue/purple accents, professional, no text.\n"
                f"One sentence, 50-80 words:"
            )
            return self._call(prompt, temperature=0.8, max_tokens=256)
        except Exception:
            return None

    def summarize_conversation(self, messages: List[Dict]) -> Optional[str]:
        lines = [f"{'User' if m.get('role')=='user' else 'Bot'}: {m.get('text','')}" for m in messages]
        return self.generate_raw("Summarize this in 3-4 sentences:\n" + "\n".join(lines), temperature=0.3)

    def generate_raw(self, prompt: str, temperature: float = 0.7) -> Optional[str]:
        try:
            return self._call(prompt, temperature=temperature, max_tokens=2048)
        except Exception:
            return None

    def reload_config(self):
        self.api_key = settings.gemini_key
        self.model = settings.gemini_model

    def close(self):
        self._session.close()


gemini = GeminiService()
