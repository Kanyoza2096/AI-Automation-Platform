from typing import List, Optional
from repositories.base import Repository
from models.workspace import Workspace

class WorkspaceRepository(Repository[Workspace]):
    """Supabase + In-Memory fallback repository for Workspaces."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory = {}

    def find_by_id(self, record_id: str) -> Optional[Workspace]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        
        def _fetch():
            res = self.supabase.table('workspaces').select('*').eq('id', record_id).execute()
            if res.data:
                return Workspace(**res.data[0])
            return None
            
        return self._safe_call(_fetch)

    def save(self, entity: Workspace) -> Workspace:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
            
        def _upsert():
            data = entity.model_dump(mode='json')
            res = self.supabase.table('workspaces').upsert(data).execute()
            if res.data:
                return Workspace(**res.data[0])
            return entity
            
        return self._safe_call(_upsert, default=entity)

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
            
        def _delete():
            self.supabase.table('workspaces').delete().eq('id', record_id).execute()
            return True
            
        return self._safe_call(_delete, default=False)

    def find_all(self) -> List[Workspace]:
        if not self.supabase:
            return list(self._in_memory.values())
            
        def _fetch():
            res = self.supabase.table('workspaces').select('*').execute()
            return [Workspace(**row) for row in res.data]
            
        return self._safe_call(_fetch, default=[])
