import pytest

from api.routers.ai_chat import _is_engineering_awareness_query


@pytest.mark.parametrize(
    "message",
    [
        "What engineering work did we complete today?",
        "Which pull requests were merged?",
        "Was the latest change deployed?",
        "Show me the repository changes",
        "What did we implement today?",
    ],
)
def test_director_engineering_questions_request_live_change_evidence(message):
    assert _is_engineering_awareness_query(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Explain how strategic planning works.",
        "Help me write a devotional message.",
        "What is the difference between autonomy and authority?",
    ],
)
def test_unrelated_director_questions_do_not_trigger_engineering_provider_reads(message):
    assert _is_engineering_awareness_query(message) is False
