from typing import List, Optional
import logging
from repositories.base import Repository
from models.knowledge_entry import KnowledgeEntry

logger = logging.getLogger("kanyoza.repositories.knowledge")

class KnowledgeRepository(Repository[KnowledgeEntry]):
    """Supabase + In-Memory fallback repository for KnowledgeEntries."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[KnowledgeEntry]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table('knowledge_entries').select('*').eq('id', record_id).execute()
            if res.data:
                return KnowledgeEntry(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find knowledge entry %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: KnowledgeEntry) -> KnowledgeEntry:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode='json')
            res = self.supabase.table('knowledge_entries').upsert(data).execute()
            if res.data:
                return KnowledgeEntry(**res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save knowledge entry %s: %s", entity.id, e)
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table('knowledge_entries').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete knowledge entry %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_brand(self, brand_id: str) -> List[KnowledgeEntry]:
        if not self.supabase:
            return [k for k in self._in_memory.values() if k.brand_id == brand_id]
        try:
            res = self.supabase.table('knowledge_entries').select('*').eq('brand_id', brand_id).execute()
            return [KnowledgeEntry(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find knowledge entries by brand %s: %s", brand_id, e)
            return [k for k in self._in_memory.values() if k.brand_id == brand_id]

    def find_by_workspace(self, workspace_id: str) -> List[KnowledgeEntry]:
        if not self.supabase:
            return [k for k in self._in_memory.values() if k.workspace_id == workspace_id]
        try:
            res = self.supabase.table('knowledge_entries').select('*').eq('workspace_id', workspace_id).execute()
            return [KnowledgeEntry(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find knowledge entries by workspace %s: %s", workspace_id, e)
            return [k for k in self._in_memory.values() if k.workspace_id == workspace_id]
