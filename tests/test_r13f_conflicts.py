from services.knowledge_conflict_resolution import analyze_knowledge_conflicts


def test_same_value_is_not_a_conflict():
    result = analyze_knowledge_conflicts([
        {"id": "a", "knowledge_key": "pricing", "value": "$10", "status": "confirmed"},
        {"id": "b", "knowledge_key": "pricing", "value": " $10 ", "status": "confirmed"},
    ])
    assert result["conflict_count"] == 0
    assert result["consistent_groups"][0]["fact_ids"] == ["a", "b"]


def test_confirmed_competing_values_require_resolution():
    result = analyze_knowledge_conflicts([
        {"id": "a", "knowledge_key": "pricing", "value": "$10", "status": "confirmed"},
        {"id": "b", "knowledge_key": "pricing", "value": "$15", "status": "confirmed"},
    ])
    conflict = result["conflicts"][0]
    assert conflict["conflict_type"] == "confirmed_conflict"
    assert conflict["recommendation"]["mode"] == "request_resolution"
    assert conflict["recommendation"]["auto_resolve"] is False


def test_explicit_authority_guides_but_does_not_auto_resolve():
    result = analyze_knowledge_conflicts([
        {"id": "a", "knowledge_key": "refund policy", "value": "30 days", "status": "confirmed", "authority": "authoritative"},
        {"id": "b", "knowledge_key": "refund policy", "value": "14 days", "status": "confirmed"},
    ])
    recommendation = result["conflicts"][0]["recommendation"]
    assert recommendation["mode"] == "confirm_authoritative_precedence"
    assert recommendation["preferred_fact_id"] == "a"
    assert recommendation["requires_explicit_confirmation"] is True
    assert recommendation["auto_resolve"] is False


def test_candidate_does_not_override_confirmed_fact():
    result = analyze_knowledge_conflicts([
        {"id": "a", "knowledge_key": "delivery areas", "value": "Blantyre", "status": "confirmed"},
        {"id": "b", "knowledge_key": "delivery areas", "value": "Lilongwe", "status": "candidate"},
    ])
    conflict = result["conflicts"][0]
    assert conflict["conflict_type"] == "candidate_against_confirmed"
    assert conflict["recommendation"]["auto_resolve"] is False


def test_superseded_fact_is_excluded_by_default():
    result = analyze_knowledge_conflicts([
        {"id": "old", "knowledge_key": "hours", "value": "08:00", "status": "superseded"},
        {"id": "new", "knowledge_key": "hours", "value": "09:00", "status": "confirmed"},
    ])
    assert result["conflict_count"] == 0
    assert result["ignored"][0]["id"] == "old"


def test_missing_fields_are_ignored_safely():
    result = analyze_knowledge_conflicts([{"id": "bad", "status": "confirmed"}])
    assert result["conflict_count"] == 0
    assert result["ignored"][0]["reason"] == "missing_knowledge_key_or_value"


def test_analysis_is_read_only():
    fact = {"id": "a", "knowledge_key": "pricing", "value": "$10", "status": "confirmed"}
    result = analyze_knowledge_conflicts([fact])
    assert result["read_only"] is True
    assert result["safety"]["does_not_create_knowledge"] is True
    assert result["safety"]["does_not_confirm_knowledge"] is True
    assert result["safety"]["does_not_supersede_facts"] is True
