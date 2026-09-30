-- Durable idempotency ledger for the Autonomous Loop.
-- One event_id may be claimed by only one worker.
CREATE TABLE IF NOT EXISTS public.autonomous_event_receipts (
    event_id       TEXT PRIMARY KEY,
    workspace_id   TEXT,
    event_type     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'processing'
                   CHECK (status IN ('processing','processed','failed')),
    error          TEXT,
    claimed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS autonomous_event_receipts_ws_idx
    ON public.autonomous_event_receipts (workspace_id, claimed_at DESC);
CREATE INDEX IF NOT EXISTS autonomous_event_receipts_status_idx
    ON public.autonomous_event_receipts (status, claimed_at DESC);
ALTER TABLE public.autonomous_event_receipts ENABLE ROW LEVEL SECURITY;
CREATE POLICY autonomous_event_receipts_workspace_isolation
    ON public.autonomous_event_receipts FOR ALL
    USING (
        workspace_id = COALESCE(current_setting('app.workspace_id', true), workspace_id)
    );
