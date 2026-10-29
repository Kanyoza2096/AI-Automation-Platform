-- Phase config: DB-backed plans, workspace organs, brand content config

-- 1. Plans (replace hardcoded PLAN_CATALOG when rows exist)
CREATE TABLE IF NOT EXISTS plans (
    name                    TEXT PRIMARY KEY,
    max_posts_per_day       INTEGER NOT NULL DEFAULT 5,
    max_ai_tokens_per_month BIGINT NOT NULL DEFAULT 50000,
    max_brands              INTEGER NOT NULL DEFAULT 1,
    max_social_accounts     INTEGER NOT NULL DEFAULT 2,
    director_authority      TEXT NOT NULL DEFAULT 'suggest',
    industry_organs         JSONB NOT NULL DEFAULT '["default"]'::jsonb,
    council_tier_3_personas INTEGER NOT NULL DEFAULT 5,
    price_usd_monthly       INTEGER NOT NULL DEFAULT 0,
    enabled                 BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO plans (name, max_posts_per_day, max_ai_tokens_per_month, max_brands, max_social_accounts,
                   director_authority, industry_organs, council_tier_3_personas, price_usd_monthly)
VALUES
  ('free', 5, 50000, 1, 2, 'suggest', '["default"]', 5, 0),
  ('pro', 20, 500000, 5, 10, 'auto_low', '["default","crm","church_mis"]', 8, 49),
  ('business', 100, 2000000, 25, 50, 'auto_medium', '["default","crm","church_mis","hospital","school_mis"]', 12, 149),
  ('enterprise', 1000, 20000000, 100, 200, 'auto_medium', '["default","crm","church_mis","hospital","school_mis"]', 12, 0)
ON CONFLICT (name) DO NOTHING;

-- 2. Per-workspace organism config
CREATE TABLE IF NOT EXISTS workspace_config (
    workspace_id            TEXT PRIMARY KEY,
    plan                    TEXT NOT NULL DEFAULT 'free' REFERENCES plans(name),
    timezone                TEXT NOT NULL DEFAULT 'UTC',
    dream_hours_start       INTEGER NOT NULL DEFAULT 1,
    dream_hours_end         INTEGER NOT NULL DEFAULT 5,
    enabled_personas        JSONB,  -- null = use defaults for plan/industry
    industry_organs         JSONB NOT NULL DEFAULT '["default"]'::jsonb,
    director_authority      TEXT,   -- null = inherit from plan
    enabled_capabilities    JSONB,  -- null = all non-critical director caps
    pulse_posts_hours       FLOAT NOT NULL DEFAULT 24,
    pulse_director_hours    FLOAT NOT NULL DEFAULT 12,
    pulse_engagement_hours  FLOAT NOT NULL DEFAULT 48,
    pulse_backup_hours      FLOAT NOT NULL DEFAULT 48,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Persona catalog (species-level)
CREATE TABLE IF NOT EXISTS persona_catalog (
    name            TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    description     TEXT,
    default_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    industries      JSONB NOT NULL DEFAULT '["default"]'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO persona_catalog (name, display_name, description, industries) VALUES
  ('youth_advocate', 'Youth Advocate', 'Young audience voice', '["default","school_mis"]'),
  ('business_owner', 'Business Owner', 'Revenue and margins', '["default","crm"]'),
  ('community_elder', 'Community Elder', 'Tradition and trust', '["default","church_mis"]'),
  ('data_analyst', 'Data Analyst', 'Evidence and samples', '["default","hospital"]'),
  ('risk_manager', 'Risk Manager', 'Downside protection', '["default","hospital"]'),
  ('trend_spotter', 'Trend Spotter', 'What is rising', '["default"]'),
  ('customer_voice', 'Customer Voice', 'User empathy', '["default","crm"]'),
  ('gut_feeling', 'Gut Feeling', 'Intuition', '["default"]'),
  ('mood_ring', 'Mood Ring', 'Emotional climate', '["default"]'),
  ('rippler', 'Rippler', 'Virality and momentum', '["default"]'),
  ('affiliate_marketer', 'Affiliate Marketer', 'Commission and conversion', '["crm"]'),
  ('theologian', 'Theologian', 'Spiritual integrity', '["church_mis"]')
ON CONFLICT (name) DO NOTHING;

-- 4. Per-brand content config (keywords + fallback topics)
CREATE TABLE IF NOT EXISTS brand_config (
    brand_id            TEXT PRIMARY KEY,
    workspace_id        TEXT NOT NULL,
    keywords            JSONB NOT NULL DEFAULT '[]'::jsonb,
    fallback_topics     JSONB NOT NULL DEFAULT '[]'::jsonb,
    content_categories  JSONB NOT NULL DEFAULT '[]'::jsonb,
    tone                TEXT,
    language            TEXT DEFAULT 'en',
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_config_workspace ON brand_config(workspace_id);

ALTER TABLE plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_config ENABLE ROW LEVEL SECURITY;
-- persona_catalog is species-readable; policies optional
