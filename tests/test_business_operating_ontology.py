from datetime import datetime, timezone

from domain.business_activation.facts import Fact, FactType
from services.business_operating_ontology import BusinessOperatingOntologyProjector


def fact(workspace, fact_type, value, subject="business", predicate="has", fid="f1", confirmed=True, superseded_at=None):
    return Fact(
        fact_id=fid,
        workspace_id=workspace,
        fact_type=fact_type,
        subject=subject,
        predicate=predicate,
        value=value,
        confidence=0.95,
        source="operator_message",
        source_span={"text": str(value)},
        asserted_by="owner",
        extraction_id="ext-1",
        confirmed=confirmed,
        confirmed_at=datetime.now(timezone.utc) if confirmed else None,
        superseded_at=superseded_at,
    )


def test_projects_confirmed_facts_into_business_ontology():
    ontology = BusinessOperatingOntologyProjector().project("w1", [
        fact("w1", FactType.BUSINESS_IDENTITY, "Acme Logistics", fid="id1"),
        fact("w1", FactType.PRODUCT_SERVICE, "Courier delivery", fid="svc1"),
        fact("w1", FactType.LOCATION, "Blantyre", fid="loc1"),
    ])
    assert ontology.schema_version == "KDO.BusinessOperatingOntology/v1"
    assert ontology.root_node_id.startswith("bo-business-")
    assert len(ontology.nodes) == 4
    assert len(ontology.edges) == 3
    assert sorted(ontology.source_fact_ids) == ["id1", "loc1", "svc1"]


def test_unconfirmed_and_superseded_facts_never_enter_projection():
    ontology = BusinessOperatingOntologyProjector().project("w1", [
        fact("w1", FactType.PRODUCT_SERVICE, "Confirmed service", fid="good"),
        fact("w1", FactType.PRODUCT_SERVICE, "Candidate service", fid="candidate", confirmed=False),
        fact("w1", FactType.PRODUCT_SERVICE, "Old service", fid="old", superseded_at=datetime.now(timezone.utc)),
    ])
    assert ontology.source_fact_ids == ["good"]
    assert all("Candidate service" not in n.label for n in ontology.nodes)
    assert all("Old service" not in n.label for n in ontology.nodes)


def test_workspace_isolation():
    ontology = BusinessOperatingOntologyProjector().project("w1", [
        fact("w1", FactType.PRODUCT_SERVICE, "Local", fid="local"),
        fact("w2", FactType.PRODUCT_SERVICE, "Foreign", fid="foreign"),
    ])
    assert ontology.source_fact_ids == ["local"]
    assert all(n.workspace_id == "w1" for n in ontology.nodes)


def test_projection_is_deterministic():
    facts = [
        fact("w1", FactType.PRODUCT_SERVICE, "Courier", fid="a"),
        fact("w1", FactType.PAYMENT_METHOD, "Mobile money", fid="b"),
    ]
    a = BusinessOperatingOntologyProjector().project("w1", facts)
    b = BusinessOperatingOntologyProjector().project("w1", list(reversed(facts)))
    assert [(n.node_id, n.label) for n in a.nodes] == [(n.node_id, n.label) for n in b.nodes]
    assert [(e.edge_id, e.edge_type) for e in a.edges] == [(e.edge_id, e.edge_type) for e in b.edges]


def test_empty_knowledge_is_explicitly_incomplete():
    ontology = BusinessOperatingOntologyProjector().project("w1", [])
    assert ontology.nodes[0].node_type.value == "business"
    assert "ontology_has_no_confirmed_business_facts" in ontology.warnings
