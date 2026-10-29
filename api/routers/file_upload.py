from fastapi import APIRouter, Depends, Request, HTTPException, File, Form, UploadFile
from typing import Optional
import logging
import re as _re_mod
import uuid as _uuid_mod

from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

logger = logging.getLogger(__name__)

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# FILE UPLOAD — Spec §11/§13: Multipart upload architecture for PDFs
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/upload")
async def upload_file(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Spec §11/§13: Scalable multipart file upload endpoint.
    Accepts PDF files via multipart/form-data, stores to Supabase Storage,
    and returns a file_id for use with plugin/job execution.

    Prevents: memory spikes, huge JSON payloads, base64 overhead, timeouts.
    """
    try:
        import io as _io

        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            return _err("Content-Type must be multipart/form-data", 415)

        form = await request.form()
        file = form.get("file")
        client_workspace_id = form.get("workspace_id")
        workspace_id = resolve_plugin_workspace(client_workspace_id)
        purpose = form.get("purpose", "pdf_merge")  # pdf_merge | pdf_split | pdf_compress
        if purpose not in {"pdf_merge", "pdf_split", "pdf_compress"}:
            return _err("Unsupported upload purpose", 400)

        if not file or not hasattr(file, "read"):
            return _err("file field is required (multipart/form-data)", 400)

        filename = getattr(file, "filename", "upload.pdf") or "upload.pdf"
        # Storage keys must never contain client-controlled path components.
        filename = _re_mod.sub(r"[^A-Za-z0-9._-]", "_", filename.split("/")[-1].split("\\")[-1])[:255] or "upload.pdf"
        content = await file.read()

        # Validate file size — 50 MB max for PDF operations
        max_bytes = 50 * 1024 * 1024
        if len(content) > max_bytes:
            return _err(f"File exceeds maximum size of {max_bytes // (1024*1024)}MB", 413)

        # Validate basic PDF signature
        if not content.startswith(b"%PDF"):
            return _err("Only PDF files are accepted", 415)

        file_id = _uuid_mod.uuid4().hex
        stored_path: Optional[str] = None
        storage_url: Optional[str] = None

        try:
            from services.supabase import supabase_service
            if not (supabase_service and supabase_service.client):
                raise RuntimeError("durable PDF storage is unavailable")
            storage_path = f"uploads/{workspace_id}/{file_id}/{filename}"
            await _async_wrap(
                lambda: supabase_service.client.storage.from_("pdf-uploads").upload(
                    storage_path,
                    content,
                    {"content-type": "application/pdf"},
                )
            )
            storage_url = supabase_service.client.storage.from_("pdf-uploads").get_public_url(storage_path)
            stored_path = storage_path
        except Exception as se:
            logger.exception("Durable PDF storage failed for workspace=%s file_id=%s", workspace_id, file_id)
            return _err("File storage temporarily unavailable; upload was not accepted", 503)

        cid = correlation_id_var.get()
        return _ok({
            "file_id": file_id,
            "filename": filename,
            "size_bytes": len(content),
            "size_kb": round(len(content) / 1024, 1),
            "storage_url": storage_url,
            "stored_path": stored_path,
            "workspace_id": workspace_id,
            "purpose": purpose,
            "correlation_id": cid,
            "note": "Use file_id when submitting to plugin commands.",
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/upload")


@router.post("/plugins/pdf_merger/upload")
async def upload_pdfs_for_merger(
    files: list[UploadFile] = File(..., description="PDF files to upload"),
    command: str = Form(default="merge_pdfs"),
    workspace_id: str = Form(default="default"),
    brand_id: str = Form(default="default"),
    pages: str = Form(default=""),
    quality: int = Form(default=50),
    output_name: str = Form(default=""),
    _auth: bool = Depends(require_auth),
):
    """Upload PDFs via multipart form data for processing."""
    # ... full function body from my previous message


@router.post("/plugins/pdf_merger/execute")
async def pdf_merger_execute(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Spec §11.1: Dedicated PDF Merger execution endpoint expected by Mutex.
    Forwards to the canonical plugin API at POST /plugins/pdf_merger/execute.
    Accepts JSON with file_ids (from /upload) instead of raw base64.
    """
    try:
        body = await request.json()
        container = request.app.state.container
        plugins = getattr(container, "industry_plugins", {})
        plugin = plugins.get("pdf_merger")
        if not plugin:
            return _err("PDF merger plugin is not registered", 404)

        command = body.get("command", "merge_pdfs")
        params = body.get("params", {})
        client_workspace_id = body.get("workspace_id")
        workspace_id = resolve_plugin_workspace(client_workspace_id)
        params = params if isinstance(params, dict) else {}
        result = await _async_wrap(
            run_plugin,
            plugin.execute,
            command,
            params,
            client_workspace_id=client_workspace_id,
        )
        return _ok(result)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/plugins/pdf_merger/execute")


@router.get("/plugins")
async def list_plugins(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """List available industry plugins."""
    try:
        container = request.app.state.container
        plugins_registry = getattr(container, "industry_plugins", {})
        result = [
            {
                "name": n,
                "commands": p.get_commands(),
                "webhooks": p.get_webhooks(),
            }
            for n, p in plugins_registry.items()
        ]
        return _ok({"plugins": result, "count": len(result)})
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/plugins")


@router.post("/plugins/{plugin_name}/execute")
async def execute_plugin_command(
    plugin_name: str,
    body: PluginExecuteRequest,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Execute a plugin command."""
    try:
        container = request.app.state.container
        plugins = getattr(container, "industry_plugins", {})
        plugin = plugins.get(plugin_name)
        if not plugin:
            return _err(f"Plugin '{plugin_name}' not found", 404)

        result = await _async_wrap(
            run_plugin,
            plugin.execute,
            body.command,
            body.params,
            client_workspace_id=body.workspace_id,
        )
        return _ok(result)
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/plugins/{plugin_name}/execute")
