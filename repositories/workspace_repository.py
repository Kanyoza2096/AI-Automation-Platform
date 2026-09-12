from typing import List, Optional
from repositories.base import Repository
from models.workspace import Workspace
import logging

logger = logging.getLogger("kanyoza.repositories.workspace")

class WorkspaceRepository(Repository[Workspace]):
    """Supabase + In-Memory fallback repository for Workspaces."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[Workspace]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        
        try:
            res = self.supabase.table('workspaces').select('*').eq('id', record_id).execute()
            if res.data:
                return Workspace(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find workspace by id %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: Workspace) -> Workspace:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            logger.info("Workspace saved to in-memory: %s", entity.id)
            return entity
        
        try:
            data = entity.model_dump(mode='json')
            logger.info("Saving workspace to Supabase: %s", data.get('id'))
            res = self.supabase.table('workspaces').upsert(data).execute()
            if res.data:
                logger.info("Workspace saved to Supabase: %s", res.data[0].get('id'))
                return Workspace(**res.data[0])
            logger.warning("Supabase upsert returned no data for workspace %s", entity.id)
            return entity
        except Exception as e:
            logger.error("Failed to save workspace to Supabase: %s", e)
            # Fall back to in-memory
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        
        try:
            self.supabase.table('workspaces').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete workspace %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_all(self) -> List[Workspace]:
        if not self.supabase:
            return list(self._in_memory.values())
        
        try:
            res = self.supabase.table('workspaces').select('*').execute()
            return [Workspace(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to fetch all workspaces: %s", e)
            return list(self._in_memory.values())

    def find_by_workspace(self, workspace_id: str) -> List[Workspace]:
        """Compatibility method — workspaces don't have a parent workspace."""
        ws = self.find_by_id(workspace_id)
        return [ws] if ws else []
