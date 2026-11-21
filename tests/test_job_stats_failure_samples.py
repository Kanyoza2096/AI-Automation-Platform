from types import SimpleNamespace

from services.job_stats_service import JobStatsService


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.limit_value = None

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows[: self.limit_value])


class FakeSupabase:
    def __init__(self, rows):
        self.query = FakeQuery(rows)

    def table(self, _name):
        return self.query


def _job(index):
    return {
        "job_id": f"job-{index}",
        "last_error_code": "AI_PROVIDER_UNAVAILABLE",
        "result": {
            "failed_steps": [{
                "step": "generate",
                "error": "provider timeout",
            }],
            "raw_payload_that_must_not_escape": "private payload",
        },
        "attempt_count": 2,
        "max_attempts": 3,
        "updated_at": "2026-11-21T00:00:00+00:00",
    }


def test_failure_intelligence_returns_bounded_structured_samples_without_raw_payloads():
    service = JobStatsService(supabase_client=FakeSupabase([_job(i) for i in range(25)]))

    result = service.get_failure_intelligence(limit=500)

    assert result["status"] == "available"
    category = result["categories"]["provider_availability"]
    assert category["count"] == 25
    assert len(category["job_ids"]) == 20
    assert len(category["failure_samples"]) == 20
    assert category["failure_samples"][0] == {
        "job_id": "job-0",
        "last_error_code": "AI_PROVIDER_UNAVAILABLE",
        "failed_steps": ["generate"],
        "attempt_count": 2,
        "max_attempts": 3,
    }
    assert "raw_payload_that_must_not_escape" not in str(result)
    assert "private payload" not in str(result)
