import pytest
from services.v69_integration_intelligence import *


def metric(c, s=100, ok=.99, amb=0, lat=100, cost=1, out=.8, w='w1'):
    return IntegrationMetric(w,c,'publish',s,ok,amb,lat,cost,out)


def test_ranks_by_reliability_outcome_latency_and_cost():
    i=IntegrationIntelligence('w1')
    r=i.recommend('publish',[metric('slow',100,.99,0,1000,10,.9),metric('fast',100,.97,0,100,2,.95)])
    assert r.recommendations[0].connector_key == 'fast'
    assert r.recommendations[0].advisory_only and not r.recommendations[0].authority_granted


def test_tenant_isolation_fails_closed_when_no_local_evidence():
    with pytest.raises(ValueError, match='tenant_mismatch'):
        IntegrationIntelligence('w1').recommend('publish',[metric('x',w='w2')])


def test_foreign_metrics_are_not_used_with_local_candidates():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('local'),metric('foreign',ok=1,w='w2')])
    assert [x.connector_key for x in r.recommendations] == ['local']


def test_insufficient_evidence_is_advisory_not_guessing():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('x',s=2)],minimum_samples=5)
    assert not r.recommendations and 'insufficient_evidence' in r.anomalies


def test_ambiguous_execution_reduces_reliability_and_surfaces_anomaly():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('x',ok=.99,amb=.2),metric('y',ok=.96,amb=0)])
    assert r.recommendations[0].connector_key == 'y'
    assert 'ambiguity_anomaly:x' in r.anomalies


def test_latency_and_cost_reasons():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('cheap',lat=500,cost=.1),metric('quick',lat=10,cost=10)])
    names={x.connector_key:x.reasons for x in r.recommendations}
    assert 'lowest_latency' in names['quick'] and 'lowest_cost' in names['cheap']


def test_confidence_is_bounded_by_sample_size():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('x',s=1000)])
    assert r.recommendations[0].confidence == 1.0


def test_top_k_is_deterministic():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('b'),metric('a'),metric('c')],top_k=2)
    assert len(r.recommendations)==2


def test_invalid_metric_rejected():
    with pytest.raises(ValueError): IntegrationMetric('w1','x','publish',1,1.2)


def test_optimizer_cannot_grant_authority():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('x')])
    assert all(not x.authority_granted and x.execution_effect=='none' for x in r.recommendations)


def test_process_option_returns_advisory_recommendation():
    x=IntegrationIntelligence('w1').compare_process_options([metric('x')],action='publish')
    assert x and x.advisory_only


def test_reliability_drift_detected():
    r=IntegrationIntelligence('w1').recommend('publish',[metric('good',ok=.99),metric('bad',ok=.60)])
    assert 'reliability_drift:bad' in r.anomalies
