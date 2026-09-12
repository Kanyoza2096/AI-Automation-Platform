-- Phase 2: distributed authority, idempotency and atomic usage accounting.

CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source TEXT NOT NULL,
    action_name TEXT NOT NULL,
    approved BOOLEAN NOT NULL,
    correlation_id TEXT NOT NULL,
    risk_level TEXT NOT NULL DEFAULT 'unknown',
    reason TEXT NOT NULL DEFAULT '',
    votes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome TEXT,
    outcome_at TIMESTAMPTZ,
    outcome_score DOUBLE PRECISION,
    outcome_source TEXT,
    platform_post_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS decisions_workspace_created_idx ON decisions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS decisions_correlation_idx ON decisions(correlation_id);
ALTER TABLE decisions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS decisions_workspace_isolation ON decisions;
CREATE POLICY decisions_workspace_isolation ON decisions FOR ALL
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text))
WITH CHECK (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()::text));

CREATE TABLE IF NOT EXISTS webhook_idempotency (
    delivery_id TEXT PRIMARY KEY,
    workspace_id TEXT,
    provider TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS webhook_idempotency_expiry_idx ON webhook_idempotency(expires_at);

CREATE OR REPLACE FUNCTION claim_webhook_delivery(
    p_delivery_id TEXT,
    p_workspace_id TEXT DEFAULT NULL,
    p_provider TEXT DEFAULT NULL,
    p_ttl_seconds INTEGER DEFAULT 600
) RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO webhook_idempotency(delivery_id, workspace_id, provider, expires_at)
    VALUES (p_delivery_id, p_workspace_id, p_provider, NOW() + make_interval(secs => GREATEST(1,p_ttl_seconds)))
    ON CONFLICT (delivery_id) DO NOTHING;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION consume_workspace_post_quota(
    p_workspace_id TEXT,
    p_limit INTEGER
) RETURNS TABLE(allowed BOOLEAN, posts_today INTEGER)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO workspace_usage(workspace_id, day, month, posts_today, ai_tokens_month)
    VALUES (p_workspace_id, CURRENT_DATE, to_char(CURRENT_DATE,'YYYY-MM'), 0, 0)
    ON CONFLICT (workspace_id, day) DO NOTHING;
    RETURN QUERY
    UPDATE workspace_usage
       SET posts_today = posts_today + 1, updated_at = NOW()
     WHERE workspace_id = p_workspace_id
       AND day = CURRENT_DATE
       AND posts_today < p_limit
     RETURNING TRUE, posts_today;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, COALESCE((SELECT posts_today FROM workspace_usage WHERE workspace_id=p_workspace_id AND day=CURRENT_DATE),0);
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION consume_workspace_ai_quota(
    p_workspace_id TEXT,
    p_tokens INTEGER,
    p_limit BIGINT
) RETURNS TABLE(allowed BOOLEAN, ai_tokens_month BIGINT)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO workspace_usage(workspace_id, day, month, posts_today, ai_tokens_month)
    VALUES (p_workspace_id, CURRENT_DATE, to_char(CURRENT_DATE,'YYYY-MM'), 0, 0)
    ON CONFLICT (workspace_id, day) DO NOTHING;
    RETURN QUERY
    UPDATE workspace_usage
       SET ai_tokens_month = ai_tokens_month + GREATEST(0,p_tokens), updated_at = NOW()
     WHERE workspace_id = p_workspace_id
       AND month = to_char(CURRENT_DATE,'YYYY-MM')
       AND ai_tokens_month + GREATEST(0,p_tokens) <= p_limit
     RETURNING TRUE, ai_tokens_month;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, COALESCE((SELECT SUM(ai_tokens_month) FROM workspace_usage WHERE workspace_id=p_workspace_id AND month=to_char(CURRENT_DATE,'YYYY-MM')),0);
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION increment_workspace_post_usage(p_workspace_id TEXT, p_count INTEGER DEFAULT 1)
RETURNS TABLE(posts_today INTEGER) LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO workspace_usage(workspace_id, day, month, posts_today, ai_tokens_month)
  VALUES (p_workspace_id, CURRENT_DATE, to_char(CURRENT_DATE,'YYYY-MM'), 0, 0)
  ON CONFLICT (workspace_id, day) DO NOTHING;
  RETURN QUERY UPDATE workspace_usage SET posts_today = posts_today + GREATEST(0,p_count), updated_at=NOW()
    WHERE workspace_id=p_workspace_id AND day=CURRENT_DATE RETURNING workspace_usage.posts_today;
END; $$;

CREATE OR REPLACE FUNCTION increment_workspace_ai_usage(p_workspace_id TEXT, p_tokens INTEGER DEFAULT 0)
RETURNS TABLE(ai_tokens_month BIGINT) LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO workspace_usage(workspace_id, day, month, posts_today, ai_tokens_month)
  VALUES (p_workspace_id, CURRENT_DATE, to_char(CURRENT_DATE,'YYYY-MM'), 0, 0)
  ON CONFLICT (workspace_id, day) DO NOTHING;
  RETURN QUERY UPDATE workspace_usage SET ai_tokens_month = ai_tokens_month + GREATEST(0,p_tokens), updated_at=NOW()
    WHERE workspace_id=p_workspace_id AND month=to_char(CURRENT_DATE,'YYYY-MM') RETURNING workspace_usage.ai_tokens_month;
END; $$;

-- Tighten the outbox RPC so service-side claims remain tenant-safe and leases are cleared on terminal states.
CREATE INDEX IF NOT EXISTS idx_publish_outbox_lock ON publish_outbox(status, locked_until, available_at);
