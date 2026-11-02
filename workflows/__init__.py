from workflows.base import Step, StepResult, StepStatus, Workflow, WorkflowResult
from workflows.post_workflow import PostPublishWorkflow

# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOW REGISTRY
#
# Single source of truth for the workflows exposed via /api/v1/workflow.
# Keys are the class-level `name` attribute (what run_workflow validates
# against); values carry the class (for future programmatic dispatch) and
# human-readable metadata (for list_workflows).
#
# Adding a new workflow class requires:
#   1. import it above
#   2. add an entry here keyed by its `.name`
#   3. add it to __all__ below
# ═══════════════════════════════════════════════════════════════════════════

AVAILABLE_WORKFLOWS = {
    PostPublishWorkflow.name: {
        "class": PostPublishWorkflow,
        "description": "Generate and publish content for all brands",
    },
}

__all__ = [
    "Step",
    "StepResult",
    "StepStatus",
    "Workflow",
    "WorkflowResult",
    "PostPublishWorkflow",
    "AVAILABLE_WORKFLOWS",
]
