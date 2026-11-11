"""Durable, tenant-scoped inbound event sources for Business Automation."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

EVENT_NAME_RE = __import__('re').compile(r'^[a-z0-9_\.]{1,100}$')


def _db():
    try:
        from services.supabase import supabase_service
        return supabase_service.service_client or supabase_service.client
    except Exception:
        return None


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ('source_id','workspace_id','name','provider','event_types','active','created_at','updated_at','last_event_at','events_received')}


async def create_source(workspace_id: str, name: str, provider: str, event_types: list[str]) -> tuple[dict[str, Any], str]:
    ws = str(workspace_id or '').strip()
    name = str(name or '').strip()[:120]
    provider = str(provider or 'generic_webhook').strip()[:50]
    if not ws or not name:
        raise ValueError('workspace_id and name are required')
    if provider not in {'generic_webhook', 'website_lead'}:
        raise ValueError('unsupported provider; supported providers are generic_webhook and website_lead')
    if provider == 'website_lead':
        event_types = ['lead.created']
    normalized = []
    for event_name in event_types:
        value = str(event_name or '').strip()
        if not EVENT_NAME_RE.fullmatch(value):
            raise ValueError(f'invalid event type: {value}')
        if value not in normalized:
            normalized.append(value)
    if len(normalized) > 50:
        raise ValueError('event_types must contain at most 50 values')
    secret = 'KDO_WEBHOOK_' + secrets.token_urlsafe(32)
    row = {'source_id': 'src_' + uuid.uuid4().hex[:20], 'workspace_id': ws, 'name': name,
           'provider': provider, 'event_types': normalized, 'secret_hash': _hash(secret), 'active': True}
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event source management')
    try:
        result = await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_sources').insert(row).execute())
    except Exception as exc:
        raise RuntimeError(f'event source creation failed: {exc}') from exc
    if not result.data:
        raise RuntimeError('event source insert returned no data')
    return _public(result.data[0]), secret


async def list_sources(workspace_id: str) -> list[dict[str, Any]]:
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event source management')
    result = await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_sources').select('source_id,workspace_id,name,provider,event_types,active,created_at,updated_at,last_event_at,events_received').eq('workspace_id', workspace_id).order('created_at', desc=True).execute())
    return [_public(r) for r in (result.data or [])]


async def set_active(workspace_id: str, source_id: str, active: bool) -> dict[str, Any]:
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event source management')
    result = await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_sources').update({'active': bool(active), 'updated_at': datetime.now(timezone.utc).isoformat()}).eq('workspace_id', workspace_id).eq('source_id', source_id).execute())
    if not result.data:
        raise KeyError('event source not found')
    return _public(result.data[0])


async def authenticate(source_id: str, secret: str, event_id: str, event_name: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event ingestion')
    if not secret or not event_id or not EVENT_NAME_RE.fullmatch(event_name):
        raise ValueError('valid event_id, event_name and secret are required')
    result = await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_sources').select('*').eq('source_id', source_id).limit(1).execute())
    row = (result.data or [None])[0]
    if not row:
        raise KeyError('event source not found')
    if not bool(row.get('active')):
        raise PermissionError('event source is inactive')
    if not __import__('hmac').compare_digest(_hash(secret), str(row.get('secret_hash') or '')):
        raise PermissionError('invalid event source secret')
    allowed = row.get('event_types') or []
    if allowed and event_name not in allowed:
        raise PermissionError('event type is not enabled for this source')
    if not isinstance(payload, dict) or len(__import__('json').dumps(payload, default=str)) > 100_000:
        raise ValueError('payload must be an object no larger than 100KB')
    existing = await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_receipts').select('receipt_id,published_at').eq('source_id', source_id).eq('event_id', event_id).limit(1).execute())
    return _public(row), bool(existing.data and existing.data[0].get('published_at'))


async def reserve_receipt(source_id: str, workspace_id: str, event_id: str, event_name: str) -> bool:
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event ingestion')
    result = await __import__('asyncio').to_thread(lambda: db.rpc('claim_business_automation_event_receipt', {
        'p_source_id': source_id, 'p_workspace_id': workspace_id, 'p_event_id': event_id, 'p_event_name': event_name
    }).execute())
    value = result.data
    if isinstance(value, list):
        value = value[0] if value else False
    return bool(value)


async def mark_receipt(source_id: str, event_id: str, *, published: bool, error: str | None = None) -> None:
    db = _db()
    if db is None:
        raise RuntimeError('durable database is required for event ingestion')
    now = datetime.now(timezone.utc).isoformat()
    await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_receipts').update({'published_at': now if published else None, 'publish_error': None if published else (error or '')[:1000]}).eq('source_id', source_id).eq('event_id', event_id).execute())
    if published:
        await __import__('asyncio').to_thread(lambda: db.table('business_automation_event_sources').update({'last_event_at': now, 'updated_at': now}).eq('source_id', source_id).execute())


async def ingest_event(*, source_id: str, secret: str, event_id: str, event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticate, durably fence, and publish one external business event.

    This is deliberately shared by generic webhooks and typed first-party
    adapters so every ingress path has identical replay and tenant semantics.
    It never executes an automation directly.
    """
    source, duplicate = await authenticate(source_id, secret, event_id, event_name, payload)
    if duplicate:
        return {"ok": True, "duplicate": True, "event_id": event_id, "event_name": event_name}
    reserved = await reserve_receipt(source_id, source["workspace_id"], event_id, event_name)
    if not reserved:
        return {"ok": True, "duplicate": True, "event_id": event_id, "event_name": event_name}
    try:
        from core.unified_bus import get_unified_bus
        bus = await get_unified_bus()
        if not bus:
            raise RuntimeError("event bus unavailable")
        published_id = await bus.publish(
            event_name,
            {**payload, "event_id": event_id, "event_source_id": source_id},
            source=f"external:{source_id}",
            workspace_id=source["workspace_id"],
            strict_nerve=True,
        )
        if not published_id:
            raise RuntimeError("event bus rejected the event")
        await mark_receipt(source_id, event_id, published=True)
    except Exception as exc:
        try:
            await mark_receipt(source_id, event_id, published=False, error=str(exc))
        except Exception:
            pass
        raise
    return {"ok": True, "duplicate": False, "event_id": event_id, "event_name": event_name}
