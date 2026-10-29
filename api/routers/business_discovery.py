from fastapi import APIRouter, Depends, HTTPException, Request
from api.dependencies import auth_user_email_var, require_auth, require_workspace
from domain.business_discovery.discovery import StartDiscoveryRequest, ContinueDiscoveryRequest
from repositories.business_discovery_repository import BusinessDiscoveryRepository
from services.business_activation_knowledge import BusinessActivationKnowledgeService
from repositories.business_activation_knowledge_repository import BusinessActivationKnowledgeRepository
from services.business_discovery import BusinessDiscoveryService

router = APIRouter()

def _service(request: Request) -> BusinessDiscoveryService:
    from services.supabase import supabase_service
    db = getattr(supabase_service, 'service_client', None) or getattr(supabase_service, 'client', None)
    return BusinessDiscoveryService(
        BusinessDiscoveryRepository(db),
        BusinessActivationKnowledgeService(BusinessActivationKnowledgeRepository(db)),
    )

@router.post('/business-activation/discovery/start')
async def start_discovery(body: StartDiscoveryRequest, request: Request, _auth: bool = Depends(require_auth), authenticated_workspace: str = Depends(require_workspace)):
    if body.workspace_id != authenticated_workspace:
        raise HTTPException(403, detail='workspace_id does not match authenticated workspace')
    return _service(request).start(body).model_dump(mode='json')

@router.post('/business-activation/discovery/continue')
async def continue_discovery(body: ContinueDiscoveryRequest, request: Request, _auth: bool = Depends(require_auth), authenticated_workspace: str = Depends(require_workspace)):
    if body.workspace_id != authenticated_workspace:
        raise HTTPException(403, detail='workspace_id does not match authenticated workspace')
    try:
        return _service(request).continue_session(body, auth_user_email_var.get() or 'authenticated-principal').model_dump(mode='json')
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc

@router.get('/business-activation/discovery/{session_id}')
async def get_discovery(session_id: str, request: Request, _auth: bool = Depends(require_auth), authenticated_workspace: str = Depends(require_workspace)):
    session = _service(request).repository.get(authenticated_workspace, session_id)
    if not session:
        raise HTTPException(404, detail='discovery_session_not_found')
    return session.model_dump(mode='json')
