import asyncio
from types import SimpleNamespace

import pytest

from contracts.website_integration import WebsiteCapability, WebsiteEventEnvelope, WebsiteEventType, WebsiteIntegrationRegistration
from services.integrations.website_integration_service import WebsiteIntegrationService


class FakeTable:
    def __init__(self, db, name): self.db, self.name = db, name; self.filters = {}
    def select(self, *_): self.mode = 'select'; return self
    def eq(self, key, value): self.filters[key] = value; return self
    def order(self, *_args, **_kwargs): return self
    def maybe_single(self): return self
    def upsert(self, row, **_kwargs): self.row = row; self.mode = 'upsert'; return self
    def insert(self, row): self.row = row; self.mode = 'insert'; return self
    def update(self, row): self.update_row = row; self.mode = 'update'; return self
    def execute(self):
        rows = self.db.rows.setdefault(self.name, [])
        if self.mode == 'upsert':
            rows[:] = [r for r in rows if not (r.get('workspace_id') == self.row.get('workspace_id') and r.get('integration_id') == self.row.get('integration_id'))]
            rows.append(self.row)
            return SimpleNamespace(data=[self.row])
        if self.mode == 'insert':
            if any(r.get('event_id') == self.row.get('event_id') and r.get('workspace_id') == self.row.get('workspace_id') for r in rows):
                raise RuntimeError('duplicate key')
            rows.append(self.row); return SimpleNamespace(data=[self.row])
        if self.mode == 'update':
            for r in rows:
                if all(str(r.get(k)) == str(v) for k, v in self.filters.items()): r.update(self.update_row)
            return SimpleNamespace(data=rows)
        selected = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in self.filters.items())]
        return SimpleNamespace(data=selected[0] if selected else None if hasattr(self, 'maybe_single') else selected)

class FakeDB:
    def __init__(self): self.rows = {}
    def table(self, name): return FakeTable(self, name)


def registration():
    return WebsiteIntegrationRegistration(
        integration_id='site-demo', app_name='Demo Site', base_url='https://demo.example',
        capabilities=[WebsiteCapability.CREATE_DRAFT, WebsiteCapability.PUBLISH_ARTICLE],
        event_types=[WebsiteEventType.ARTICLE_PUBLISHED],
    )


def test_registration_is_durable_and_uses_website_connector_account(monkeypatch):
    db = FakeDB()
    saved = {}
    class Repo:
        def __init__(self, client): pass
        def save(self, account): saved['account'] = account; return account
    monkeypatch.setattr('services.integrations.website_integration_service.SocialAccountRepository', Repo)
    item = WebsiteIntegrationService(db).register('ws-1', registration(), 'site-secret')
    assert item.connector_authority == 'WebsiteConnector'
    assert saved['account'].platform.value == 'website'
    assert saved['account'].access_token == 'site-secret'


def test_event_is_idempotent_per_workspace_and_integration():
    db = FakeDB(); svc = WebsiteIntegrationService(db)
    # seed integration without invoking credential repository
    db.rows['website_integrations'] = [{
        'workspace_id':'ws-1','integration_id':'site-demo','app_name':'Demo','environment':'production',
        'status':'configured','capabilities':[],'event_types':['article.published']
    }]
    event = WebsiteEventEnvelope(integration_id='site-demo', event_id='evt-12345678', event_type=WebsiteEventType.ARTICLE_PUBLISHED, payload={'id':'p1'})
    first = svc.record_event('ws-1', event); second = svc.record_event('ws-1', event)
    assert first['accepted'] and not first['duplicate']
    assert second['accepted'] and second['duplicate']
    assert len(db.rows['website_integration_events']) == 1


def test_event_type_must_be_enabled_for_integration():
    db = FakeDB()
    db.rows['website_integrations'] = [{
        'workspace_id': 'ws-1', 'integration_id': 'site-demo', 'app_name': 'Demo',
        'environment': 'production', 'status': 'configured', 'capabilities': [],
        'event_types': ['article.published'],
    }]
    svc = WebsiteIntegrationService(db)
    assert svc.event_type_is_registered('ws-1', 'site-demo', 'article.published')
    assert not svc.event_type_is_registered('ws-1', 'site-demo', 'project.created')


def test_growthbridge_domain_events_are_part_of_website_contract():
    from contracts.website_integration import WebsiteEventType
    expected = {
        'project.created', 'project.updated', 'project.deleted',
        'service.created', 'service.updated', 'blog.created', 'blog.published',
        'application.created', 'application.updated', 'contact.submitted',
        'partnership.requested', 'talent.profile.created', 'testimonial.created',
        'settings.updated',
    }
    assert expected.issubset({item.value for item in WebsiteEventType})
