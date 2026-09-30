import pytest
from services.v70_3_customer_intelligence import CustomerIntelligence, CustomerIdentity, IntelligenceBlock

def make(): return CustomerIntelligence("ws1")

def test_identity_and_context_are_tenant_bound():
    c=make(); c.observe_identity(CustomerIdentity("ws1","c1",name="Jane",email="j@example.com"))
    x=c.observe(event_id="e1",customer_id="c1",signal_type="qualified",source="crm",lifecycle_state="qualified")
    assert x.identity.name=="Jane" and x.interaction_count==1 and x.positive_signals==1

def test_cross_tenant_fails_closed():
    with pytest.raises(ValueError,match="tenant_mismatch"):
        make().observe(event_id="e1",customer_id="c1",signal_type="reply",workspace_id="ws2")

def test_duplicate_event_fenced():
    c=make(); c.observe(event_id="e1",customer_id="c1",signal_type="reply")
    with pytest.raises(ValueError,match="duplicate_event"): c.observe(event_id="e1",customer_id="c1",signal_type="reply")

def test_high_intent_recommendation_is_advisory():
    c=make(); c.observe(event_id="e1",customer_id="c1",signal_type="purchase_intent")
    x=c.recommend_next_workflow("c1")
    assert x.recommended_workflow=="sales.high-intent-follow-up" and x.advisory_only and x.authority=="none"

def test_opt_out_blocks_later_signals():
    c=make(); c.observe(event_id="e1",customer_id="c1",signal_type="opt_out")
    with pytest.raises(ValueError,match="opted_out"): c.observe(event_id="e2",customer_id="c1",signal_type="reply")

def test_context_fingerprint_is_deterministic():
    c=make(); c.observe(event_id="e1",customer_id="c1",signal_type="reply",source="web")
    assert c.context("c1").fingerprint()==c.context("c1").fingerprint()

def test_no_execution_surface():
    c=make(); assert not hasattr(c,"engine"); assert not hasattr(c,"connector"); assert not hasattr(c,"provider"); assert not hasattr(c,"action_kernel")

def test_invalid_signal_rejected():
    with pytest.raises(ValueError,match="invalid_signal"): make().observe(event_id="e1",customer_id="c1",signal_type="reply",value="bad")
