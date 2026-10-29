from services.v71_31_worker_selection_policy import WorkerSelectionPolicyProfile, WorkerSelectionPolicyRegistry

def test_phase720_policy_is_versioned_and_durable():
    r=WorkerSelectionPolicyRegistry("ws-20")
    p=r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1))
    assert p.policy_fingerprint == p.fingerprint()
    assert r.get("ops",1).policy_fingerprint == p.policy_fingerprint
    r.assert_integrity(p)

def test_phase720_policy_is_immutable_by_version():
    r=WorkerSelectionPolicyRegistry("ws-20")
    r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1))
    try: r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1))
    except ValueError as e: assert str(e)=="duplicate"
    else: raise AssertionError("duplicate policy version accepted")

def test_phase720_weights_are_explicit_and_bounded():
    r=WorkerSelectionPolicyRegistry("ws-20")
    p=r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1, .25,.35,.20,.10,.10,2,.20,.40,.08,5,True,("ops",)))
    assert sum((p.capability_weight,p.capacity_weight,p.safety_weight,p.autonomy_weight,p.budget_weight)) == 1.0
    assert p.minimum_autonomy_level == 2
    assert p.allowed_capabilities == ("ops",)

def test_phase720_policy_has_no_authority():
    r=WorkerSelectionPolicyRegistry("ws-20")
    p=r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1))
    assert p.advisory_only and not p.authority_granted and not p.execution_requested

def test_phase720_tamper_detected():
    from dataclasses import replace
    r=WorkerSelectionPolicyRegistry("ws-20")
    p=r.publish(WorkerSelectionPolicyProfile("ops", "ws-20", 1))
    try: r.assert_integrity(replace(p, capability_weight=.31))
    except ValueError as e: assert str(e)=="policy_tampered"
    else: raise AssertionError("tampered policy accepted")

def test_phase720_policy_is_consumed_by_reputation_selection():
    from services.v71_29_reputation_aware_worker_selection import ReputationAwareWorkerSelectionRegistry
    class Base:
        workspace_id='ws-20'
        def select(self, **kwargs):
            from services.v71_9_worker_selection import WorkerSelection, WorkerCandidate
            assert kwargs['minimum_autonomy_level'] == 2
            assert kwargs['selection_policy']['capability'] == .25
            c=WorkerCandidate('worker-a',1,.8,1,1,.9,.91,())
            return WorkerSelection('s20','ws-20','t20','ops',{},'worker-a',(c,),True,False,False,'x')
    class Rep:
        workspace_id='ws-20'
        def profile(self, **kwargs):
            return type('P',(),{'sample_count':5,'verified_sample_count':5,'average_capability_score':1,'average_performance_score':1,'can_authorize':False,'can_execute':False,'can_call_provider':False})()
    r=WorkerSelectionPolicyRegistry('ws-20')
    r.publish(WorkerSelectionPolicyProfile('ops-policy','ws-20',1,.25,.35,.20,.10,.10,2,.2,.4,.08,5,True,('ops',)))
    s=ReputationAwareWorkerSelectionRegistry('ws-20',Base(),Rep(),policy_registry=r,policy_id='ops-policy')
    result=s.select(selection_id='s20',task_id='t20',capability='ops')
    assert result.selected_agent_id == 'worker-a'
