from fastapi import APIRouter, Depends
from core.security.deps import require_species_privileges
from services.code_intelligence_engine import code_intel_engine

router = APIRouter(prefix="/api/v1/code-intel", tags=["code-intel"], dependencies=[Depends(require_species_privileges)])

@router.get("/status")
def get_status():
    return code_intel_engine.get_status() if code_intel_engine else {"error": "not running"}

@router.get("/issues")
def get_issues(severity: str = None, limit: int = 100):
    return {"issues": code_intel_engine.get_issues(severity, limit) if code_intel_engine else []}

@router.post("/scan")
def trigger_scan():
    if not code_intel_engine:
        return {"error": "not running"}
    return code_intel_engine.scan_now()
