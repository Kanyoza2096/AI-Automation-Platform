from api.routers.workflows import _filter_workflow_history_for_workspace


def test_workflow_history_excludes_other_and_unscoped_records():
    entries = [
        {"id": "own", "workspace_id": "workspace-a"},
        {"id": "other", "workspace_id": "workspace-b"},
        {"id": "legacy", "workspace_id": None},
    ]

    assert _filter_workflow_history_for_workspace(entries, "workspace-a") == [
        {"id": "own", "workspace_id": "workspace-a"}
    ]


def test_workflow_history_fails_closed_without_workspace():
    entries = [{"id": "own", "workspace_id": "workspace-a"}]

    assert _filter_workflow_history_for_workspace(entries, "") == []
    assert _filter_workflow_history_for_workspace(entries, None) == []
