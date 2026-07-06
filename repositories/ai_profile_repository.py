from typing import List, Optional
from repositories.base import Repository
from models.ai_profile import AIProfile

class AIProfileRepository(Repository[AIProfile]):
    """Supabase + In-Memory fallback repository for AI Profiles."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[AIProfile]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        
        def _fetch():
            res = self.supabase.table('ai_profiles').select('*').eq('id', record_id).execute()
            if res.data:
                return AIProfile(**res.data[0])
            return None
            
        return self._safe_call(_fetch)

    def save(self, entity: AIProfile) -> AIProfile:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
            
        def _upsert():
            data = entity.model_dump(mode='json')
            res = self.supabase.table('ai_profiles').upsert(data).execute()
            if res.data:
                return AIProfile(**res.data[0])
            return entity
            
        return self._safe_call(_upsert, default=entity)

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
            
        def _delete():
            self.supabase.table('ai_profiles').delete().eq('id', record_id).execute()
            return True
            
        return self._safe_call(_delete, default=False)

    def find_by_workspace(self, workspace_id: str) -> List[AIProfile]:
        if not self.supabase:
            return [p for p in self._in_memory.values() if p.workspace_id == workspace_id]
            
        def _fetch():
            res = self.supabase.table('ai_profiles').select('*').eq('workspace_id', workspace_id).execute()
            return [AIProfile(**row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])
