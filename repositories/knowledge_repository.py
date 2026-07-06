from typing import List, Optional
from repositories.base import Repository
from models.knowledge_entry import KnowledgeEntry

class KnowledgeRepository(Repository[KnowledgeEntry]):
    """Supabase + In-Memory fallback repository for KnowledgeEntries."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[KnowledgeEntry]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        
        def _fetch():
            res = self.supabase.table('knowledge_entries').select('*').eq('id', record_id).execute()
            if res.data:
                return KnowledgeEntry(**res.data[0])
            return None
            
        return self._safe_call(_fetch)

    def save(self, entity: KnowledgeEntry) -> KnowledgeEntry:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
            
        def _upsert():
            data = entity.model_dump(mode='json')
            res = self.supabase.table('knowledge_entries').upsert(data).execute()
            if res.data:
                return KnowledgeEntry(**res.data[0])
            return entity
            
        return self._safe_call(_upsert, default=entity)

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
            
        def _delete():
            self.supabase.table('knowledge_entries').delete().eq('id', record_id).execute()
            return True
            
        return self._safe_call(_delete, default=False)

    def find_by_brand(self, brand_id: str) -> List[KnowledgeEntry]:
        if not self.supabase:
            return [k for k in self._in_memory.values() if k.brand_id == brand_id]
            
        def _fetch():
            res = self.supabase.table('knowledge_entries').select('*').eq('brand_id', brand_id).execute()
            return [KnowledgeEntry(**row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])

    def find_by_workspace(self, workspace_id: str) -> List[KnowledgeEntry]:
        if not self.supabase:
            return [k for k in self._in_memory.values() if k.workspace_id == workspace_id]
            
        def _fetch():
            res = self.supabase.table('knowledge_entries').select('*').eq('workspace_id', workspace_id).execute()
            return [KnowledgeEntry(**row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])
