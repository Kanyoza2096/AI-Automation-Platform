-- Phase D — Metabolism / usage metering

CREATE TABLE IF NOT EXISTS workspace_usage (
    workspace_id TEXT NOT NULL,
    day          DATE NOT NULL,
    month        TEXT NOT NULL,
    posts_today  INTEGER NOT NULL DEFAULT 0,
    ai_tokens_month BIGINT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, day)
);

CREATE INDEX IF NOT EXISTS idx_workspace_usage_month
    ON workspace_usage (workspace_id, month);

-- Optional billing columns on workspaces
ALTER TABLE IF EXISTS workspaces
    ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free',
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS plan_updated_at TIMESTAMPTZ;

-- RLS
ALTER TABLE IF EXISTS workspace_usage ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  DROP POLICY IF EXISTS workspace_usage_isolation ON workspace_usage;
EXCEPTION WHEN undefined_table THEN NULL;
END $$;

CREATE POLICY workspace_usage_isolation ON workspace_usage
    FOR ALL
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    )
    WITH CHECK (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()::text
        )
    );
