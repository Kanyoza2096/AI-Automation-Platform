from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.mark.skipif(
    not os.getenv("KDO_ENGINEERING_TEST_POSTGRES_DSN"),
    reason="requires the disposable PostgreSQL database configured by focused CI",
)
def test_u1_delivery_rpc_fences_claims_retries_and_dead_letters():
    dsn = os.environ["KDO_ENGINEERING_TEST_POSTGRES_DSN"]
    event_id = f"u1-migration-test-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as connection:
        try:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO public.events (id, event_type) VALUES (%s, %s)",
                    (event_id, "u1.migration.test"),
                )
                delivery_id = connection.execute(
                    """
                    INSERT INTO public.event_deliveries
                        (event_id, consumer_group, event_type)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (event_id, "u1-migration-test", "u1.migration.test"),
                ).fetchone()[0]

                connection.execute(
                    """
                    UPDATE public.event_deliveries
                    SET claimed_by = %s, claimed_at = NOW()
                    WHERE id = %s
                    """,
                    ("worker:current-claim", delivery_id),
                )
                stale_result = connection.execute(
                    """
                    SELECT public.complete_shared_event_delivery(
                        %s, %s, %s, FALSE, %s, 5
                    )
                    """,
                    (
                        delivery_id,
                        "u1-migration-test",
                        "worker:stale-claim",
                        "stale worker must not acknowledge",
                    ),
                ).fetchone()[0]
                assert stale_result is False
                assert connection.execute(
                    "SELECT claimed_by FROM public.event_deliveries WHERE id = %s",
                    (delivery_id,),
                ).fetchone()[0] == "worker:current-claim"

                for attempt in range(1, 6):
                    claim_token = f"worker:attempt-{attempt}"
                    connection.execute(
                        """
                        UPDATE public.event_deliveries
                        SET claimed_by = %s, claimed_at = NOW()
                        WHERE id = %s
                        """,
                        (claim_token, delivery_id),
                    )
                    completed = connection.execute(
                        """
                        SELECT public.complete_shared_event_delivery(
                            %s, %s, %s, FALSE, %s, 5
                        )
                        """,
                        (
                            delivery_id,
                            "u1-migration-test",
                            claim_token,
                            f"failure {attempt}",
                        ),
                    ).fetchone()[0]
                    assert completed is True

                    row = connection.execute(
                        """
                        SELECT attempt_count, next_attempt_at, dead_lettered_at,
                               claimed_by, processing_error
                        FROM public.event_deliveries WHERE id = %s
                        """,
                        (delivery_id,),
                    ).fetchone()
                    assert row[0] == attempt
                    assert row[3] is None
                    assert row[4] == f"failure {attempt}"
                    if attempt < 5:
                        assert row[1] is not None
                        assert row[2] is None
                    else:
                        assert row[2] is not None

                success_event_id = f"{event_id}-success"
                connection.execute(
                    "INSERT INTO public.events (id, event_type) VALUES (%s, %s)",
                    (success_event_id, "u1.migration.success"),
                )
                success_delivery_id = connection.execute(
                    """
                    INSERT INTO public.event_deliveries
                        (event_id, consumer_group, event_type, claimed_by, claimed_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (
                        success_event_id,
                        "u1-migration-test",
                        "u1.migration.success",
                        "worker:success",
                    ),
                ).fetchone()[0]
                success_result = connection.execute(
                    """
                    SELECT public.complete_shared_event_delivery(
                        %s, %s, %s, TRUE, NULL, 5
                    )
                    """,
                    (
                        success_delivery_id,
                        "u1-migration-test",
                        "worker:success",
                    ),
                ).fetchone()[0]
                assert success_result is True
                success_row = connection.execute(
                    """
                    SELECT processed, processed_at, processing_error, claimed_by
                    FROM public.event_deliveries WHERE id = %s
                    """,
                    (success_delivery_id,),
                ).fetchone()
                assert success_row[0] is True
                assert success_row[1] is not None
                assert success_row[2:] == (None, None)

                metrics = connection.execute(
                    "SELECT * FROM public.get_shared_event_delivery_metrics(%s)",
                    ("u1-migration-test",),
                ).fetchone()
                assert metrics[0] == "u1-migration-test"
                assert metrics[1] == 2  # total deliveries
                assert metrics[2] == 0  # active pending deliveries
                assert metrics[6] == 1  # completed deliveries
                assert metrics[7] == 1  # dead-lettered deliveries
        finally:
            connection.rollback()
