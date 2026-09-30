from pathlib import Path


def test_main_installs_v71_17_before_application_runs():
    text = Path("main.py").read_text()
    assert "install_workforce_execution_coordinator" in text
    assert "V71.17 durable workforce execution coordinator installed." in text


def test_autonomous_loop_no_longer_directly_calls_publish_pipeline():
    text = Path("core/autonomous_loop.py").read_text()
    start = text.index("    async def _handle_publish_command")
    end = text.index("    async def _handle_task_created", start)
    handler = text[start:end]
    assert "run_publish(" not in handler
    assert "durable_workforce_run_required" in handler
