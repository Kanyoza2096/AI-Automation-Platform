from fastapi import APIRouter, Depends
from api.v1 import require_auth
from services.meta_engine import meta_engine

# §3/§13: All meta-engine routes require authentication
router = APIRouter(prefix="/api/v1/meta", tags=["meta"], dependencies=[Depends(require_auth)])

@router.get("/status")
async def get_status():
    return meta_engine.get_status()

@router.get("/engines")
async def get_engines():
    return {"engines": {name: meta_engine.check_engine_health(name) for name in meta_engine._engines}}

@router.get("/engines/{engine_name}")
async def get_engine(engine_name: str):
    return meta_engine.get_engine_report(engine_name)

@router.get("/diagnoses")
async def get_diagnoses():
    return {"diagnoses": [d for d in meta_engine._diagnoses]}

@router.get("/dependency-graph")
async def get_dependency_graph():
    return meta_engine.get_dependency_graph()

@router.post("/diagnose")
async def run_diagnosis():
    diagnoses = meta_engine.diagnose()
    return {"diagnoses": diagnoses}

@router.post("/heal")
async def run_heal(_auth: bool = Depends(require_auth)):
    """Trigger recovery actions. Requires authenticated access."""
    actions = meta_engine.auto_heal()
    return {"actions": actions}

@router.get("/snapshots")
async def get_snapshots():
    return {"snapshots": list(meta_engine._snapshots)}

@router.get("/healing-history")
async def get_healing_history():
    return {"history": [r for r in meta_engine._recovery_history]}
