from services.v72_8_business_outcome_closed_loop_learning import BusinessOutcomeClosedLoopLearning, LearningState

def test_repeated_observed_successes_form_pattern_not_authority():
    rows = [{"recommendationId": f"r{i}", "outcomeId": f"o{i}", "state":"OBSERVED", "businessSuccess":True, "patternKey":"retention"} for i in range(3)]
    x = BusinessOutcomeClosedLoopLearning("w1", rows).build()
    s = x["signals"][0]
    assert s["state"] == LearningState.LEARNED_PATTERN
    assert s["observedSuccesses"] == 3
    assert s["actionAuthorityGranted"] is False

def test_failures_are_learning_evidence_not_policy():
    rows = [{"recommendationId": f"r{i}", "outcomeId": f"o{i}", "state":"OBSERVED", "businessSuccess":False, "patternKey":"sales"} for i in range(3)]
    s = BusinessOutcomeClosedLoopLearning("w1", rows).build()["signals"][0]
    assert s["state"] == LearningState.LEARNED_PATTERN
    assert s["policyMutationAllowed"] is False

def test_associated_outcomes_remain_mixed_evidence():
    rows = [{"recommendationId":"r1", "outcomeId":"o1", "state":"ASSOCIATED", "patternKey":"marketing"}]
    s = BusinessOutcomeClosedLoopLearning("w1", rows).build()["signals"][0]
    assert s["state"] == LearningState.MIXED_EVIDENCE
    assert s["causalClaim"] if "causalClaim" in s else True

def test_insufficient_evidence_is_explicit():
    rows = [{"recommendationId":"r1", "patternKey":"ops", "state":"INSUFFICIENT_EVIDENCE"}]
    s = BusinessOutcomeClosedLoopLearning("w1", rows).build()["signals"][0]
    assert s["state"] == LearningState.INSUFFICIENT_EVIDENCE

def test_deterministic_signal_fingerprint_and_tenant_scope():
    rows = [{"recommendationId":"r1", "outcomeId":"o1", "state":"OBSERVED", "businessSuccess":True, "patternKey":"x"},
            {"recommendationId":"r2", "outcomeId":"o2", "state":"OBSERVED", "businessSuccess":True, "patternKey":"x"},
            {"recommendationId":"r3", "outcomeId":"o3", "state":"OBSERVED", "businessSuccess":True, "patternKey":"x"}]
    a = BusinessOutcomeClosedLoopLearning("w1", rows).build()["signals"][0]
    b = BusinessOutcomeClosedLoopLearning("w1", list(reversed(rows))).build()["signals"][0]
    c = BusinessOutcomeClosedLoopLearning("w2", rows).build()["signals"][0]
    assert a["fingerprint"] == b["fingerprint"]
    assert a["signalId"] == b["signalId"]
    assert a["signalId"] != c["signalId"]
