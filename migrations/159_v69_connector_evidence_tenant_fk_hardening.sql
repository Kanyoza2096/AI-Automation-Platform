-- U2/V69 connector evidence tenant-binding hardening.
-- The canonical integration_connections key is (workspace_id, id).
-- Existing evidence is left NOT VALID for historical audit, while all new
-- inserts are checked against the workspace-owned connection row.

CREATE TABLE IF NOT EXISTS public.integration_evidence (
    evidence_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    connector_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    action TEXT NOT NULL DEFAULT '',
    correlation_id TEXT,
    actor TEXT NOT NULL DEFAULT 'system',
    provider TEXT NOT NULL DEFAULT '',
    connector_version TEXT NOT NULL DEFAULT '',
    payload_fingerprint CHAR(64) NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_hash CHAR(64) NOT NULL DEFAULT '',
    evidence_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.integration_evidence
    DROP CONSTRAINT IF EXISTS integration_evidence_workspace_connection_fk;
ALTER TABLE public.integration_evidence
    ADD CONSTRAINT integration_evidence_workspace_connection_fk
    FOREIGN KEY (workspace_id, connection_id)
    REFERENCES public.integration_connections(workspace_id, id)
    ON DELETE RESTRICT
    NOT VALID;

CREATE INDEX IF NOT EXISTS idx_integration_evidence_workspace_time
    ON public.integration_evidence(workspace_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_evidence_connection_time
    ON public.integration_evidence(workspace_id, connection_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_integration_evidence_correlation
    ON public.integration_evidence(workspace_id, correlation_id)
    WHERE correlation_id IS NOT NULL;

ALTER TABLE public.integration_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS integration_evidence_member_select ON public.integration_evidence;
CREATE POLICY integration_evidence_member_select
    ON public.integration_evidence FOR SELECT
    USING (
        workspace_id IN (
            SELECT workspace_id
            FROM public.workspace_members
            WHERE user_id = auth.uid()::text
        )
    );
DROP POLICY IF EXISTS integration_evidence_no_client_write ON public.integration_evidence;
CREATE POLICY integration_evidence_no_client_write
    ON public.integration_evidence FOR INSERT
    WITH CHECK (false);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'connector_health_observations_workspace_connection_fk'
          AND conrelid = 'public.connector_health_observations'::regclass
    ) THEN
        ALTER TABLE public.connector_health_observations
            ADD CONSTRAINT connector_health_observations_workspace_connection_fk
            FOREIGN KEY (workspace_id, connection_id)
            REFERENCES public.integration_connections(workspace_id, id)
            ON DELETE RESTRICT
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'connector_certification_registry_workspace_connection_fk'
          AND conrelid = 'public.connector_certification_registry'::regclass
    ) THEN
        ALTER TABLE public.connector_certification_registry
            ADD CONSTRAINT connector_certification_registry_workspace_connection_fk
            FOREIGN KEY (workspace_id, connection_id)
            REFERENCES public.integration_connections(workspace_id, id)
            ON DELETE RESTRICT
            NOT VALID;
    END IF;
END
$$;
