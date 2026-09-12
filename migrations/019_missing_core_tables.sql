-- 019_missing_core_tables.sql
-- Authoritative CREATE for tables referenced by app code but missing from earlier migrations.
-- Safe to re-run (IF NOT EXISTS). Run in Supabase SQL editor or migration pipeline.

-- ── Events / shared bus ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS events_ws_created_idx ON events (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS events_type_idx ON events (event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS event_store (
    id              TEXT PRIMARY KEY,
    stream          TEXT NOT NULL DEFAULT 'default',
    event_type      TEXT NOT NULL,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS event_store_stream_idx ON event_store (stream, created_at DESC);

CREATE TABLE IF NOT EXISTS shared_event_bus (
    id              TEXT PRIMARY KEY,
    event_name      TEXT NOT NULL,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id              TEXT PRIMARY KEY,
    source          TEXT,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Posts / history / engagement ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_history (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    brand_id        TEXT,
    platform        TEXT,
    platform_post_id TEXT,
    caption         TEXT,
    topic           TEXT,
    status          TEXT,
    decision_id     TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS post_history_ws_pub_idx ON post_history (workspace_id, published_at DESC);

CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    brand_id        TEXT,
    content         TEXT,
    status          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS post_engagement (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    post_id         TEXT,
    platform        TEXT,
    likes           INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    shares          INTEGER DEFAULT 0,
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    measured_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    brand_id        TEXT,
    caption         TEXT,
    scheduled_for   TIMESTAMPTZ,
    status          TEXT DEFAULT 'scheduled',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    id              TEXT PRIMARY KEY DEFAULT 'global',
    workspace_id    TEXT,
    state           JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Rate limit config (code uses rate_limit_config; 012 created rate_limits) ─
CREATE TABLE IF NOT EXISTS rate_limit_config (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    action          TEXT NOT NULL,
    max_per_minute  INTEGER NOT NULL DEFAULT 30,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS rate_limit_config_ws_action
  ON rate_limit_config (COALESCE(workspace_id, ''), action);

-- ── DFMO / monitor ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS monitor_targets (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    name            TEXT NOT NULL,
    target_type     TEXT,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monitor_checks (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    target_id       TEXT,
    status          TEXT,
    source          TEXT,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS monitor_checks_checked_idx ON monitor_checks (checked_at DESC);
CREATE INDEX IF NOT EXISTS monitor_checks_source_checked_idx ON monitor_checks (source, checked_at);

-- ── Trend (full create; ALTER-only migrations assumed already applied) ─
CREATE TABLE IF NOT EXISTS trend_sources (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    name            TEXT,
    source_type     TEXT,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trend_posts (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    url             TEXT,
    title           TEXT,
    summary         TEXT,
    score           DOUBLE PRECISION,
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_url_unique ON trend_posts (url);
CREATE UNIQUE INDEX IF NOT EXISTS trend_posts_workspace_url_unique
  ON trend_posts (workspace_id, url) WHERE workspace_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS trend_brand_rules (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    brand_id        TEXT,
    rules           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trend_decisions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    brand_id        TEXT,
    trend_post_id   TEXT,
    decision        TEXT,
    relevance       DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS trend_decisions_post_brand_unique
  ON trend_decisions (COALESCE(trend_post_id,''), COALESCE(brand_id,''));

CREATE TABLE IF NOT EXISTS trend_publish_jobs (
    job_id          TEXT PRIMARY KEY,
    workspace_id    TEXT,
    brand_id        TEXT,
    trend_id        TEXT,
    topic           TEXT,
    content         TEXT,
    url             TEXT,
    platform        TEXT,
    decision        TEXT,
    relevance       DOUBLE PRECISION,
    correlation_id  TEXT,
    media_url       TEXT,
    source          TEXT,
    briefed         BOOLEAN DEFAULT FALSE,
    status          TEXT,
    requested_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trend_page_posts (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    page_id         TEXT,
    post_id         TEXT,
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Director persistence ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS director_tasks (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    status          TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS director_observations (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    observation     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS director_decisions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    decision        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS director_approvals (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    status          TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS director_learnings (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    learning        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meta_engine_snapshots (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT DEFAULT 'platform',
    snapshot        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS autonomous_decisions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS autonomous_decision_memory (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Council ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS council_decisions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS council_votes (
    id              TEXT PRIMARY KEY,
    decision_id     TEXT,
    persona         TEXT,
    vote            TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS council_outcomes (
    id              TEXT PRIMARY KEY,
    decision_id     TEXT,
    outcome         TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Backup / conversations / misc ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS backup_targets (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    name            TEXT,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS backup_records (
    id              TEXT PRIMARY KEY,
    target_id       TEXT,
    workspace_id    TEXT,
    status          TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    user_id         TEXT,
    role            TEXT,
    content         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_templates (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    name            TEXT,
    body            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    plan            TEXT,
    status          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenants (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scan_history (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    source          TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS engaged_posts (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT,
    post_id         TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Growthbridge (minimal)
CREATE TABLE IF NOT EXISTS growthbridge_services (
    id TEXT PRIMARY KEY, name TEXT, payload JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_projects (
    id TEXT PRIMARY KEY, name TEXT, payload JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_talent (
    id TEXT PRIMARY KEY, name TEXT, payload JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_applications (
    id TEXT PRIMARY KEY, payload JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_partners (
    id TEXT PRIMARY KEY, name TEXT, payload JSONB DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_blog (
    id TEXT PRIMARY KEY, title TEXT, body TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_testimonials (
    id TEXT PRIMARY KEY, body TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS growthbridge_admin_users (
    id TEXT PRIMARY KEY, email TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS enable on critical tenant tables (service_role bypasses)
ALTER TABLE IF EXISTS post_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS events ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scheduled_posts ENABLE ROW LEVEL SECURITY;
