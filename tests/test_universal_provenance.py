from services.universal_provenance import UniversalProvenanceProjector


def _records():
    return [
        {"id": "fact-1", "kind": "business_fact", "workspace_id": "w1", "evidence_refs": ["ev-1"]},
        {"id": "obs-1", "kind": "observation", "workspace_id": "w1", "parent_ids": ["fact-1"], "evidence_refs": ["ev-2"], "correlation_id": "corr-1"},
        {"id": "drift-1", "kind": "drift", "workspace_id": "w1", "parent_ids": ["obs-1"]},
        {"id": "decision-1", "kind": "decision", "workspace_id": "w1", "parent_ids": ["drift-1"], "evidence_parent_ids": ["fact-1"]},
        {"id": "action-1", "kind": "action_intent", "workspace_id": "w1", "parent_ids": ["decision-1"]},
        {"id": "exec-1", "kind": "execution", "workspace_id": "w1", "parent_ids": ["action-1"]},
        {"id": "receipt-1", "kind": "receipt", "workspace_id": "w1", "parent_ids": ["exec-1"]},
        {"id": "verify-1", "kind": "verification", "workspace_id": "w1", "parent_ids": ["receipt-1"]},
        {"id": "outcome-1", "kind": "outcome", "workspace_id": "w1", "parent_ids": ["verify-1"]},
        {"id": "learning-1", "kind": "learning", "workspace_id": "w1", "parent_ids": ["outcome-1"]},
    ]


def test_projects_explicit_end_to_end_lineage():
    graph = UniversalProvenanceProjector().project(workspace_id="w1", records=_records())
    assert len(graph.nodes) == 10
    assert len(graph.edges) == 10
    assert not graph.warnings
    assert all(node.authority == "none" for node in graph.nodes)
    assert graph.to_dict()["authority"]["read_only"] is True


def test_never_infers_relationships_from_type_or_order():
    rows = [
        {"id": "decision", "kind": "decision", "workspace_id": "w1"},
        {"id": "outcome", "kind": "outcome", "workspace_id": "w1"},
    ]
    graph = UniversalProvenanceProjector().project(workspace_id="w1", records=rows)
    assert graph.edges == ()


def test_workspace_isolation():
    graph = UniversalProvenanceProjector().project(
        workspace_id="w1",
        records=_records() + [{"id": "foreign", "kind": "decision", "workspace_id": "w2"}],
    )
    assert all(node.workspace_id == "w1" for node in graph.nodes)
    assert all(edge.workspace_id == "w1" for edge in graph.edges)
    assert all(node.source_id != "foreign" for node in graph.nodes)


def test_unresolved_explicit_parent_is_warning_not_inferred_edge():
    graph = UniversalProvenanceProjector().project(
        workspace_id="w1",
        records=[{"id": "decision", "kind": "decision", "workspace_id": "w1", "parent_ids": ["missing"]}],
    )
    assert len(graph.nodes) == 1
    assert graph.edges == ()
    assert any("unresolved explicit parent missing" in warning for warning in graph.warnings)


def test_projection_is_deterministic_and_fingerprinted():
    projector = UniversalProvenanceProjector()
    a = projector.project(workspace_id="w1", records=_records())
    b = projector.project(workspace_id="w1", records=list(reversed(_records())))
    assert a.fingerprint == b.fingerprint
    assert [n.node_id for n in a.nodes] == [n.node_id for n in b.nodes]
    assert [e.edge_id for e in a.edges] == [e.edge_id for e in b.edges]
