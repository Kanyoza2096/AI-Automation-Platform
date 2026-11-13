from lifecycle import health_registry
from services.prompt_builder import prompt_builder


def test_prompt_builder_exposes_canonical_health_provider_identity():
    assert prompt_builder.service_name == "prompt_builder"
    assert callable(prompt_builder.health_check)


def test_prompt_builder_health_check_reports_operational_contract():
    report = prompt_builder.health_check()

    assert report["service"] == "prompt_builder"
    assert report["status"] in {"healthy", "degraded", "unknown"}
