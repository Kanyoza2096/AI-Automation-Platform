import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = os.getenv("KDO_ENGINEERING_TEST_POSTGRES_DSN")


@pytest.mark.skipif(not DSN, reason="disposable PostgreSQL migration test runs in CI")
def test_connector_evidence_rows_must_reference_connection_in_same_workspace():
    suffix = uuid.uuid4().hex
    workspace_a = f"it-workspace-a-{suffix}"
    workspace_b = f"it-workspace-b-{suffix}"
    connection_id = f"it-connection-{suffix}"

    with psycopg.connect(DSN) as connection:
        connection.execute(
            "INSERT INTO public.workspaces (id) VALUES (%s), (%s)",
            (workspace_a, workspace_b),
        )
        connection.execute(
            """
            INSERT INTO public.integration_connections
                (id, workspace_id, connector_key)
            VALUES (%s, %s, 'facebook')
            """,
            (connection_id, workspace_a),
        )

        # The correctly bound workspace/connection pair is accepted by all
        # three evidence surfaces.
        connection.execute(
            """
            INSERT INTO public.integration_evidence
                (evidence_id, workspace_id, connection_id, connector_key,
                 event_type, occurred_at, outcome, evidence_hash)
            VALUES (%s, %s, %s, 'facebook', 'health.checked', NOW(),
                    'healthy', %s)
            """,
            (f"evidence-{suffix}", workspace_a, connection_id, "a" * 64),
        )
        connection.execute(
            """
            INSERT INTO public.connector_health_observations
                (workspace_id, connection_id, connector_key, observed_at,
                 health_state, evidence_ref)
            VALUES (%s, %s, 'facebook', NOW(), 'healthy', %s)
            """,
            (workspace_a, connection_id, f"provider-check:{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO public.connector_certification_registry
                (workspace_id, connection_id, connector_key, connector_version,
                 registry_state)
            VALUES (%s, %s, 'facebook', '1.0.0', 'active')
            """,
            (workspace_a, connection_id),
        )

        invalid_inserts = (
            (
                """
                INSERT INTO public.integration_evidence
                    (evidence_id, workspace_id, connection_id, connector_key,
                     event_type, occurred_at, outcome, evidence_hash)
                VALUES (%s, %s, %s, 'facebook', 'health.checked', NOW(),
                        'healthy', %s)
                """,
                (f"bad-evidence-{suffix}", workspace_b, connection_id, "b" * 64),
            ),
            (
                """
                INSERT INTO public.connector_health_observations
                    (workspace_id, connection_id, connector_key, observed_at,
                     health_state, evidence_ref)
                VALUES (%s, %s, 'facebook', NOW(), 'healthy', %s)
                """,
                (workspace_b, connection_id, f"forged:{suffix}"),
            ),
            (
                """
                INSERT INTO public.connector_certification_registry
                    (workspace_id, connection_id, connector_key, connector_version,
                     registry_state)
                VALUES (%s, %s, 'facebook', '1.0.0', 'active')
                """,
                (workspace_b, connection_id),
            ),
        )

        for sql, params in invalid_inserts:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(sql, params)
