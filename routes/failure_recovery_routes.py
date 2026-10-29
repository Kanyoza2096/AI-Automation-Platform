from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, Dict, Any

router = APIRouter(prefix="/api/v1/failure-recovery", tags=["failure-recovery"])

def _auth_dep():
    from api.v1 import require_auth
    return require_auth

# In-memory store for demonstration purposes
_active_runs: Dict[str, Dict[str, Any]] = {}

@router.get("/contract", dependencies=[Depends(_auth_dep())])
async def get_contract():
    return {
        "schema": "KDO.FailureRecoveryContract/v1",
        "available": True,
        "environment": "development",
        "injectionEnabled": True,
        "productionInjectionAllowed": False,
        "scenarios": [
            {
                "id": "scenario-db-latency",
                "name": "Database Latency Spike",
                "failureMode": "latency",
                "targetDomain": "database",
                "purpose": "Test circuit breakers",
                "safetyClass": "safe",
                "requiredAuthority": "operator",
                "available": True
            },
            {
                "id": "scenario-worker-crash",
                "name": "Worker Node Crash",
                "failureMode": "crash",
                "targetDomain": "compute",
                "purpose": "Test auto-recovery",
                "safetyClass": "moderate",
                "requiredAuthority": "operator",
                "available": True
            }
        ],
        "activeRuns": list(_active_runs.values()),
        "warnings": [],
        "authority": "backend"
    }

@router.get("/runs", dependencies=[Depends(_auth_dep())])
async def get_runs(limit: int = 50):
    return {"runs": list(_active_runs.values())[-limit:]}

@router.post("/scenarios/{scenario_id}/execute", dependencies=[Depends(_auth_dep())])
async def execute_scenario(scenario_id: str, request: Request):
    body = await request.json()
    run_id = f"run-{scenario_id}-{int(request.state.start_time if hasattr(request.state, 'start_time') else 0)}"
    run = {
        "runId": run_id,
        "scenarioId": scenario_id,
        "status": "RUNNING",
        "phase": "injecting",
        "evidence": ["Scenario accepted"]
    }
    _active_runs[run_id] = run
    return {"run": run}

@router.post("/runs/{run_id}/recover", dependencies=[Depends(_auth_dep())])
async def recover_run(run_id: str, request: Request):
    run = _active_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["status"] = "RECOVERING"
    run["phase"] = "operator_intervention"
    run["evidence"].append("Operator requested recovery")
    return {"run": run}

@router.post("/runs/{run_id}/verify", dependencies=[Depends(_auth_dep())])
async def verify_run(run_id: str):
    run = _active_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run["status"] = "VERIFIED"
    run["phase"] = "completed"
    run["evidence"].append("Recovery verified by backend")
    return {"run": run}
