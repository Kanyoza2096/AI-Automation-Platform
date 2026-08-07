-- Migration 009: Automation Jobs Table
-- Architecture §7 — Persistent Job Lifecycle
-- Architecture §8 — Database-Level Idempotency
--
-- Replaces in-memory scheduler state (_fired_slots, _published_keys)
-- with persistent, multi-worker-safe job tracking.
--
-- Idempotency contract: UNIQUE(brand_id, scheduled_for, workflow_type)
-- ensures ONE JOB → ONE PUBLICATION across all workers and restarts.

-- ═══════════════════════════════════════════════════════════════════════════
-- Enum types (created as CHECK constraints for portability)
-- ═══════════════════════════════════════════════════════════════════════════

-- Note: PostgreSQL native enums are preferred but CHECK constraints
-- are more portable across Supabase managed instances and local dev.


-- ═══════════════════════════════════════════════════════════════════════════
-- Main table
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS automation_jobs (
    -- ── Identity ──────────────────────────────────────────────────────────
    id              TEXT PRIMARY KEY DEFAULT ('job_' || encode(gen_random_bytes(6), 'hex')),
    
    -- ── Correlation ───────────────────────────────────────────────────────
    job_id          TEXT NOT NULL,                          -- Human-readable: pub_YYYYMMDD_xxxxxxxx
    idempotency_key TEXT NOT NULL,                          -- Unique per job: {brand_id}-{date}-{hour}-{type}
    
    -- ── Tenant Context ────────────────────────────────────────────────────
    workspace_id    TEXT NOT NULL DEFAULT '',
    brand_id        TEXT NOT NULL,
    
    -- ── Execution Context ─────────────────────────────────────────────────
    workflow_type   TEXT NOT NULL DEFAULT 'content_publish'
                    CHECK (workflow_type IN ('content_publish', 'news_publish', 'manual_publish')),
    
    platform        TEXT NOT NULL DEFAULT 'facebook',
    
    social_account_id TEXT,                                 -- Resolved at publish time
    
    -- ── Scheduling ────────────────────────────────────────────────────────
    source          TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (source IN ('scheduled', 'manual')),
    
    scheduled_for   TIMESTAMPTZ,                            -- When this job was scheduled to run
    
    -- ── Lifecycle ─────────────────────────────────────────────────────────
    status          TEXT NOT NULL DEFAULT 'CREATED'
                    CHECK (status IN (
                        'CREATED', 'QUEUED', 'RUNNING',
                        'GENERATING', 'VALIDATING', 'RENDERING',
                        'READY', 'PUBLISHING', 'PUBLISHED',
                        'FAILED', 'CANCELLED',
                        'RETRY_PENDING', 'STALE'
                    )),
    
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    
    last_error      TEXT,                                   -- Human-readable error message
    last_error_code TEXT,                                   -- Machine-readable error code
    
    -- ── Result ────────────────────────────────────────────────────────────
    topic           TEXT,                                   -- Content topic that was generated
    platform_post_id TEXT,                                  -- External platform post ID
    result          JSONB,                                  -- Full structured PublishingResult
    
    -- ── Timestamps ────────────────────────────────────────────────────────
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- ── Duration ──────────────────────────────────────────────────────────
    duration_ms     INTEGER                                 -- Total execution duration
);

-- ═══════════════════════════════════════════════════════════════════════════
-- Constraints
-- ═══════════════════════════════════════════════════════════════════════════

-- Idempotency: ONE JOB → ONE PUBLICATION (Architecture §8)
-- Two workers attempting the same scheduled slot will hit this constraint.
-- The second worker must handle the unique violation gracefully.
CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_jobs_idempotency
    ON automation_jobs (idempotency_key);

-- Prevent duplicate manual triggers within the same second for the same brand
-- (idempotency_key for manual includes microsecond timestamp, but this
-- provides an additional safety net)
CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_jobs_manual_dedup
    ON automation_jobs (brand_id, workflow_type, created_at)
    WHERE workflow_type = 'manual_publish';

-- ═══════════════════════════════════════════════════════════════════════════
-- Indexes for common queries
-- ═══════════════════════════════════════════════════════════════════════════

-- Look up all jobs for a brand
CREATE INDEX IF NOT EXISTS idx_automation_jobs_brand_id
    ON automation_jobs (brand_id);

-- Look up all jobs for a workspace
CREATE INDEX IF NOT EXISTS idx_automation_jobs_workspace_id
    ON automation_jobs (workspace_id);

-- Find active jobs (for stale detection)
CREATE INDEX IF NOT EXISTS idx_automation_jobs_status
    ON automation_jobs (status)
    WHERE status IN ('RUNNING', 'GENERATING', 'RENDERING', 'PUBLISHING');

-- Find jobs by scheduled time (for scheduler recovery)
CREATE INDEX IF NOT EXISTS idx_automation_jobs_scheduled_for
    ON automation_jobs (scheduled_for);

-- Find recent jobs by platform (for health checks)
CREATE INDEX IF NOT EXISTS idx_automation_jobs_platform_status
    ON automation_jobs (platform, status);

-- Find jobs needing retry
CREATE INDEX IF NOT EXISTS idx_automation_jobs_retry_pending
    ON automation_jobs (status, updated_at)
    WHERE status = 'RETRY_PENDING';

-- ═══════════════════════════════════════════════════════════════════════════
-- Stale job detection function
-- Architecture §76 — Worker Recovery
-- ═══════════════════════════════════════════════════════════════════════════

-- Jobs stuck in an active state for more than 15 minutes are considered stale.
-- This function marks them and returns the affected rows for processing.
CREATE OR REPLACE FUNCTION detect_stale_jobs(stale_threshold_minutes INTEGER DEFAULT 15)
RETURNS SETOF automation_jobs AS $$
BEGIN
    RETURN QUERY
    UPDATE automation_jobs
    SET status = 'STALE',
        last_error = 'Job timed out after ' || stale_threshold_minutes || ' minutes in state ' || status,
        last_error_code = 'STALE_JOB',
        updated_at = NOW()
    WHERE status IN ('RUNNING', 'GENERATING', 'RENDERING', 'PUBLISHING')
      AND updated_at < NOW() - (stale_threshold_minutes || ' minutes')::INTERVAL
    RETURNING *;
END;
$$ LANGUAGE plpgsql;

-- ═══════════════════════════════════════════════════════════════════════════
-- Updated_at trigger
-- ═══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION update_automation_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    
    -- Auto-set started_at when first entering an active state
    IF NEW.status IN ('RUNNING', 'GENERATING', 'VALIDATING', 'RENDERING', 'PUBLISHING')
       AND OLD.started_at IS NULL THEN
        NEW.started_at = NOW();
    END IF;
    
    -- Auto-set completed_at and calculate duration on terminal state
    IF NEW.status IN ('PUBLISHED', 'FAILED', 'CANCELLED')
       AND OLD.completed_at IS NULL THEN
        NEW.completed_at = NOW();
        IF NEW.started_at IS NOT NULL THEN
            NEW.duration_ms = EXTRACT(MILLISECONDS FROM (NEW.completed_at - NEW.started_at))::INTEGER;
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_automation_jobs_updated_at ON automation_jobs;
CREATE TRIGGER trg_automation_jobs_updated_at
    BEFORE UPDATE ON automation_jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_automation_jobs_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════
-- Row-Level Security
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE automation_jobs ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (backend operations)
-- This is automatically handled by Supabase service_role client.

-- Authenticated users can view jobs in their workspace
-- (Adjust workspace_id check based on your actual auth model)
CREATE POLICY "Users can view workspace jobs"
    ON automation_jobs FOR SELECT
    USING (
        workspace_id IN (
            SELECT workspace_id FROM workspace_members
            WHERE user_id = auth.uid()
        )
    );

-- Only backend (service_role) can insert/update jobs
-- This is enforced by application-level authorization.

-- ═══════════════════════════════════════════════════════════════════════════
-- Comments
-- ═══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE automation_jobs IS
'Persistent automation job tracking — Architecture §7-8.
Replaces in-memory scheduler state with multi-worker-safe persistence.
Idempotency enforced via UNIQUE(idempotency_key).';

COMMENT ON COLUMN automation_jobs.idempotency_key IS
'Format: {brand_id}-{date}-{hour}-{type} for scheduled, {brand_id}-manual-{timestamp} for manual.
UNIQUE constraint prevents duplicate execution across workers and restarts.';

COMMENT ON COLUMN automation_jobs.status IS
'Job lifecycle: CREATED → QUEUED → RUNNING → GENERATING → VALIDATING →
RENDERING → READY → PUBLISHING → PUBLISHED.
Failure: → FAILED (terminal) or → RETRY_PENDING (retryable).
Stuck: → STALE (requires investigation).';
