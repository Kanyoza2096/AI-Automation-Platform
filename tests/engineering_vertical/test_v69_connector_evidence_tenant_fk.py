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


@pytest.mark.skipif(not DSN, reason="disposable PostgreSQL migration test runs in CI")
def test_integration_evidence_rpc_is_atomic_and_service_role_only():
    from psycopg.types.json import Jsonb

    suffix = uuid.uuid4().hex
    workspace_id = f"it-evidence-rpc-{suffix}"
    connection_id = f"it-evidence-connection-{suffix}"
    evidence_id = f"it-evidence-{suffix}"

    append_sql = """
        SELECT evidence_id
        FROM public.append_integration_evidence(
            p_evidence_id => %s,
            p_workspace_id => %s,
            p_connection_id => %s,
            p_connector_key => 'facebook',
            p_event_type => 'health.checked',
            p_occurred_at => NOW(),
            p_outcome => 'healthy',
            p_reason_codes => %s,
            p_action => '',
            p_correlation_id => NULL,
            p_actor => 'system',
            p_provider => 'facebook',
            p_connector_version => '1.0.0',
            p_payload_fingerprint => %s,
            p_metadata => %s,
            p_expected_previous_hash => %s,
            p_evidence_hash => %s
        )
    """

    with psycopg.connect(DSN) as connection:
        connection.execute(
            "INSERT INTO public.workspaces (id) VALUES (%s)",
            (workspace_id,),
        )
        connection.execute(
            """
            INSERT INTO public.integration_connections
                (id, workspace_id, connector_key)
            VALUES (%s, %s, 'facebook')
            """,
            (connection_id, workspace_id),
        )

        first_params = (
            evidence_id, workspace_id, connection_id, Jsonb([]),
            "f" * 64, Jsonb({"latency_ms": 10}), "", "a" * 64,
        )
        with connection.transaction():
            connection.execute("SET LOCAL ROLE service_role")
            row = connection.execute(append_sql, first_params).fetchone()
            assert row[0] == evidence_id

        second_params = (
            f"{evidence_id}-2", workspace_id, connection_id, Jsonb([]),
            "e" * 64, Jsonb({"latency_ms": 11}), "a" * 64, "b" * 64,
        )
        with connection.transaction():
            connection.execute("SET LOCAL ROLE service_role")
            row = connection.execute(append_sql, second_params).fetchone()
            assert row[0] == f"{evidence_id}-2"

        # A stale expected head must fail with a serialization conflict.
        stale_params = (
            f"{evidence_id}-stale", workspace_id, connection_id, Jsonb([]),
            "d" * 64, Jsonb({}), "", "c" * 64,
        )
        with pytest.raises(psycopg.errors.SerializationFailure):
            with connection.transaction():
                connection.execute("SET LOCAL ROLE service_role")
                connection.execute(append_sql, stale_params)

        # Even service_role cannot bypass the append RPC with a direct INSERT.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute("SET LOCAL ROLE service_role")
                connection.execute(
                    """
                    INSERT INTO public.integration_evidence
                        (evidence_id, workspace_id, connection_id, connector_key,
                         event_type, occurred_at, outcome, evidence_hash)
                    VALUES (%s, %s, %s, 'facebook', 'health.checked', NOW(),
                            'healthy', %s)
                    """,
                    (f"{evidence_id}-direct", workspace_id, connection_id, "c" * 64),
                )
