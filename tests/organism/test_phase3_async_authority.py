import asyncio

from workflows.base import Step, StepStatus, Workflow


class AsyncStep(Step):
    name = "async_step"

    async def async_run(self, context):
        await asyncio.sleep(0)
        context["value"] = 42
        return {"value": 42}

    def run(self, context):
        raise AssertionError("async_run must be used by execute_async")


class SyncStep(Step):
    name = "sync_step"

    def run(self, context):
        return context["value"] + 1


def test_workflow_async_executes_async_steps_without_nested_loop():
    async def scenario():
        result = await Workflow([AsyncStep(), SyncStep()]).execute_async({})
        assert result.success
        assert [s.status for s in result.steps] == [StepStatus.SUCCESS, StepStatus.SUCCESS]
        assert result.steps[-1].output == 43

    asyncio.run(scenario())
