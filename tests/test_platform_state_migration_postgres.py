"""Disposable-PostgreSQL contract tests for migration 166.

The focused CI workflow applies migration 166 to its own ephemeral PostgreSQL
service before this module runs. These tests never connect to staging or prod.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest


@pytest.fixture(scope="module")
def connection():
    dsn = os.getenv("KDO_ENGINEERING_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("disposable PostgreSQL DSN is not configured")
    with psycopg.connect(dsn, autocommit=True) as conn:
        yield conn


def test_platform_state_table_is_rls_protected_and_rpc_is_service_role_only(connection):
    row = connection.execute(
        """
        SELECT c.relrowsecurity,
               has_table_privilege('service_role', c.oid, 'SELECT'),
               has_table_privilege('service_role', c.oid, 'INSERT'),
               has_table_privilege('authenticated', c.oid, 'SELECT'),
               has_table_privilege('anon', c.oid, 'SELECT'),
               has_function_privilege(
                   'service_role',
                   'public.write_platform_state_snapshot(text,text,bigint,jsonb)',
                   'EXECUTE'
               ),
               has_function_privilege(
                   'authenticated',
                   'public.write_platform_state_snapshot(text,text,bigint,jsonb)',
                   'EXECUTE'
               ),
               has_function_privilege(
                   'anon',
                   'public.write_platform_state_snapshot(text,text,bigint,jsonb)',
                   'EXECUTE'
               )
          FROM pg_class c
         WHERE c.oid = 'public.platform_state_snapshots'::regclass
        """
    ).fetchone()

    assert row == (True, True, False, False, False, True, False, False)


def test_platform_state_rpc_enforces_atomic_revision_cas_and_no_resurrection(connection):
    scope_id = "platform-state-ci-" + uuid.uuid4().hex
    connection.execute(
        "INSERT INTO public.workspaces (id, status) VALUES (%s, 'active')",
        (scope_id,),
    )
    try:
        state = {"schema_version": 1, "services": [], "capabilities": []}
        first = connection.execute(
            """
            SELECT public.write_platform_state_snapshot(
                'workspace', %s, 0, %s::jsonb
            )
            """,
            (scope_id, psycopg.types.json.Jsonb(state)),
        ).fetchone()[0]
        assert first == 1

        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                """
                SELECT public.write_platform_state_snapshot(
                    'workspace', %s, 0, %s::jsonb
                )
                """,
                (scope_id, psycopg.types.json.Jsonb(state)),
            )

        second_state = {**state, "generation": 2}
        second = connection.execute(
            """
            SELECT public.write_platform_state_snapshot(
                'workspace', %s, 1, %s::jsonb
            )
            """,
            (scope_id, psycopg.types.json.Jsonb(second_state)),
        ).fetchone()[0]
        assert second == 2

        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                """
                SELECT public.write_platform_state_snapshot(
                    'workspace', %s, 1, %s::jsonb
                )
                """,
                (scope_id, psycopg.types.json.Jsonb(state)),
            )

        connection.execute(
            "DELETE FROM public.platform_state_snapshots WHERE scope_type='workspace' AND scope_id=%s",
            (scope_id,),
        )
        with pytest.raises(psycopg.errors.SerializationFailure):
            connection.execute(
                """
                SELECT public.write_platform_state_snapshot(
                    'workspace', %s, 2, %s::jsonb
                )
                """,
                (scope_id, psycopg.types.json.Jsonb(state)),
            )

        assert connection.execute(
            "SELECT 1 FROM public.platform_state_snapshots WHERE scope_type='workspace' AND scope_id=%s",
            (scope_id,),
        ).fetchone() is None
    finally:
        connection.execute(
            "DELETE FROM public.platform_state_snapshots WHERE scope_type='workspace' AND scope_id=%s",
            (scope_id,),
        )
        connection.execute("DELETE FROM public.workspaces WHERE id=%s", (scope_id,))
