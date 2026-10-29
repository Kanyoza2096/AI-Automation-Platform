from __future__ import annotations
from typing import Optional
from domain.business_discovery.discovery import DiscoverySession

class BusinessDiscoveryRepository:
    """Persistence adapter for R13 discovery state; never stores Knowledge facts."""
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._sessions: dict[str, DiscoverySession] = {}

    def save(self, session: DiscoverySession) -> DiscoverySession:
        self._sessions[session.session_id] = session.model_copy(deep=True)
        if self.supabase:
            self.supabase.table('business_discovery_sessions').upsert({
                'session_id': session.session_id,
                'workspace_id': session.workspace_id,
                'status': session.status.value,
                'state': session.model_dump(mode='json'),
                'updated_at': session.updated_at.isoformat(),
            }).execute()
        return session

    def get(self, workspace_id: str, session_id: str) -> Optional[DiscoverySession]:
        if not self.supabase:
            session = self._sessions.get(session_id)
            if session and session.workspace_id == workspace_id:
                return session.model_copy(deep=True)
            return None
        result = self.supabase.table('business_discovery_sessions').select('state').eq('session_id', session_id).eq('workspace_id', workspace_id).limit(1).execute()
        rows = result.data or []
        return DiscoverySession(**rows[0]['state']) if rows else None
