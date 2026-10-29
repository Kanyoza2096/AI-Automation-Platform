"""V67.8 multi-process chaos pre-staging certification tests."""

from tests.chaos.v67_multiprocess_chaos_harness import run_race


def test_only_one_worker_wins_atomic_claim():
    result = run_race(workers=4, crash_point="none")
    assert result.winner_count == 1
    assert result.provider_calls == 1
    assert result.final_status == "succeeded"
    assert result.duplicate_prevented


def test_provider_success_followed_by_worker_crash_becomes_uncertain():
    result = run_race(workers=4, crash_point="after_provider_before_receipt")
    assert result.winner_count == 1
    assert result.provider_calls == 1
    assert result.uncertain
    assert result.final_status == "reconciliation_required"
    assert result.duplicate_prevented


def test_worker_crash_before_provider_does_not_create_external_side_effect():
    result = run_race(workers=4, crash_point="before_provider")
    assert result.winner_count == 1
    assert result.provider_calls == 0
    assert result.final_status == "processing"


def test_process_identity_is_the_single_claim_winner():
    result = run_race(workers=4, crash_point="none")
    assert len(set(result.worker_pids)) == 1  # exactly one process obtained the claim
