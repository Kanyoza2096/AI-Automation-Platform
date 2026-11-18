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
        integration_id='site-demo', app_name='Demo Site', base_url='https://demo.example', brand_id='brand-1',
        capabilities=[WebsiteCapability.CREATE_DRAFT, WebsiteCapability.PUBLISH_ARTICLE],
        event_types=[WebsiteEventType.ARTICLE_PUBLISHED],
    )


def test_registration_is_durable_and_uses_website_connector_account(monkeypatch):
    db = FakeDB()
    saved = {}
    class Repo:
        def __init__(self, client): pass
        def save(self, account): saved['account'] = account; return account
    class Brands:
        def __init__(self, client): pass
        def find_by_id(self, brand_id): return SimpleNamespace(id=brand_id, workspace_id='ws-1')
    monkeypatch.setattr('services.integrations.website_integration_service.SocialAccountRepository', Repo)
    monkeypatch.setattr('services.integrations.website_integration_service.BrandRepository', Brands)
    item = WebsiteIntegrationService(db).register('ws-1', registration(), 'site-secret')
    assert item.connector_authority == 'WebsiteConnector'
    assert saved['account'].platform.value == 'website'
    assert saved['account'].access_token == 'site-secret'
    assert saved['account'].brand_id == 'brand-1'
    assert saved['account'].platform_config['capabilities'] == ['CREATE_DRAFT', 'PUBLISH_ARTICLE']
    assert saved['account'].platform_config['status'] == 'publish'


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


def test_publish_capability_requires_brand_binding(monkeypatch):
    db = FakeDB()
    class Repo:
        def __init__(self, client): pass
        def save(self, account): raise AssertionError("must not save an unbound publisher")
    monkeypatch.setattr('services.integrations.website_integration_service.SocialAccountRepository', Repo)
    reg = registration().model_copy(update={'brand_id': None})
    with pytest.raises(ValueError, match='brand_id is required'):
        WebsiteIntegrationService(db).register('ws-1', reg, 'site-secret')


def test_registration_rejects_brand_from_another_workspace(monkeypatch):
    db = FakeDB()
    class Brands:
        def __init__(self, client): pass
        def find_by_id(self, brand_id): return SimpleNamespace(id=brand_id, workspace_id='ws-other')
    monkeypatch.setattr('services.integrations.website_integration_service.BrandRepository', Brands)
    with pytest.raises(ValueError, match='authenticated workspace'):
        WebsiteIntegrationService(db).register('ws-1', registration(), 'site-secret')


def test_website_connector_blocks_publish_without_capability(monkeypatch):
    from models.social_account import Platform, SocialAccount
    from services.connectors.website_connector import WebsiteConnector
    account = SocialAccount(
        id='website-draft-only', workspace_id='ws-1', brand_id='brand-1',
        platform=Platform.WEBSITE, access_token='secret',
        platform_config={'base_url':'https://demo.example', 'publish_path':'/api/kdo/content',
                         'capabilities':['CREATE_DRAFT'], 'status':'draft'},
    )
    connector = WebsiteConnector(account)
    monkeypatch.setattr('services.connectors.website_connector.requests.post',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network must not be called')))
    result = connector.publish('Article body', title='Article', status='publish')
    assert not result.ok
    assert result.outcome == 'failed'
    assert 'PUBLISH_ARTICLE' in result.error


def test_website_connector_defaults_to_draft_when_publish_not_authorized():
    from models.social_account import Platform, SocialAccount
    from services.connectors.website_connector import WebsiteConnector
    account = SocialAccount(
        id='website-draft-only', workspace_id='ws-1', brand_id='brand-1',
        platform=Platform.WEBSITE, access_token='secret',
        platform_config={'base_url':'https://demo.example', 'publish_path':'/api/kdo/content',
                         'capabilities':['CREATE_DRAFT']},
    )
    assert WebsiteConnector(account)._status == 'draft'



def test_website_provider_contract_accepts_article_payload():
    from domain.publishing.provider_contract import validate_request
    assert validate_request('website', text='A useful article body') is None
    assert validate_request('website', text='x' * 50001)['error_code'] == 'content_too_long'


def test_website_connector_publishes_only_with_explicit_capability(monkeypatch):
    from models.social_account import Platform, SocialAccount
    from services.connectors.website_connector import WebsiteConnector
    captured = {}
    class Response:
        status_code = 201
        content = b'{"id":"article-123"}'
        def json(self): return {'id':'article-123'}
    def post(url, **kwargs):
        captured['url'] = url
        captured['payload'] = kwargs['json']
        return Response()
    monkeypatch.setattr('services.connectors.website_connector.requests.post', post)
    account = SocialAccount(
        id='website-publisher', workspace_id='ws-1', brand_id='brand-1',
        platform=Platform.WEBSITE, access_token='secret',
        platform_config={'base_url':'https://demo.example', 'publish_path':'/api/kdo/content',
                         'capabilities':['CREATE_DRAFT', 'PUBLISH_ARTICLE'], 'status':'publish'},
    )
    result = WebsiteConnector(account).publish('Article body', title='Article title', topic='Article title',
                                               idempotency_key='article-key-123')
    assert result.ok and result.post_id == 'article-123'
    assert captured['payload']['title'] == 'Article title'
    assert captured['payload']['status'] == 'publish'



def test_brand_scheduler_uses_configured_website_platform():
    from types import SimpleNamespace
    from scheduler.brand_scheduler import _brand_publish_platform
    brand = SimpleNamespace(metadata={'primary_platform':'website'})
    assert _brand_publish_platform(brand) == 'website'


def test_brand_scheduler_preserves_legacy_facebook_default():
    from types import SimpleNamespace
    from scheduler.brand_scheduler import _brand_publish_platform
    assert _brand_publish_platform(SimpleNamespace(metadata={})) == 'facebook'


def test_website_connector_blocks_delete_without_capability(monkeypatch):
    from models.social_account import Platform, SocialAccount
    from services.connectors.website_connector import WebsiteConnector
    account = SocialAccount(
        id='website-no-delete', workspace_id='ws-1', brand_id='brand-1',
        platform=Platform.WEBSITE, access_token='secret',
        platform_config={'base_url':'https://demo.example', 'publish_path':'/api/kdo/content',
                         'delete_path_template':'/api/kdo/content/{post_id}',
                         'capabilities':['PUBLISH_ARTICLE']},
    )
    monkeypatch.setattr('services.connectors.website_connector.requests.delete',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network must not be called')))
    result = WebsiteConnector(account).delete('article-1')
    assert not result.ok
    assert 'DELETE_ARTICLE' in result.error


def test_website_connector_blocks_update_without_capability(monkeypatch):
    from models.social_account import Platform, SocialAccount
    from services.connectors.website_connector import WebsiteConnector
    account = SocialAccount(
        id='website-no-update', workspace_id='ws-1', brand_id='brand-1',
        platform=Platform.WEBSITE, access_token='secret',
        platform_config={'base_url':'https://demo.example', 'publish_path':'/api/kdo/content',
                         'edit_path_template':'/api/kdo/content/{post_id}',
                         'capabilities':['PUBLISH_ARTICLE']},
    )
    monkeypatch.setattr('services.connectors.website_connector.requests.patch',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network must not be called')))
    result = WebsiteConnector(account).edit('article-1', 'updated content')
    assert not result.ok
    assert 'UPDATE_ARTICLE' in result.error
