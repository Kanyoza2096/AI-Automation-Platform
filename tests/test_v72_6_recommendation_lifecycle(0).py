from services.v72_6_recommendation_lifecycle import RecommendationLifecycleService, Lifecycle


class Repo:
    def __init__(self): self.rows = {}; self.events = []
    def upsert_recommendation(self, r): self.rows[(r['workspaceId'], r['recommendationId'])] = dict(r); return r
    def append_event(self, e):
        if not any(x['eventId'] == e['eventId'] for x in self.events): self.events.append(e)
        return e
    def get_recommendation(self, w, r): return self.rows.get((w, r))
    def list_recommendations(self, w, limit=50): return list(v for (ws, _), v in self.rows.items() if ws == w)[:limit]


def surface():
    return {'workspaceId':'ws1','recommendations':[{'recommendationId':'r1','fingerprint':'fp1','title':'Test','outcomeLinks':[], 'actionAuthorityGranted':False}]}


def test_materialization_is_idempotent_and_authority_free():
    repo = Repo(); svc = RecommendationLifecycleService(repo)
    svc.materialize(surface()); svc.materialize(surface())
    assert len(repo.rows) == 1
    assert repo.rows[('ws1','r1')]['authorityGranted'] is False


def test_lifecycle_transition_is_fenced_and_audited():
    repo = Repo(); svc = RecommendationLifecycleService(repo); svc.materialize(surface())
    result = svc.transition('ws1','r1',Lifecycle.ACKNOWLEDGED,'user1','reviewed')
    assert result.lifecycle == Lifecycle.ACKNOWLEDGED
    assert len(repo.events) == 1
    assert repo.events[0]['authorityGranted'] is False


def test_invalid_transition_and_cross_tenant_are_rejected():
    repo = Repo(); svc = RecommendationLifecycleService(repo); svc.materialize(surface())
    try: svc.transition('other','r1',Lifecycle.DISMISSED,'user1')
    except KeyError: pass
    else: assert False
    svc.transition('ws1','r1',Lifecycle.DISMISSED,'user1')
    try: svc.transition('ws1','r1',Lifecycle.ACKNOWLEDGED,'user1')
    except ValueError: pass
    else: assert False


def test_outcome_attachment_is_deduped_and_linked():
    repo = Repo(); svc = RecommendationLifecycleService(repo); svc.materialize(surface())
    svc.attach_outcome('ws1','r1','out1','user1')
    svc.attach_outcome('ws1','r1','out1','user1')
    assert repo.rows[('ws1','r1')]['outcomeLinks'] == ['out1']
    assert all(e['authorityGranted'] is False for e in repo.events)


def test_ledger_read_is_explicitly_read_only():
    repo = Repo(); svc = RecommendationLifecycleService(repo); svc.materialize(surface())
    result = svc.list('ws1')
    assert result['authority']['actionAuthorityGranted'] is False
    assert result['recommendations'][0]['actionAuthorityGranted'] is False
