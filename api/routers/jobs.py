from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# JOBS — Architecture §29 Observability
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get status of an automation job by job_id."""
    try:
        from services.job_service import JobService
        job_service = await _async_wrap(container.try_resolve, JobService)
        
        if job_service is None:
            return _err("Job service not available", 503)
        
        job = await _async_wrap(job_service.find_by_id, job_id)
        
        if job is None:
            return _err(f"Job '{job_id}' not found", 404)
        
        # Workspace isolation
        workspace_id = auth_workspace_id_var.get()
        if workspace_id and job.workspace_id and job.workspace_id != workspace_id:
            return _err(f"Job '{job_id}' not found", 404)  # Mask existence
        
        return _ok({"job": job.to_status_dict()})
    except DatabaseQueryError:
        return _err("Job service unavailable", 503)
    except KanyozaError as exc:
        return _safe_err(exc, f"GET /api/v1/jobs/{job_id}")
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/jobs/{job_id}")

@router.get("/brands/{brand_id}/jobs")
async def get_brand_jobs(
    brand_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    status: str = "",
    limit: int = 20,
):
    """Get automation jobs for a brand.
    
    Optional query params:
      - status: Filter by job status (e.g. 'PUBLISHED', 'FAILED')
      - limit: Max results (default 20, max 100)
    """
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
        
        from services.job_service import JobService
        from models.automation_job import JobStatus
        
        job_service = await _async_wrap(container.try_resolve, JobService)
        
        if job_service is None:
            return _err("Job service not available", 503)
        
        limit = min(100, max(1, limit))
        
        job_status = None
        if status:
            try:
                job_status = JobStatus(status.upper())
            except ValueError:
                return _err(f"Invalid status: {status}. Valid: {[s.value for s in JobStatus]}", 400)
        
        jobs = await _async_wrap(
            job_service.find_by_brand,
            brand_id=brand_id,
            status=job_status,
            limit=limit,
        )
        
        stats = await _async_wrap(job_service.get_today_stats, brand_id=brand_id)
        
        return _ok({
            "jobs": [j.to_status_dict() for j in jobs],
            "count": len(jobs),
            "today_stats": stats,
        })
    except HTTPException:
        raise
    except DatabaseQueryError:
        return _err("Job service unavailable", 503)
    except KanyozaError as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/jobs")
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/jobs")
