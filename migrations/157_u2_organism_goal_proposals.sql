-- U2: durable, workspace-scoped goal proposals.
-- Proposals are suggestions only; this table does not store execution grants.
CREATE TABLE IF NOT EXISTS public.organism_goal_proposals (
    proposal_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    success_condition TEXT NOT NULL CHECK (length(trim(success_condition)) > 0),
    source_event_ids TEXT[] NOT NULL CHECK (
        cardinality(source_event_ids) BETWEEN 1 AND 8
    ),
    priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    expected_outcome TEXT NOT NULL CHECK (length(trim(expected_outcome)) > 0),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > created_at),
    authority TEXT NOT NULL DEFAULT 'suggest' CHECK (authority = 'suggest'),
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    CONSTRAINT organism_goal_proposals_workspace_proposal_unique
        UNIQUE (workspace_id, proposal_id)
);

CREATE INDEX IF NOT EXISTS organism_goal_proposals_pending_idx
    ON public.organism_goal_proposals (workspace_id, expires_at, created_at DESC)
    WHERE status = 'proposed';

ALTER TABLE public.organism_goal_proposals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS organism_goal_proposals_workspace_isolation
    ON public.organism_goal_proposals;
CREATE POLICY organism_goal_proposals_workspace_isolation
    ON public.organism_goal_proposals
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM public.workspace_members
            WHERE user_id = auth.uid()::text
        )
    );

REVOKE ALL PRIVILEGES ON TABLE public.organism_goal_proposals FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.organism_goal_proposals TO service_role;

COMMENT ON TABLE public.organism_goal_proposals IS
    'Evidence-backed organism goal suggestions; no activation, approval, or execution authority is persisted here.';
