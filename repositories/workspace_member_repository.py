"""Repository for WorkspaceMember records — user membership and roles."""

import logging
from typing import List, Optional

from repositories.base import Repository
from models.workspace import WorkspaceMember, MemberRole

logger = logging.getLogger("kanyoza.repositories.workspace_member")


class WorkspaceMemberRepository(Repository[WorkspaceMember]):
    """Supabase + In-Memory fallback for workspace memberships."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._in_memory: dict[str, WorkspaceMember] = {}

    def find_by_id(self, record_id: str) -> Optional[WorkspaceMember]:
        if not self.supabase:
            return self._in_memory.get(record_id)
        try:
            res = self.supabase.table('workspace_members').select('*').eq('id', record_id).execute()
            if res.data:
                return WorkspaceMember(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find member by id %s: %s", record_id, e)
            return self._in_memory.get(record_id)

    def save(self, entity: WorkspaceMember) -> WorkspaceMember:
        if not self.supabase:
            self._in_memory[entity.id] = entity
            return entity
        try:
            data = entity.model_dump(mode='json')
            res = self.supabase.table('workspace_members').upsert(data).execute()
            if res.data:
                return WorkspaceMember(**res.data[0])
            return entity
        except Exception as e:
            logger.error("Failed to save member %s: %s", entity.id, e)
            self._in_memory[entity.id] = entity
            return entity

    def delete(self, record_id: str) -> bool:
        if not self.supabase:
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False
        try:
            self.supabase.table('workspace_members').delete().eq('id', record_id).execute()
            return True
        except Exception as e:
            logger.error("Failed to delete member %s: %s", record_id, e)
            if record_id in self._in_memory:
                del self._in_memory[record_id]
                return True
            return False

    def find_by_workspace(self, workspace_id: str) -> List[WorkspaceMember]:
        if not self.supabase:
            return [m for m in self._in_memory.values() if m.workspace_id == workspace_id]
        try:
            res = self.supabase.table('workspace_members').select('*').eq('workspace_id', workspace_id).execute()
            return [WorkspaceMember(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find members for workspace %s: %s", workspace_id, e)
            return [m for m in self._in_memory.values() if m.workspace_id == workspace_id]

    def find_by_user_and_workspace(self, user_id: str, workspace_id: str) -> Optional[WorkspaceMember]:
        """Find a specific user's membership in a workspace."""
        if not self.supabase:
            for m in self._in_memory.values():
                if m.user_id == user_id and m.workspace_id == workspace_id:
                    return m
            return None
        try:
            res = self.supabase.table('workspace_members') \
                .select('*') \
                .eq('user_id', user_id) \
                .eq('workspace_id', workspace_id) \
                .limit(1) \
                .execute()
            if res.data:
                return WorkspaceMember(**res.data[0])
            return None
        except Exception as e:
            logger.error("Failed to find member user=%s ws=%s: %s", user_id, workspace_id, e)
            for m in self._in_memory.values():
                if m.user_id == user_id and m.workspace_id == workspace_id:
                    return m
            return None

    def find_by_email(self, email: str) -> List[WorkspaceMember]:
        """Find all memberships for a given user email."""
        if not self.supabase:
            return [m for m in self._in_memory.values() if m.user_id == email]
        try:
            res = self.supabase.table('workspace_members').select('*').eq('user_id', email).execute()
            return [WorkspaceMember(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to find members by email %s: %s", email, e)
            return [m for m in self._in_memory.values() if m.user_id == email]

    def find_all(self) -> List[WorkspaceMember]:
        if not self.supabase:
            return list(self._in_memory.values())
        try:
            res = self.supabase.table('workspace_members').select('*').execute()
            return [WorkspaceMember(**row) for row in (res.data or [])]
        except Exception as e:
            logger.error("Failed to fetch all members: %s", e)
            return list(self._in_memory.values())
