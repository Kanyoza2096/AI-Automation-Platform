from dataclasses import replace

from models.business_operating_ontology import OntologyNode, OntologyNodeType
from models.business_process_capability_graph import GraphEdgeType, GraphNodeType
from services.business_operating_ontology import BusinessOperatingOntologyProjector
from services.business_process_capability_graph import BusinessProcessCapabilityGraphProjector
from services.v69_business_process_orchestration import BusinessProcessDefinition, ProcessState, ProcessTransition
from organism.capabilities import CapabilitySpec, CapabilityRisk, CapabilityStatus, AuthorityRequirement


def _ontology(workspace="w1"):
    # The projector only needs the existing ontology contract; use the real projector input shape.
    from domain.business_activation.facts import Fact, FactType
    facts = [Fact(
        fact_id="f1", workspace_id=workspace, fact_type=FactType.PRODUCT_SERVICE,
        subject="business", predicate="offers", value="Consulting", confidence=1.0, source="operator_message", source_span={}, asserted_by="test", extraction_id="x1", confirmed=True,
    )]
    return BusinessOperatingOntologyProjector().project(workspace_id=workspace, facts=facts)


def _process(workspace="w1"):
    return BusinessProcessDefinition(
        "sales.lead", workspace, "Lead lifecycle", ProcessState.RUNNABLE,
        transitions=(ProcessTransition(
            "t1", ProcessState.RUNNABLE, ProcessState.WAITING_EVENT, "event", "lead.created",
            metadata={"capabilities": ["lead_management"], "action": "crm_add_contact"},
        ),),
    )


def _cap(workspace="w1"):
    return CapabilitySpec(workspace, "lead_management", "1.0", "Manage leads",
                          risk=CapabilityRisk.MEDIUM, authority=AuthorityRequirement.EXECUTE,
                          status=CapabilityStatus.ENABLED, prerequisites=("crm_connection",))


def test_projects_existing_sources_without_execution():
    graph = BusinessProcessCapabilityGraphProjector().project(
        workspace_id="w1", ontology=_ontology(), process_definitions=[_process()], capability_specs=[_cap()],
        sector_platform={"packs": [{"pack_id": "crm", "display_name": "CRM", "version": "1.1", "capabilities": ["contacts"], "workflows": ["lead_followup"], "external_actions": [{"action": "crm.send_message", "execution_binding": "ACTION_KERNEL", "contract": {}}]}]},
    )
    assert graph.schema.endswith("/v1")
    assert any(n.node_type is GraphNodeType.PROCESS for n in graph.nodes)
    assert any(n.node_type is GraphNodeType.CAPABILITY and n.name == "lead_management" for n in graph.nodes)
    assert any(n.node_type is GraphNodeType.ACTION and n.name == "crm_add_contact" for n in graph.nodes)
    assert any(e.edge_type is GraphEdgeType.ENABLES for e in graph.edges)
    assert any(e.edge_type is GraphEdgeType.REALIZED_BY_ACTION for e in graph.edges)


def test_does_not_infer_capability_from_action_or_pack_name():
    graph = BusinessProcessCapabilityGraphProjector().project(
        workspace_id="w1",
        sector_platform={"packs": [{"pack_id": "crm", "display_name": "CRM", "version": "1", "capabilities": ["contacts"], "external_actions": [{"action": "crm.send_message", "execution_binding": "ACTION_KERNEL", "contract": {}}]}]},
    )
    capability_edges = [e for e in graph.edges if e.edge_type is GraphEdgeType.ENABLES]
    assert capability_edges == []


def test_workspace_isolation():
    graph = BusinessProcessCapabilityGraphProjector().project(
        workspace_id="w1", process_definitions=[_process("w2")], capability_specs=[_cap("w2")]
    )
    assert graph.coverage["processes"] == 0
    assert graph.coverage["capabilities"] == 0
    assert "process_workspace_mismatch:sales.lead" in graph.warnings
    assert "capability_workspace_mismatch:lead_management" in graph.warnings


def test_projection_is_deterministic():
    projector = BusinessProcessCapabilityGraphProjector()
    a = projector.project(workspace_id="w1", ontology=_ontology(), process_definitions=[_process()], capability_specs=[_cap()]).to_dict()
    b = projector.project(workspace_id="w1", ontology=_ontology(), process_definitions=[_process()], capability_specs=[_cap()]).to_dict()
    assert a == b


def test_empty_graph_is_explicit():
    graph = BusinessProcessCapabilityGraphProjector().project(workspace_id="w1")
    assert graph.coverage["nodes"] == 0
    assert "empty_graph" in graph.warnings
