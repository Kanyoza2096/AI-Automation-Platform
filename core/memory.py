"""
Advanced memory system v8.0 — Supabase Persistence
- All state persisted to Supabase
- Matches key-value contact_profiles table structure
- Survives process restarts
- Graceful fallback to in-memory if Supabase unavailable
"""

import json
import re
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger("kanyoza.core.memory")


class MemorySystem:
    """Multi-tier memory system with Supabase persistence."""

    def __init__(self, supabase_client=None):
        self._lock = threading.RLock()
        self.supabase = supabase_client
        self._short_term: Dict[str, List[Dict]] = {}
        self._profiles: Dict[str, Dict] = {}
        self._profile_cache_ttl = timedelta(minutes=30)
        self._profile_cache_times: Dict[str, datetime] = {}
        self._summaries: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def get_recent_messages(self, sender_id: str, limit: int = 10) -> List[Dict]:
        with self._lock:
            messages = self._short_term.get(sender_id, [])
            if messages:
                return messages[-limit:]

        if self.supabase:
            try:
                result = (self.supabase.table('conversation_history')
                    .select('role, text, created_at')
                    .eq('sender_id', sender_id)
                    .order('created_at', desc=True)
                    .limit(limit)
                    .execute())
                if result.data:
                    messages = []
                    for row in reversed(result.data):
                        messages.append({
                            'role': row['role'],
                            'text': row['text'],
                            'timestamp': str(row.get('created_at', '')),
                        })
                    with self._lock:
                        self._short_term[sender_id] = messages
                    return messages[-limit:]
            except Exception as e:
                logger.error("Failed to load messages: %s", e)
        return []

    def add_message(self, sender_id: str, role: str, text: str):
        message = {"role": role, "text": text, "timestamp": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            if sender_id not in self._short_term:
                self._short_term[sender_id] = []
            self._short_term[sender_id].append(message)
            if len(self._short_term[sender_id]) > 50:
                self._short_term[sender_id] = self._short_term[sender_id][-30:]

        if self.supabase:
            try:
                self.supabase.table('conversation_history').insert({
                    'id': f"msg-{uuid.uuid4().hex[:16]}-{sender_id[:8]}",
                    'sender_id': sender_id, 'role': role, 'text': text[:2000],
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.error("Failed to persist message: %s", e)

        if len(self._short_term.get(sender_id, [])) >= 20:
            self._maybe_summarize(sender_id)

    # ------------------------------------------------------------------
    # Profiles (key-value table)
    # ------------------------------------------------------------------

    def get_user_profile(self, sender_id: str) -> Dict:
        with self._lock:
            if sender_id in self._profiles:
                cache_time = self._profile_cache_times.get(sender_id)
                if cache_time and (datetime.now(timezone.utc) - cache_time) < self._profile_cache_ttl:
                    return self._profiles[sender_id]

        profile = self._fetch_profile(sender_id)
        with self._lock:
            self._profiles[sender_id] = profile
            self._profile_cache_times[sender_id] = datetime.now(timezone.utc)
        return profile

    def _fetch_profile(self, sender_id: str) -> Dict:
        if self.supabase:
            try:
                result = (self.supabase.table('contact_profiles')
                    .select('*').eq('sender_id', sender_id).execute())
                if result.data:
                    profile = {
                        'sender_id': sender_id,
                        'name': 'there',
                        'interests': [],
                        'topics_discussed': [],
                        'communication_style': 'neutral',
                        'relationship_score': 0,
                        'total_messages': 0,
                        'first_seen': datetime.now(timezone.utc).isoformat(),
                        'last_seen': datetime.now(timezone.utc).isoformat(),
                    }
                    for row in result.data:
                        key = row.get('fact_key', '')
                        value = row.get('fact_value', '')
                        if not key:
                            continue
                        if key in ('interests', 'topics_discussed'):
                            try:
                                profile[key] = json.loads(value) if value else []
                            except (json.JSONDecodeError, TypeError):
                                profile[key] = [value] if value else []
                        elif key in ('relationship_score', 'total_messages'):
                            try:
                                profile[key] = int(value)
                            except (ValueError, TypeError):
                                pass
                        else:
                            profile[key] = value if value else profile[key]
                    return profile
            except Exception as e:
                logger.error("Failed to fetch profile: %s", e)

        return {
            'sender_id': sender_id, 'name': 'there', 'interests': [],
            'topics_discussed': [], 'communication_style': 'neutral',
            'relationship_score': 0, 'total_messages': 0,
            'first_seen': datetime.now(timezone.utc).isoformat(), 'last_seen': datetime.now(timezone.utc).isoformat(),
        }

    def update_user_profile(self, sender_id: str, facts: Dict[str, Any]):
        profile = self.get_user_profile(sender_id)
        for key, value in facts.items():
            if key in profile and isinstance(profile[key], list) and isinstance(value, list):
                combined = value + [v for v in profile[key] if v not in value]
                profile[key] = combined[:20]
            else:
                profile[key] = value
        profile['last_seen'] = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._profiles[sender_id] = profile
            self._profile_cache_times[sender_id] = datetime.now(timezone.utc)

        if self.supabase:
            try:
                for key, value in facts.items():
                    if isinstance(value, list):
                        value = json.dumps(value)
                    elif not isinstance(value, str):
                        value = str(value)

                    self.supabase.table('contact_profiles').upsert({
                        'sender_id': sender_id,
                        'fact_key': key,
                        'fact_value': str(value)[:500],
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    }, on_conflict='sender_id,fact_key').execute()
            except Exception as e:
                logger.error("Failed to persist profile: %s", e)

    # ------------------------------------------------------------------
    # Fact Extraction
    # ------------------------------------------------------------------

    def extract_and_save_facts(self, sender_id: str, message: str, ai_response: str = ""):
        facts = {}
        message_lower = message.lower()

        name_patterns = [r"(?:my name is|i'm|i am|call me|this is) (\w+(?:\s+\w+)?)"]
        for pattern in name_patterns:
            match = re.search(pattern, message_lower)
            if match:
                name = match.group(1).strip()
                skip = ('not', 'just', 'here', 'there', 'the', 'a', 'an',
                        'your', 'my', 'good', 'fine', 'ok', 'okay', 'well')
                if name not in skip and len(name) > 1:
                    facts['name'] = name.capitalize()
                    break

        interest_patterns = [r"(?:i (?:love|like|enjoy) |i'm (?:into|interested in|learning) )(.+?)(?:\.|,| and| but|$)"]
        for pattern in interest_patterns:
            match = re.search(pattern, message_lower)
            if match:
                interest = match.group(1).strip()[:50]
                if interest and len(interest) > 2:
                    facts['interests'] = [interest]
                    break

        tech_keywords = ['python', 'react', 'database', 'api', 'linux', 'server',
                        'javascript', 'bug', 'error', 'deploy', 'code', 'docker',
                        'sql', 'git', 'node', 'typescript', 'postgresql', 'mongodb']
        mentioned = [kw for kw in tech_keywords if kw in message_lower]
        if mentioned:
            facts['topics_discussed'] = mentioned

        if facts:
            self.update_user_profile(sender_id, facts)

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def _maybe_summarize(self, sender_id: str):
        messages = self._short_term.get(sender_id, [])
        if len(messages) < 20:
            return
        older = messages[:-10]
        if not older:
            return
        topics, questions = set(), []
        for msg in older:
            text = msg.get('text', '')
            if msg.get('role') == 'user':
                if '?' in text:
                    questions.append(text[:150])
                for keyword in ['python', 'react', 'database', 'api', 'docker', 'sql', 'error', 'bug']:
                    if keyword in text.lower():
                        topics.add(keyword)
        parts = []
        if topics:
            parts.append(f"Topics: {', '.join(sorted(topics)[:8])}")
        if questions:
            parts.append(f"Questions: {'; '.join(questions[-3:])}")
        if not parts:
            parts.append(f"General conversation ({len(older)} messages)")
        summary = ". ".join(parts)

        with self._lock:
            self._summaries[sender_id] = summary
            self._short_term[sender_id] = messages[-10:]

        if self.supabase:
            try:
                self.supabase.table('conversation_summaries').upsert({
                    'id': f"sum-{sender_id}", 'sender_id': sender_id,
                    'summary': summary,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                }, on_conflict='sender_id').execute()
            except Exception as e:
                logger.error("Failed to persist summary: %s", e)

    def get_summary(self, sender_id: str) -> Optional[str]:
        with self._lock:
            if sender_id in self._summaries:
                return self._summaries[sender_id]
        if self.supabase:
            try:
                result = (self.supabase.table('conversation_summaries')
                    .select('summary').eq('sender_id', sender_id).maybe_single().execute())
                if result and result.data:
                    summary = result.data['summary']
                    with self._lock:
                        self._summaries[sender_id] = summary
                    return summary
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------

    def get_conversation_context(self, sender_id: str) -> str:
        messages = self.get_recent_messages(sender_id, limit=8)
        if not messages:
            return ""
        lines = []
        for msg in messages:
            role_label = "Friend" if msg['role'] == 'user' else "You"
            text = msg.get('text', '')[:300]
            lines.append(f"{role_label}: {text}")
        return "\n".join(lines)

    def get_memory_context(self, sender_id: str) -> str:
        parts = []
        profile = self.get_user_profile(sender_id)
        name = profile.get('name')
        if name and name != 'there':
            parts.append(f"User's name: {name}")
        interests = profile.get('interests', [])
        if isinstance(interests, str):
            try:
                interests = json.loads(interests)
            except (json.JSONDecodeError, TypeError):
                interests = [interests] if interests else []
        if interests:
            parts.append(f"Their interests: {', '.join(interests[:5])}")
        summary = self.get_summary(sender_id)
        if summary:
            parts.append(f"Earlier: {summary}")
        context = self.get_conversation_context(sender_id)
        if context:
            parts.append(f"Recent:\n{context}")
        return "\n".join(parts) if parts else ""

    def increment_message_count(self, sender_id: str):
        profile = self.get_user_profile(sender_id)
        count = profile.get('total_messages', 0)
        self.update_user_profile(sender_id, {'total_messages': count + 1})


memory = MemorySystem()
