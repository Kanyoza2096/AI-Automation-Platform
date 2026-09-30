-- Sprint 3: durable publish outbox (survives process death)

CREATE TABLE IF NOT EXISTS publish_outbox (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    brand_id        TEXT,
    platform        TEXT NOT NULL DEFAULT 'facebook',
    decision_id     TEXT NOT NULL,
    correlation_id  TEXT,
    caption         TEXT,
    topic           TEXT,
    risk_level      TEXT NOT NULL DEFAULT 'low',
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','processing','published','failed','dead')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    last_error      TEXT,
    platform_post_id TEXT,
    available_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_drain
    ON publish_outbox (status, available_at)
    WHERE status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS idx_outbox_workspace
    ON publish_outbox (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_outbox_decision
    ON publish_outbox (decision_id);

ALTER TABLE publish_outbox ENABLE ROW LEVEL SECURITY;

-- Tenant isolation: only service role bypasses; app must filter workspace_id
CREATE POLICY publish_outbox_workspace_isolation ON publish_outbox
    FOR ALL
    USING (
        workspace_id = COALESCE(current_setting('app.workspace_id', true), workspace_id)
    );
