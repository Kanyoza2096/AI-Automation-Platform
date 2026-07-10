from typing import List, Optional
import logging
from repositories.base import Repository
from models.brand import Brand

logger = logging.getLogger("kanyoza.repositories.brand")

class BrandRepository(Repository[Brand]):
    """Supabase + In-Memory fallback repository for Brands."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[Brand]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table('brands').select('*').eq('id', record_id).execute()
            if res.data:
                return Brand(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find brand %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: Brand) -> Brand:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode='json')
            res = self.supabase.table('brands').upsert(data).execute()
            if res.data:
                return Brand(**res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save brand %s: %s", entity.id, e)
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table('brands').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete brand %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_workspace(self, workspace_id: str) -> List[Brand]:
        if not self.supabase:
            return [b for b in self._in_memory.values() if b.workspace_id == workspace_id]
        try:
            res = self.supabase.table('brands').select('*').eq('workspace_id', workspace_id).execute()
            return [Brand(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find brands by workspace %s: %s", workspace_id, e)
            return [b for b in self._in_memory.values() if b.workspace_id == workspace_id]
