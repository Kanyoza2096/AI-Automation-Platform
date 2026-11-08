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
            raise RuntimeError(
                f"Knowledge entry {record_id} could not be loaded"
            ) from e

    def save(self, entity: KnowledgeEntry) -> KnowledgeEntry:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode='json')
            res = self.supabase.table('knowledge_entries').upsert(data).execute()
            if res.data:
                return KnowledgeEntry(**res.data[0])
            # Do not claim durable success unless the database confirms the
            # upsert. A no-representation response is ambiguous for ingestion.
            raise RuntimeError(
                f"Knowledge entry {entity.id} was not confirmed by Supabase"
            )
        except Exception as e:
            # A production ingestion must not report success when Supabase
            # persistence failed. In-memory fallback would disappear across
            # requests/restarts and create a false-positive indexed record.
            logger.error("Failed to save knowledge entry %s: %s", entity.id, e)
            raise RuntimeError(
                f"Knowledge entry {entity.id} could not be persisted"
            ) from e

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
            raise RuntimeError(
                f"Knowledge entry {record_id} could not be deleted"
            ) from e

    def find_by_brand(self, brand_id: str, workspace_id: str) -> List[KnowledgeEntry]:
        """Load knowledge entries for one brand inside one workspace only."""
        if not brand_id or not str(brand_id).strip():
            raise ValueError("brand_id is required for knowledge retrieval")
        if not workspace_id or not str(workspace_id).strip():
            raise ValueError("workspace_id is required for scoped knowledge retrieval")

        if not self.supabase:
            return [
                entry for entry in self._in_memory.values()
                if entry.brand_id == brand_id and entry.workspace_id == workspace_id
            ]
        try:
            res = (
                self.supabase.table("knowledge_entries")
                .select("*")
                .eq("brand_id", brand_id)
                .eq("workspace_id", workspace_id)
                .execute()
            )
            return [KnowledgeEntry(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error(
                "Failed to find knowledge entries for brand %s in workspace %s: %s",
                brand_id, workspace_id, e,
            )
            raise RuntimeError(
                f"Knowledge entries for brand {brand_id} in workspace {workspace_id} could not be loaded"
            ) from e

    def find_by_workspace(self, workspace_id: str) -> List[KnowledgeEntry]:
        if not self.supabase:
            return [k for k in self._in_memory.values() if k.workspace_id == workspace_id]
        try:
            res = self.supabase.table('knowledge_entries').select('*').eq('workspace_id', workspace_id).execute()
            return [KnowledgeEntry(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find knowledge entries by workspace %s: %s", workspace_id, e)
            raise RuntimeError(
                f"Knowledge entries for workspace {workspace_id} could not be loaded"
            ) from e
