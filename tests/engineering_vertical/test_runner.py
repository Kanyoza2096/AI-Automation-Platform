import pytest
from services.kdo_engineering_runner import run_command, RunnerInputError

def test_runs_allowlisted_command_and_returns_evidence(tmp_path):
    result = run_command(tmp_path, ["python", "-c", "print('KDO_RUNNER_OK')"])
    assert result.status == "passed"
    assert "KDO_RUNNER_OK" in result.stdout
    assert result.exit_code == 0

def test_rejects_command_not_allowlisted(tmp_path):
    with pytest.raises(RunnerInputError, match="command_not_allowlisted"):
        run_command(tmp_path, ["sh", "-c", "echo unsafe"])

def test_rejects_shell_injection_as_single_argument_not_shell(tmp_path):
    result = run_command(tmp_path, ["python", "-c", "print('safe; not shell')"])
    assert result.status == "passed"
    assert "safe; not shell" in result.stdout

def test_timeout_is_reported_not_success(tmp_path):
    result = run_command(tmp_path, ["python", "-c", "import time; time.sleep(2)"], timeout_seconds=1)
    assert result.status == "timed_out"
    assert result.timed_out is True

def test_rejects_bad_timeout(tmp_path):
    with pytest.raises(RunnerInputError, match="timeout_out_of_range"):
        run_command(tmp_path, ["python", "-c", "print(1)"], timeout_seconds=999)
