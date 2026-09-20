from typing import List, Optional
import logging
from repositories.base import Repository
from models.ai_profile import AIProfile

logger = logging.getLogger("kanyoza.repositories.ai_profile")

class AIProfileRepository(Repository[AIProfile]):
    """Supabase + In-Memory fallback repository for AI Profiles."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[AIProfile]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table('ai_profiles').select('*').eq('id', record_id).execute()
            if res.data:
                return AIProfile(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find AI profile %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: AIProfile) -> AIProfile:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode='json')
            res = self.supabase.table('ai_profiles').upsert(data).execute()
            if res.data:
                return AIProfile(**res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save AI profile %s: %s", entity.id, e)
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table('ai_profiles').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete AI profile %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_workspace(self, workspace_id: str) -> List[AIProfile]:
        if not self.supabase:
            return [p for p in self._in_memory.values() if p.workspace_id == workspace_id]
        try:
            res = self.supabase.table('ai_profiles').select('*').eq('workspace_id', workspace_id).execute()
            return [AIProfile(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find AI profiles by workspace %s: %s", workspace_id, e)
            return [p for p in self._in_memory.values() if p.workspace_id == workspace_id]
