from services.kdo_engineering_release_gate import evaluate_staging_configuration


def staging_env(**overrides):
    env = {
        "ENVIRONMENT": "staging",
        "GUARDIAN_ENABLED": "false",
        "SUPABASE_URL": "https://staging-project.supabase.co",
        "SUPABASE_ANON_KEY": "anon-test-value",
        "SUPABASE_SERVICE_ROLE_KEY": "service-test-value",
        "GEMINI_KEY": "provider-test-value",
    }
    env.update(overrides)
    return env


def test_release_gate_accepts_complete_staging_config_without_claiming_release_ready():
    report = evaluate_staging_configuration(staging_env())
    assert report["ready"] is True
    assert report["secrets_redacted"] is True
    assert "does_not_certify_release_readiness" in report["limitations"]
    serialized = str(report)
    assert "service-test-value" not in serialized
    assert "provider-test-value" not in serialized


def test_release_gate_fails_closed_for_missing_required_credentials():
    env = staging_env()
    env.pop("SUPABASE_SERVICE_ROLE_KEY")
    report = evaluate_staging_configuration(env)
    assert report["ready"] is False
    assert "supabase_service_role_key_required" in report["blockers"]


def test_release_gate_rejects_non_https_database_url():
    report = evaluate_staging_configuration(staging_env(SUPABASE_URL="http://staging-project.supabase.co"))
    assert report["ready"] is False
    assert "supabase_url_must_be_https_without_embedded_credentials" in report["blockers"]


def test_release_gate_requires_explicit_staging_and_guardian_off():
    report = evaluate_staging_configuration(staging_env(ENVIRONMENT="production", GUARDIAN_ENABLED="true"))
    assert report["ready"] is False
    assert "environment_must_be_explicitly_staging" in report["blockers"]
    assert "autonomous_guardian_must_be_disabled_for_initial_staging" in report["blockers"]
